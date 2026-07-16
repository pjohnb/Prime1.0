"""
WO-PRIME-PARALLEL-SCANS-01 acceptance tests.

Static tests verify the parallel coordinator structure in prime_api_routes.py.
Behavioral tests mock subprocess calls to verify concurrency, ordering,
failure isolation, and bridge sequencing.

ACs that require Phase 3 runtime validation (AC2 wall-clock time, AC3
timestamp spread, AC4 API error rate) are noted but not tested here — they
are validated by the 3-consecutive-trading-day comparison run.
"""

import inspect
import re
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

ROUTES_SRC = (PROJECT_ROOT / "prime_api" / "prime_api_routes.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Static / structural tests
# ---------------------------------------------------------------------------

class TestParallelScansStructure(unittest.TestCase):
    def test_skip_bridge_param_in_run_scanner_bg(self):
        """_run_scanner_bg must accept skip_bridge keyword argument."""
        self.assertIn("skip_bridge", ROUTES_SRC,
                      "_run_scanner_bg must declare skip_bridge parameter")
        self.assertRegex(ROUTES_SRC, r"def _run_scanner_bg\([^)]*skip_bridge",
                         "_run_scanner_bg signature must include skip_bridge")

    def test_skip_bridge_guards_bridge_call(self):
        """Bridge invocation must be guarded by `not skip_bridge`."""
        self.assertIn("not skip_bridge", ROUTES_SRC,
                      "Bridge call must be guarded by `not skip_bridge`")

    def test_parallel_coordinator_exists(self):
        """_run_parallel_deep_scan function must exist."""
        self.assertRegex(ROUTES_SRC, r"def _run_parallel_deep_scan\(",
                         "_run_parallel_deep_scan function not found")

    def test_deep_scan_job_calls_parallel_coordinator(self):
        """_deep_scan_job must delegate to _run_parallel_deep_scan."""
        # Find _deep_scan_job body
        m = re.search(r"def _deep_scan_job\(\):(.*?)(?=\n    def |\n    job_defs|\Z)",
                      ROUTES_SRC, re.DOTALL)
        self.assertIsNotNone(m, "_deep_scan_job not found")
        body = m.group(1)
        self.assertIn("_run_parallel_deep_scan()", body,
                      "_deep_scan_job must call _run_parallel_deep_scan()")

    def test_thread_pool_executor_used(self):
        """ThreadPoolExecutor must be used in the parallel coordinator."""
        # Find _run_parallel_deep_scan body
        start = ROUTES_SRC.index("def _run_parallel_deep_scan(")
        end = ROUTES_SRC.index("\n@api_bp.route", start)
        body = ROUTES_SRC[start:end]
        self.assertIn("ThreadPoolExecutor", body,
                      "Parallel coordinator must use ThreadPoolExecutor")

    def test_polygon_semaphore_used(self):
        """Polygon semaphore must gate concurrent Polygon scanner processes."""
        start = ROUTES_SRC.index("def _run_parallel_deep_scan(")
        end = ROUTES_SRC.index("\n@api_bp.route", start)
        body = ROUTES_SRC[start:end]
        self.assertIn("polygon_sem", body,
                      "Parallel coordinator must declare polygon_sem semaphore")
        self.assertIn("Semaphore", body,
                      "Parallel coordinator must use threading.Semaphore")

    def test_uoa_and_pead_events_gate_psa(self):
        """uoa_done event must be waited before PSA is submitted.

        Note: pead_done.wait() was intentionally removed (Sprint BUG-PSA-SHORT-NOOP):
        PEAD contends for polygon_sem with IDX/SRS (free plan cap=1), which blocks the
        coordinator indefinitely. Gating is now on uoa_done only; PEAD signals reach
        prime_signals via bridge pass 2.
        """
        start = ROUTES_SRC.index("def _run_parallel_deep_scan(")
        end = ROUTES_SRC.index("\n@api_bp.route", start)
        body = ROUTES_SRC[start:end]
        self.assertIn("uoa_done.wait()", body,
                      "Coordinator must wait for uoa_done before PSA")
        # PSA pool submission must come AFTER the uoa_done wait
        uoa_wait_pos = body.index("uoa_done.wait()")
        psa_submit_pos = body.index('pool.submit(_guarded_run, "psa")')
        self.assertGreater(psa_submit_pos, uoa_wait_pos,
                           "PSA must be submitted after uoa_done.wait()")

    def test_two_bridge_passes(self):
        """Coordinator must run at least bridge passes '1' and '2'; a third pass ('3')
        is permitted in confirmation mode (post-MTFA ingestion).
        """
        start = ROUTES_SRC.index("def _run_parallel_deep_scan(")
        end = ROUTES_SRC.index("\n@api_bp.route", start)
        body = ROUTES_SRC[start:end]
        bridge_calls = re.findall(r'_run_bridge\("(\w+)"\)', body)
        self.assertGreaterEqual(len(bridge_calls), 2,
                                f"Expected at least 2 bridge passes, found {len(bridge_calls)}: {bridge_calls}")
        self.assertIn("1", bridge_calls, "Bridge pass '1' not found")
        self.assertIn("2", bridge_calls, "Bridge pass '2' not found")
        # Pass 1 must precede PSA pool submission
        bridge1_pos = body.index('_run_bridge("1")')
        psa_submit_pos = body.index('pool.submit(_guarded_run, "psa")')
        self.assertLess(bridge1_pos, psa_submit_pos,
                        "Bridge pass 1 must occur before PSA is submitted")

    def test_short_submitted_to_pool_after_bridge1(self):
        """WO-PRIME-SHORT-AUTOTRIGGER-01: SHORT pool submit must follow bridge pass 1
        and precede bridge pass 2 (pool exit guarantees SHORT completes before bridge 2)."""
        start = ROUTES_SRC.index("def _run_parallel_deep_scan(")
        end = ROUTES_SRC.index("\n@api_bp.route", start)
        body = ROUTES_SRC[start:end]
        bridge1_pos = body.index('_run_bridge("1")')
        short_submit_pos = body.index('pool.submit(_guarded_run, "short")')
        bridge2_pos = body.index('_run_bridge("2")')
        self.assertGreater(short_submit_pos, bridge1_pos,
                           "SHORT must be submitted to pool after bridge pass 1")
        self.assertLess(short_submit_pos, bridge2_pos,
                        "SHORT pool submit must precede bridge pass 2")

    def test_failure_isolation_try_finally(self):
        """Scanner failures must not block other scanners (try/finally in _guarded_run)."""
        start = ROUTES_SRC.index("def _run_parallel_deep_scan(")
        end = ROUTES_SRC.index("\n@api_bp.route", start)
        body = ROUTES_SRC[start:end]
        self.assertIn("except Exception", body,
                      "_guarded_run must catch exceptions to isolate failures")
        self.assertIn("finally:", body,
                      "_guarded_run must use finally to release semaphore and set events")


# ---------------------------------------------------------------------------
# Behavioral tests (mock-based)
# ---------------------------------------------------------------------------

class TestParallelScansBehavior(unittest.TestCase):
    """Import and exercise _run_parallel_deep_scan with mocked subprocesses."""

    @classmethod
    def setUpClass(cls):
        # Import only after path is set up; guard against Flask side-effects
        # by patching heavy imports before the module loads if needed.
        from prime_api.prime_api_routes import _run_parallel_deep_scan, _SCANNER_MAP
        cls._run_parallel_deep_scan = staticmethod(_run_parallel_deep_scan)
        cls._SCANNER_MAP = _SCANNER_MAP

    def _make_mock_scanner_bg(self, call_log, fail_on=None):
        """Return a mock for _run_scanner_bg that records calls and optionally raises."""
        def _mock(scanner, module, *, skip_bridge=False):
            call_log.append((scanner, skip_bridge))
            if fail_on and scanner == fail_on:
                raise RuntimeError(f"simulated failure: {scanner}")
        return _mock

    def test_all_stage1_scanners_skip_bridge(self):
        """Every Stage-1 scanner must be called with skip_bridge=True."""
        call_log = []
        with patch("prime_api.prime_api_routes._run_scanner_bg",
                   side_effect=self._make_mock_scanner_bg(call_log)), \
             patch("prime_api.prime_api_routes._run_bridge"), \
             patch("prime_api.prime_api_routes._get_scan_log_path", return_value="/tmp/test.log"), \
             patch("prime_api.prime_api_routes._OPS_CONFIG_PATH",
                   PROJECT_ROOT / "ops_config.json"):
            self._run_parallel_deep_scan()

        stage1 = {"idx", "uoa", "mmr", "pead", "srs"}
        for scanner, skip_bridge in call_log:
            if scanner in stage1:
                self.assertTrue(skip_bridge,
                                f"{scanner} must be called with skip_bridge=True in Stage 1")

    def test_psa_skip_bridge_true(self):
        """PSA must also be called with skip_bridge=True (bridge pass 2 consolidates it)."""
        call_log = []
        with patch("prime_api.prime_api_routes._run_scanner_bg",
                   side_effect=self._make_mock_scanner_bg(call_log)), \
             patch("prime_api.prime_api_routes._run_bridge"), \
             patch("prime_api.prime_api_routes._get_scan_log_path", return_value="/tmp/test.log"), \
             patch("prime_api.prime_api_routes._OPS_CONFIG_PATH",
                   PROJECT_ROOT / "ops_config.json"):
            self._run_parallel_deep_scan()

        psa_calls = [c for c in call_log if c[0] == "psa"]
        self.assertEqual(len(psa_calls), 1, "PSA must be called exactly once")
        self.assertTrue(psa_calls[0][1], "PSA must be called with skip_bridge=True")

    def test_short_scanner_skip_bridge_true(self):
        """SHORT runs in the pool via _guarded_run (skip_bridge=True; bridge pass 2 consolidates)."""
        call_log = []
        with patch("prime_api.prime_api_routes._run_scanner_bg",
                   side_effect=self._make_mock_scanner_bg(call_log)), \
             patch("prime_api.prime_api_routes._run_bridge"), \
             patch("prime_api.prime_api_routes._get_scan_log_path", return_value="/tmp/test.log"), \
             patch("prime_api.prime_api_routes._OPS_CONFIG_PATH",
                   PROJECT_ROOT / "ops_config.json"):
            self._run_parallel_deep_scan()

        short_calls = [c for c in call_log if c[0] == "short"]
        self.assertEqual(len(short_calls), 1, "Short scanner must be called exactly once")
        self.assertTrue(short_calls[0][1], "Short scanner must be called with skip_bridge=True")

    def test_uoa_failure_does_not_block_other_scanners(self):
        """AC6: a failing UOA must not prevent IDX, MMR, SRS, PEAD, PSA from running."""
        call_log = []
        with patch("prime_api.prime_api_routes._run_scanner_bg",
                   side_effect=self._make_mock_scanner_bg(call_log, fail_on="uoa")), \
             patch("prime_api.prime_api_routes._run_bridge"), \
             patch("prime_api.prime_api_routes._get_scan_log_path", return_value="/tmp/test.log"), \
             patch("prime_api.prime_api_routes._OPS_CONFIG_PATH",
                   PROJECT_ROOT / "ops_config.json"):
            self._run_parallel_deep_scan()

        ran = {c[0] for c in call_log}
        for expected in ("idx", "mmr", "pead", "srs", "psa"):
            self.assertIn(expected, ran,
                          f"AC6: {expected} must run even if UOA fails")

    def test_bridge_called_twice(self):
        """Exactly two _run_bridge calls must occur per deep scan."""
        bridge_calls = []
        with patch("prime_api.prime_api_routes._run_scanner_bg",
                   side_effect=self._make_mock_scanner_bg([])), \
             patch("prime_api.prime_api_routes._run_bridge",
                   side_effect=lambda label: bridge_calls.append(label)), \
             patch("prime_api.prime_api_routes._get_scan_log_path", return_value="/tmp/test.log"), \
             patch("prime_api.prime_api_routes._OPS_CONFIG_PATH",
                   PROJECT_ROOT / "ops_config.json"):
            self._run_parallel_deep_scan()

        self.assertEqual(len(bridge_calls), 2,
                         f"Expected 2 bridge passes, got {len(bridge_calls)}: {bridge_calls}")
        self.assertIn("1", bridge_calls)
        self.assertIn("2", bridge_calls)

    def test_psa_runs_after_uoa_completes(self):
        """PSA must not start before UOA has completed (ordering via events)."""
        order = []
        uoa_finished = threading.Event()

        def _mock_bg(scanner, module, *, skip_bridge=False):
            if scanner == "uoa":
                order.append("uoa_start")
                # simulate UOA work
                order.append("uoa_end")
            elif scanner == "psa":
                # PSA should only appear after UOA has ended
                order.append("psa_start")

        with patch("prime_api.prime_api_routes._run_scanner_bg", side_effect=_mock_bg), \
             patch("prime_api.prime_api_routes._run_bridge"), \
             patch("prime_api.prime_api_routes._get_scan_log_path", return_value="/tmp/test.log"), \
             patch("prime_api.prime_api_routes._OPS_CONFIG_PATH",
                   PROJECT_ROOT / "ops_config.json"):
            self._run_parallel_deep_scan()

        self.assertIn("uoa_end", order, "UOA must complete")
        self.assertIn("psa_start", order, "PSA must start")
        uoa_end_idx = order.index("uoa_end")
        psa_start_idx = order.index("psa_start")
        self.assertGreater(psa_start_idx, uoa_end_idx,
                           "PSA must start after UOA has completed")


if __name__ == "__main__":
    unittest.main()
