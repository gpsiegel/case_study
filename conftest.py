"""Put the project root on sys.path so `import s3_event_processor` works
regardless of which directory pytest is launched from (e.g. the repo root in CI)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))