"""
Sprint 33 Thread 1 (CIL-049) -- scanner runner UTF-8 handling.

The scanner runner (_run_scanner_bg) spawns `python -m <scanner>` subprocesses
and appends their stdout to a dated scan log. On Windows the child process
otherwise inherits cp1252 and raises UnicodeEncodeError when a scan prints
non-ASCII characters, crashing the runner silently. The runner now forces
PYTHONIOENCODING=utf-8 for the child and reads/writes the log as UTF-8 with
errors='replace', so non-ASCII scan output is logged without raising.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import prime_api.prime_api_routes as routes


class _FakeProc:
    """Minimal stand-in for subprocess.Popen yielding a non-ASCII stdout line."""

    def __init__(self, *args, **kwargs):
        self.pid = 4321
        self.returncode = 0
        # Symbols/notes/API fields can contain non-ASCII; an arrow + check mark
        # would raise UnicodeEncodeError under cp1252.
        self.stdout = iter(["IDX scan → 2 signals ✓ café\n"])

    def wait(self):
        return 0


class TestScanRunnerHandlesUnicode(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.log_path = self.tmp / "scan_log_test.txt"
        # 'idx' is not in the bridge-ingest set, so no second subprocess runs.
        self.scanner = "idx"

    def test_psa_runner_handles_unicode(self):
        captured = {}

        def _fake_popen(*args, **kwargs):
            captured["env"] = kwargs.get("env")
            captured["encoding"] = kwargs.get("encoding")
            captured["errors"] = kwargs.get("errors")
            return _FakeProc()

        with patch.object(routes._subprocess, "Popen", side_effect=_fake_popen), \
             patch.object(routes, "_get_scan_log_path", return_value=self.log_path):
            # Must not raise despite the non-ASCII child output.
            routes._run_scanner_bg(self.scanner, "prime_intelligence.prime_index_scanner")

        # Child stdout is forced to UTF-8 (the CIL-049 fix).
        self.assertIsNotNone(captured["env"])
        self.assertEqual(captured["env"].get("PYTHONIOENCODING"), "utf-8")
        # Parent reads/writes UTF-8 with errors='replace'.
        self.assertEqual(captured["encoding"], "utf-8")
        self.assertEqual(captured["errors"], "replace")
        # The non-ASCII line was written to the scan log without error.
        contents = self.log_path.read_text(encoding="utf-8")
        self.assertIn("café", contents)
        self.assertIn("→", contents)


if __name__ == "__main__":
    unittest.main()
