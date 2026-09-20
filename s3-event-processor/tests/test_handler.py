"""Handler tests. Stdlib only (unittest), so they run with `python -m unittest`
or `pytest` without needing boto3 or AWS credentials."""

import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda_src"))

import handler  # noqa: E402


class FakeS3:
    def __init__(self, objects):
        self.objects = objects  # {(bucket, key): bytes}
        self.requested = []

    def get_object(self, Bucket, Key):
        self.requested.append((Bucket, Key))
        if (Bucket, Key) not in self.objects:
            raise RuntimeError("NoSuchKey")  # stands in for a transient/infra error
        data = self.objects[(Bucket, Key)]
        return {"ContentLength": len(data), "Body": io.BytesIO(data)}


def s3_event(key, bucket="b", event_name="ObjectCreated:Put"):
    return {
        "Records": [
            {"eventName": event_name, "s3": {"bucket": {"name": bucket}, "object": {"key": key}}}
        ]
    }


class ParseLineTests(unittest.TestCase):
    def test_json_object(self):
        out = handler.parse_line('{"id": 7, "name": "a"}')
        self.assertEqual(out, {"format": "json", "data": {"id": 7, "name": "a"}})

    def test_json_array(self):
        self.assertEqual(handler.parse_line("[1,2,3]")["data"], [1, 2, 3])

    def test_invalid_json_rejected(self):
        with self.assertRaises(handler.InvalidFileError):
            handler.parse_line('{"id": ')

    def test_csv(self):
        out = handler.parse_line("1, Alice ,NYC")
        self.assertEqual(out["format"], "delimited")
        self.assertEqual(out["data"], ["1", "Alice", "NYC"])

    def test_csv_quoted_comma(self):
        self.assertEqual(handler.parse_line('1,"Doe, Jane",x')["data"], ["1", "Doe, Jane", "x"])

    def test_pipe_and_tab(self):
        self.assertEqual(handler.parse_line("a|b|c")["delimiter"], "|")
        self.assertEqual(handler.parse_line("a\tb")["delimiter"], "\t")

    def test_plain_text(self):
        self.assertEqual(handler.parse_line("hello"), {"format": "text", "data": "hello"})


class ExtractSingleLineTests(unittest.TestCase):
    def test_trailing_newline_ok(self):
        self.assertEqual(handler.extract_single_line(b"abc\n"), "abc")

    def test_crlf_and_trailing_blank_lines_ok(self):
        self.assertEqual(handler.extract_single_line(b"abc\r\n\r\n"), "abc")

    def test_bom_stripped(self):
        self.assertEqual(handler.extract_single_line(b"\xef\xbb\xbfabc"), "abc")

    def test_empty(self):
        for raw in (b"", b"\n", b"  \n"):
            with self.assertRaises(handler.InvalidFileError):
                handler.extract_single_line(raw)

    def test_multiple_lines(self):
        with self.assertRaisesRegex(handler.InvalidFileError, "found 2"):
            handler.extract_single_line(b"a\nb\n")

    def test_not_utf8(self):
        with self.assertRaises(handler.InvalidFileError):
            handler.extract_single_line(b"\xff\xfe\x00")


class LambdaHandlerTests(unittest.TestCase):
    def setUp(self):
        self._orig_client, self._orig_max = handler._s3, handler.MAX_BYTES

    def tearDown(self):
        handler._s3, handler.MAX_BYTES = self._orig_client, self._orig_max

    def test_happy_path(self):
        handler._s3 = FakeS3({("b", "in/data.csv"): b"1,Alice,NYC\n"})
        result = handler.lambda_handler(s3_event("in/data.csv"), None)
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["results"][0]["data"], ["1", "Alice", "NYC"])

    def test_url_encoded_key_is_decoded(self):
        fake = FakeS3({("b", "my dir/a file.txt"): b'{"ok": true}'})
        handler._s3 = fake
        handler.lambda_handler(s3_event("my+dir/a+file.txt"), None)
        self.assertEqual(fake.requested, [("b", "my dir/a file.txt")])

    def test_bad_file_is_skipped_not_raised(self):
        handler._s3 = FakeS3({("b", "k"): b"line1\nline2\n"})
        result = handler.lambda_handler(s3_event("k"), None)  # must not raise
        self.assertEqual((result["processed"], result["rejected"]), (0, 1))

    def test_oversized_file_rejected(self):
        handler.MAX_BYTES = 10
        handler._s3 = FakeS3({("b", "k"): b"x" * 11})
        self.assertEqual(handler.lambda_handler(s3_event("k"), None)["rejected"], 1)

    def test_infrastructure_error_propagates_for_retry(self):
        handler._s3 = FakeS3({})
        with self.assertRaises(RuntimeError):
            handler.lambda_handler(s3_event("missing"), None)

    def test_non_create_events_and_test_events_ignored(self):
        handler._s3 = FakeS3({})
        self.assertEqual(
            handler.lambda_handler(s3_event("k", event_name="ObjectRemoved:Delete"), None)["processed"], 0
        )
        # S3 sends {"Service": "Amazon S3", "Event": "s3:TestEvent", ...} when wiring up
        self.assertEqual(handler.lambda_handler({"Event": "s3:TestEvent"}, None)["processed"], 0)

    def test_result_is_json_serialisable(self):
        handler._s3 = FakeS3({("b", "k"): b'{"a": [1, 2]}'})
        json.dumps(handler.lambda_handler(s3_event("k"), None))


if __name__ == "__main__":
    unittest.main()
