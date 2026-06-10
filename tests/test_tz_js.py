"""Sprint 29 TZ-01 -- run the prime_ui/tz.js node test suite under pytest.

Keeps the JavaScript timezone tests inside the single `pytest` gate used at
sprint close. Skips cleanly when node is unavailable.
"""

import shutil
import subprocess
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TZ_TEST_JS = PROJECT_ROOT / "tests" / "test_tz.js"


class TestTzJs(unittest.TestCase):
    def test_tz_js_suite_passes(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not available on PATH")
        proc = subprocess.run(
            [node, str(TZ_TEST_JS)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            proc.returncode, 0,
            msg=f"tz.js tests failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}",
        )
        self.assertIn("test groups passed", proc.stdout)


if __name__ == "__main__":
    unittest.main()
