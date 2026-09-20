"""Lambda handler: parse the single-line file that just landed in S3.

Triggered by S3 ``ObjectCreated:*`` notifications. For every record it:

1. fetches the object (size-capped),
2. verifies it holds exactly one line of text,
3. parses that line (JSON object/array, or delimited values such as CSV),
4. logs the parsed result as structured JSON (CloudWatch Logs).

Error handling follows one rule:

* *Bad input* (empty file, multiple lines, too big, not UTF-8) is a permanent
  failure. It is logged and skipped, because retrying can never fix it.
* *Infrastructure* errors (S3 throttling, network, permissions) are re-raised
  so Lambda's async retry policy and the dead-letter queue take over.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
from typing import Any
from urllib.parse import unquote_plus

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# A "single line" file should be tiny; refuse anything unreasonable.
MAX_BYTES = int(os.environ.get("MAX_OBJECT_BYTES", str(1024 * 1024)))  # 1 MiB
DELIMITERS = (",", "|", "\t", ";")

_s3 = None


class InvalidFileError(ValueError):
    """The object's content is not a valid single-line file (do not retry)."""


def _get_s3():
    """Create the S3 client lazily so the module imports without AWS config."""
    global _s3
    if _s3 is None:
        import boto3

        _s3 = boto3.client("s3")
    return _s3


# --------------------------------------------------------------------------- #
# Parsing (pure functions, no AWS calls)
# --------------------------------------------------------------------------- #
def extract_single_line(raw: bytes) -> str:
    """Decode ``raw`` and return its only line, without the line terminator."""
    try:
        text = raw.decode("utf-8-sig")  # tolerate a leading BOM
    except UnicodeDecodeError as exc:
        raise InvalidFileError(f"file is not valid UTF-8: {exc}") from exc

    lines = text.splitlines()
    # A trailing blank line (e.g. file ends with "\n\n") is harmless.
    while lines and not lines[-1].strip():
        lines.pop()

    if not lines:
        raise InvalidFileError("file is empty")
    if len(lines) > 1:
        raise InvalidFileError(f"expected a single line, found {len(lines)}")
    return lines[0].strip()


def parse_line(line: str) -> dict[str, Any]:
    """Parse one line into ``{"format": ..., "data": ...}``.

    * ``{...}`` / ``[...]``      -> JSON
    * anything else with a
      , | ; or tab               -> delimited values (first delimiter found)
    * otherwise                  -> plain text value
    """
    if not line:
        raise InvalidFileError("line is blank")

    if line[0] in "{[":
        try:
            return {"format": "json", "data": json.loads(line)}
        except json.JSONDecodeError as exc:
            raise InvalidFileError(f"line looks like JSON but is invalid: {exc}") from exc

    for delimiter in DELIMITERS:
        if delimiter in line:
            fields = next(csv.reader(io.StringIO(line), delimiter=delimiter))
            return {
                "format": "delimited",
                "delimiter": delimiter,
                "data": [f.strip() for f in fields],
            }

    return {"format": "text", "data": line}


# --------------------------------------------------------------------------- #
# S3 access
# --------------------------------------------------------------------------- #
def read_object(bucket: str, key: str) -> bytes:
    """Fetch an object, refusing anything larger than ``MAX_BYTES``."""
    response = _get_s3().get_object(Bucket=bucket, Key=key)
    declared = response.get("ContentLength", 0)
    if declared > MAX_BYTES:
        raise InvalidFileError(f"object is {declared} bytes; limit is {MAX_BYTES}")
    # Read one byte past the limit so a wrong ContentLength can't fool us.
    body = response["Body"].read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise InvalidFileError(f"object exceeds {MAX_BYTES} bytes")
    return body


def process_record(record: dict[str, Any]) -> dict[str, Any]:
    """Process one S3 event record and return a result summary."""
    bucket = record["s3"]["bucket"]["name"]
    # Keys arrive URL-encoded ("my file.txt" -> "my+file.txt").
    key = unquote_plus(record["s3"]["object"]["key"])

    raw = read_object(bucket, key)
    parsed = parse_line(extract_single_line(raw))
    return {"bucket": bucket, "key": key, "bytes": len(raw), **parsed}


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []

    for record in event.get("Records", []):
        # Ignore anything that is not an S3 object-created notification.
        if not record.get("eventName", "").startswith("ObjectCreated:"):
            logger.info("Skipping event %s", record.get("eventName"))
            continue

        try:
            result = process_record(record)
        except InvalidFileError as exc:
            # Permanent failure: log and move on; a retry would fail identically.
            key = unquote_plus(record["s3"]["object"]["key"])
            logger.error(json.dumps({"status": "rejected", "key": key, "reason": str(exc)}))
            rejected.append({"key": key, "reason": str(exc)})
            continue

        logger.info(json.dumps({"status": "parsed", **result}))
        results.append(result)

    return {"processed": len(results), "rejected": len(rejected), "results": results}
