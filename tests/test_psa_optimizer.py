"""
WO-PRIME-PSA-CALIBRATION-02 Phase 2 acceptance tests — PSA Settings Optimizer.

Covers: PCA scoring (AC2), threshold back-calc (AC3), gap analysis (AC4),
and diagnostic scan structure (AC1 structural checks).
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_scanners.prime_psa_diagnostic import (
    compute_pca_scores,
    compute_recommended_thresholds,
    compute_gap_analysis,
    load_diagnostic_results,
    FACTOR_COLS,
)


def _make_rows(n=10, seed=42):
    """Generate synthetic factor rows for testing."""
    import random
    random.seed(seed)
    rows = []
    for i in range(n):
        rows.append({
            "symbol": f"SYM{i:03d}",
            "price": round(10.0 + i * 5, 2),
            "daily_volume": float(500_000 + i * 50_000),
            "momentum_pct": round(40.0 + i * 2.5, 2),
            "volume_pct": round(45.0 + i * 1.5, 2),
            "volatility_pct": round(50.0 + i, 2),
            "bc_drawdown": round(1.0 + (n - i) * 0.2, 2),
            "cd_drawdown": round(0.5 + (n - i) * 0.1, 2),
        })
    return rows


class TestPcaScoring(unittest.TestCase):

    def test_returns_weights_and_ranked(self):
        rows = _make_rows(20)
        result = compute_pca_scores(rows)
        self.assertIn("weights", result)
        self.assertIn("ranked", result)

    def test_weights_cover_all_factors(self):
        rows = _make_rows(20)
        result = compute_pca_scores(rows)
        for col in FACTOR_COLS:
            self.assertIn(col, result["weights"], f"Missing weight for factor: {col}")

    def test_ranked_length_matches_input(self):
        rows = _make_rows(15)
        result = compute_pca_scores(rows)
        self.assertEqual(len(result["ranked"]), 15)

    def test_all_ranked_have_pca_score(self):
        rows = _make_rows(10)
        result = compute_pca_scores(rows)
        for r in result["ranked"]:
            self.assertIn("pca_score", r)
            self.assertIsInstance(r["pca_score"], float)

    def test_ranked_sorted_descending(self):
        rows = _make_rows(20)
        result = compute_pca_scores(rows)
        scores = [r["pca_score"] for r in result["ranked"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_deterministic_for_same_input(self):
        rows = _make_rows(20)
        r1 = compute_pca_scores(rows)
        r2 = compute_pca_scores(rows)
        self.assertEqual(r1["ranked"][0]["symbol"], r2["ranked"][0]["symbol"])

    def test_empty_input_returns_empty(self):
        result = compute_pca_scores([])
        self.assertEqual(result["weights"], {})
        self.assertEqual(result["ranked"], [])


class TestThresholdBackCalc(unittest.TestCase):

    def setUp(self):
        rows = _make_rows(20)
        pca = compute_pca_scores(rows)
        self.ranked = pca["ranked"]

    def test_returns_all_eight_keys(self):
        thresh = compute_recommended_thresholds(self.ranked, 10)
        expected = {
            "psa_min_price", "psa_max_price", "psa_min_daily_volume",
            "psa_stage1_momentum", "psa_stage1_volume", "psa_stage1_volatility",
            "psa_stage1_bc_drawdown", "psa_stage1_cd_drawdown",
        }
        self.assertEqual(set(thresh.keys()), expected)

    def test_min_price_admits_all_top_n(self):
        n = 10
        thresh = compute_recommended_thresholds(self.ranked, n)
        top = self.ranked[:n]
        min_price = min(r["price"] for r in top)
        # Recommended min_price <= min_price in top-N, so all top-N pass
        self.assertLessEqual(thresh["psa_min_price"], min_price + 0.01)

    def test_max_price_admits_all_top_n(self):
        n = 10
        thresh = compute_recommended_thresholds(self.ranked, n)
        top = self.ranked[:n]
        max_price = max(r["price"] for r in top)
        self.assertGreaterEqual(thresh["psa_max_price"], max_price)

    def test_min_daily_volume_admits_all_top_n(self):
        n = 10
        thresh = compute_recommended_thresholds(self.ranked, n)
        top = self.ranked[:n]
        min_vol = min(r["daily_volume"] for r in top)
        self.assertLessEqual(thresh["psa_min_daily_volume"], min_vol + 1)

    def test_stage1_lower_bounds_admit_all_top_n(self):
        n = 10
        thresh = compute_recommended_thresholds(self.ranked, n)
        top = self.ranked[:n]
        for key, factor in [
            ("psa_stage1_momentum", "momentum_pct"),
            ("psa_stage1_volume", "volume_pct"),
            ("psa_stage1_volatility", "volatility_pct"),
        ]:
            min_val = min(r[factor] for r in top)
            self.assertLessEqual(thresh[key], min_val + 0.01,
                                 f"{key} threshold must be <= min({factor}) in top-N")

    def test_stage1_drawdown_upper_bounds_admit_all_top_n(self):
        n = 10
        thresh = compute_recommended_thresholds(self.ranked, n)
        top = self.ranked[:n]
        max_bc = max(r["bc_drawdown"] for r in top)
        max_cd = max(r["cd_drawdown"] for r in top)
        self.assertGreaterEqual(thresh["psa_stage1_bc_drawdown"], max_bc - 0.01)
        self.assertGreaterEqual(thresh["psa_stage1_cd_drawdown"], max_cd - 0.01)

    def test_empty_returns_empty(self):
        self.assertEqual(compute_recommended_thresholds([], 10), {})

    def test_target_n_larger_than_ranked_uses_all(self):
        thresh = compute_recommended_thresholds(self.ranked, 1000)
        self.assertIn("psa_min_price", thresh)


class TestGapAnalysis(unittest.TestCase):

    def _write_psa_json(self, tmpdir, signals, stage0_rejections=None):
        data = {
            "signals": [{"symbol": s} for s in signals],
            "stage0_rejections": stage0_rejections or [],
        }
        p = Path(tmpdir) / "psa_scan_20260101_0900_ET.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return Path(tmpdir)

    def test_approved_classification(self):
        with tempfile.TemporaryDirectory() as tmp:
            scan_dir = self._write_psa_json(tmp, signals=["AAPL", "MSFT"])
            gap = compute_gap_analysis(["AAPL", "MSFT", "GOOGL"],
                                       scan_results_dir=scan_dir)
        statuses = {r["symbol"]: r["live_scan_status"] for r in gap["symbols"]}
        self.assertEqual(statuses["AAPL"], "approved")
        self.assertEqual(statuses["MSFT"], "approved")
        self.assertEqual(statuses["GOOGL"], "blocked_stage1_unknown")

    def test_gap_counts_sum_to_target_n(self):
        with tempfile.TemporaryDirectory() as tmp:
            scan_dir = self._write_psa_json(tmp, signals=["AAPL"])
            gap = compute_gap_analysis(["AAPL", "MSFT", "GOOGL"],
                                       scan_results_dir=scan_dir)
        total = gap["currently_approved"] + gap["blocked_stage0"] + gap["blocked_stage1_unknown"]
        self.assertEqual(total, 3)

    def test_no_scan_data_all_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            gap = compute_gap_analysis(["AAPL", "MSFT"],
                                       scan_results_dir=Path(tmp))
        for r in gap["symbols"]:
            self.assertEqual(r["live_scan_status"], "blocked_stage1_unknown")

    def test_stage0_db_classification(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            from prime_data.prime_db import init_psa_stage0_rejections_table, write_psa_stage0_rejections
            init_psa_stage0_rejections_table(db)
            write_psa_stage0_rejections(
                rejections=[{"symbol": "TSLA", "criterion": "min_price",
                             "symbol_value": 2.0, "threshold_value": 5.0}],
                run_timestamp="2026-07-15T09:30:00",
                db_path=db,
            )
            gap = compute_gap_analysis(["TSLA"], db_path=db,
                                       scan_results_dir=Path(tmp))
        statuses = {r["symbol"]: r["live_scan_status"] for r in gap["symbols"]}
        self.assertEqual(statuses["TSLA"], "blocked_stage0")

    def test_approved_takes_priority_over_stage0(self):
        """If a symbol appears in both approved and stage0 (edge case), approved wins."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            from prime_data.prime_db import init_psa_stage0_rejections_table, write_psa_stage0_rejections
            init_psa_stage0_rejections_table(db)
            write_psa_stage0_rejections(
                rejections=[{"symbol": "AAPL", "criterion": "min_price",
                             "symbol_value": 2.0, "threshold_value": 5.0}],
                run_timestamp="2026-07-15T09:30:00",
                db_path=db,
            )
            scan_dir = self._write_psa_json(tmp, signals=["AAPL"])
            gap = compute_gap_analysis(["AAPL"], db_path=db,
                                       scan_results_dir=scan_dir)
        statuses = {r["symbol"]: r["live_scan_status"] for r in gap["symbols"]}
        self.assertEqual(statuses["AAPL"], "approved")


class TestLoadDiagnosticResults(unittest.TestCase):

    def test_returns_none_when_no_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = load_diagnostic_results(Path(tmp))
        self.assertIsNone(result)

    def test_loads_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            from prime_scanners.prime_psa_diagnostic import DIAG_RESULT_FILENAME
            data = {"run_timestamp": "2026-07-15T09:00:00", "factors": []}
            (Path(tmp) / DIAG_RESULT_FILENAME).write_text(json.dumps(data), encoding="utf-8")
            result = load_diagnostic_results(Path(tmp))
        self.assertIsNotNone(result)
        self.assertEqual(result["run_timestamp"], "2026-07-15T09:00:00")


if __name__ == "__main__":
    unittest.main()
