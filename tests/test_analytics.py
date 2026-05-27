"""
Sprint 9 Item 3 (CIL-101) acceptance tests -- Analytics Tab Rewrite.
Covers signal write, trade linkage, filter/sort, analytics summary,
sector analytics, factor analysis, empty state, migration idempotent.
"""

import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from prime_analytics.prime_signals_db import (
    get_analytics_summary,
    get_factor_analysis,
    get_sector_analytics,
    get_signals,
    init_signals_table,
    insert_signal,
    link_signal_to_trade,
)
from prime_data.prime_db import (
    close_trade,
    get_trade,
    init_db,
    insert_trade,
)
from scripts.migrate_signals_backfill import migrate


class TestSignalsCRUD(unittest.TestCase):
    """AC: signal write, filter, sort."""

    def setUp(self):
        self.db = Path(__file__).parent / "_test_analytics.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def test_insert_and_fetch(self):
        sid = insert_signal("AAPL", "UOA", "2026-05-27T10:00:00",
                            entry_price=185.0, score=7.5, sector="Technology",
                            db_path=self.db)
        self.assertIsNotNone(sid)
        signals = get_signals(db_path=self.db)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["symbol"], "AAPL")
        self.assertEqual(signals[0]["strategy"], "UOA")

    def test_filter_by_strategy(self):
        insert_signal("AAPL", "UOA", "2026-05-27T10:00:00", db_path=self.db)
        insert_signal("MSFT", "PEAD", "2026-05-27T10:00:00", db_path=self.db)
        uoa = get_signals(strategy="UOA", db_path=self.db)
        self.assertEqual(len(uoa), 1)
        self.assertEqual(uoa[0]["strategy"], "UOA")

    def test_filter_by_symbol(self):
        insert_signal("AAPL", "UOA", "2026-05-27T10:00:00", db_path=self.db)
        insert_signal("MSFT", "UOA", "2026-05-27T11:00:00", db_path=self.db)
        aapl = get_signals(symbol="AAPL", db_path=self.db)
        self.assertEqual(len(aapl), 1)

    def test_filter_by_sector(self):
        insert_signal("AAPL", "UOA", "2026-05-27T10:00:00",
                       sector="Technology", db_path=self.db)
        insert_signal("JPM", "UOA", "2026-05-27T10:00:00",
                       sector="Financials", db_path=self.db)
        tech = get_signals(sector="Technology", db_path=self.db)
        self.assertEqual(len(tech), 1)

    def test_sort_by_scan_ts_desc(self):
        insert_signal("AAPL", "UOA", "2026-05-27T10:00:00", db_path=self.db)
        insert_signal("MSFT", "UOA", "2026-05-27T12:00:00", db_path=self.db)
        signals = get_signals(db_path=self.db)
        self.assertEqual(signals[0]["symbol"], "MSFT")

    def test_empty_state(self):
        signals = get_signals(db_path=self.db)
        self.assertEqual(len(signals), 0)

    def test_limit(self):
        for i in range(10):
            insert_signal(f"SYM{i}", "UOA", f"2026-05-27T{10+i}:00:00", db_path=self.db)
        signals = get_signals(limit=5, db_path=self.db)
        self.assertEqual(len(signals), 5)


class TestTradeLinkage(unittest.TestCase):
    """AC: trade linkage via trade_id FK."""

    def setUp(self):
        self.db = Path(__file__).parent / "_test_analytics_link.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def test_link_signal_to_trade(self):
        sid = insert_signal("AAPL", "UOA", "2026-05-27T10:00:00", db_path=self.db)
        tid = insert_trade(
            strategy="UOA", symbol="AAPL", direction="LONG",
            mode="PAPER", order_type="MARKET", shares=10,
            entry_time="2026-05-27T10:00:00", price_at_scan=185.0,
            db_path=self.db,
        )
        link_signal_to_trade(sid, tid, db_path=self.db)
        signals = get_signals(db_path=self.db)
        self.assertEqual(signals[0]["trade_id"], tid)
        self.assertEqual(signals[0]["status"], "TRADED")


class TestAnalyticsSummary(unittest.TestCase):
    """AC: all metrics live, no stale cache."""

    def setUp(self):
        self.db = Path(__file__).parent / "_test_analytics_summary.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def test_empty_summary(self):
        summary = get_analytics_summary(db_path=self.db)
        self.assertEqual(summary["total_signals"], 0)
        self.assertEqual(summary["total_pnl"], 0)

    def test_summary_with_data(self):
        tid = insert_trade(
            strategy="UOA", symbol="AAPL", direction="LONG",
            mode="PAPER", order_type="MARKET", shares=10,
            entry_time="2026-05-27T10:00:00", price_at_scan=185.0,
            db_path=self.db,
        )
        close_trade(tid, 190.0, "2026-05-27T12:00:00", "TARGET",
                     50.0, 2.7, 120, db_path=self.db)
        sid = insert_signal("AAPL", "UOA", "2026-05-27T10:00:00",
                            score=8.0, db_path=self.db)
        link_signal_to_trade(sid, tid, db_path=self.db)

        summary = get_analytics_summary(db_path=self.db)
        self.assertEqual(summary["total_signals"], 1)
        strats = summary["strategies"]
        self.assertEqual(len(strats), 1)
        self.assertEqual(strats[0]["strategy"], "UOA")
        self.assertEqual(strats[0]["wins"], 1)
        self.assertGreater(strats[0]["total_pnl"], 0)

    def test_summary_by_strategy(self):
        insert_signal("AAPL", "UOA", "2026-05-27T10:00:00", db_path=self.db)
        insert_signal("MSFT", "PEAD", "2026-05-27T10:00:00", db_path=self.db)
        summary = get_analytics_summary(strategy="UOA", db_path=self.db)
        self.assertEqual(summary["total_signals"], 1)

    def test_win_rate_calculation(self):
        for i, (sym, pnl) in enumerate([("AAPL", 50), ("MSFT", -20), ("NVDA", 30)]):
            tid = insert_trade(
                strategy="UOA", symbol=sym, direction="LONG",
                mode="PAPER", order_type="MARKET", shares=10,
                entry_time=f"2026-05-27T{10+i}:00:00", price_at_scan=100.0,
                db_path=self.db,
            )
            close_trade(tid, 100.0, f"2026-05-27T{12+i}:00:00", "TEST",
                         pnl, 0.0, 120, db_path=self.db)
            sid = insert_signal(sym, "UOA", f"2026-05-27T{10+i}:00:00", db_path=self.db)
            link_signal_to_trade(sid, tid, db_path=self.db)

        summary = get_analytics_summary(db_path=self.db)
        strat = summary["strategies"][0]
        self.assertEqual(strat["wins"], 2)
        self.assertEqual(strat["losses"], 1)
        self.assertAlmostEqual(strat["win_rate"], 66.7, places=0)


class TestSectorAnalytics(unittest.TestCase):
    """AC: performance grouped by GICS sector."""

    def setUp(self):
        self.db = Path(__file__).parent / "_test_analytics_sector.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def test_sector_grouping(self):
        insert_signal("AAPL", "UOA", "2026-05-27T10:00:00",
                       sector="Technology", db_path=self.db)
        insert_signal("JPM", "UOA", "2026-05-27T10:00:00",
                       sector="Financials", db_path=self.db)
        sectors = get_sector_analytics(db_path=self.db)
        self.assertEqual(len(sectors), 2)
        sector_names = {s["sector"] for s in sectors}
        self.assertIn("Technology", sector_names)
        self.assertIn("Financials", sector_names)

    def test_empty_sectors(self):
        sectors = get_sector_analytics(db_path=self.db)
        self.assertEqual(len(sectors), 0)


class TestFactorAnalysis(unittest.TestCase):
    """AC: entry quality, duration classification breakdown."""

    def setUp(self):
        self.db = Path(__file__).parent / "_test_analytics_factors.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def test_duration_breakdown(self):
        factors_st = json.dumps({"duration": {"class": "ST"}, "entry": {"method": "IMMEDIATE_FULL"}})
        factors_mt = json.dumps({"duration": {"class": "MT"}, "entry": {"method": "SCALED"}})
        insert_signal("AAPL", "UOA", "2026-05-27T10:00:00",
                       factors=factors_st, score=8.0, db_path=self.db)
        insert_signal("MSFT", "UOA", "2026-05-27T11:00:00",
                       factors=factors_mt, score=7.0, db_path=self.db)
        fa = get_factor_analysis(db_path=self.db)
        dur = fa["duration_breakdown"]
        self.assertEqual(len(dur), 2)
        classes = {d["class"] for d in dur}
        self.assertIn("ST", classes)
        self.assertIn("MT", classes)

    def test_entry_method_breakdown(self):
        factors = json.dumps({"duration": {"class": "ST"}, "entry": {"method": "IMMEDIATE_FULL"}})
        insert_signal("AAPL", "UOA", "2026-05-27T10:00:00",
                       factors=factors, db_path=self.db)
        fa = get_factor_analysis(db_path=self.db)
        entry = fa["entry_method_breakdown"]
        self.assertTrue(len(entry) >= 1)

    def test_empty_factors(self):
        fa = get_factor_analysis(db_path=self.db)
        self.assertEqual(len(fa["duration_breakdown"]), 0)


class TestMigrationBackfill(unittest.TestCase):
    """AC: migration idempotent, backfill existing trade_log into prime_signals."""

    def setUp(self):
        self.db = Path(__file__).parent / "_test_analytics_migrate.db"
        if self.db.exists():
            self.db.unlink()
        init_db(self.db)
        init_signals_table(self.db)

    def tearDown(self):
        if self.db.exists():
            self.db.unlink()

    def test_backfill_creates_signals(self):
        insert_trade(
            strategy="UOA", symbol="AAPL", direction="LONG",
            mode="PAPER", order_type="MARKET", shares=10,
            entry_time="2026-05-27T10:00:00", price_at_scan=185.0,
            db_path=self.db,
        )
        count = migrate(db_path=self.db)
        self.assertEqual(count, 1)
        signals = get_signals(db_path=self.db)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["symbol"], "AAPL")

    def test_migration_idempotent(self):
        insert_trade(
            strategy="UOA", symbol="AAPL", direction="LONG",
            mode="PAPER", order_type="MARKET", shares=10,
            entry_time="2026-05-27T10:00:00", price_at_scan=185.0,
            db_path=self.db,
        )
        count1 = migrate(db_path=self.db)
        count2 = migrate(db_path=self.db)
        self.assertEqual(count1, 1)
        self.assertEqual(count2, 0)
        signals = get_signals(db_path=self.db)
        self.assertEqual(len(signals), 1)

    def test_migration_preserves_trade_linkage(self):
        tid = insert_trade(
            strategy="UOA", symbol="AAPL", direction="LONG",
            mode="PAPER", order_type="MARKET", shares=10,
            entry_time="2026-05-27T10:00:00", price_at_scan=185.0,
            db_path=self.db,
        )
        migrate(db_path=self.db)
        signals = get_signals(db_path=self.db)
        self.assertEqual(signals[0]["trade_id"], tid)

    def test_migration_sets_sector(self):
        insert_trade(
            strategy="UOA", symbol="AAPL", direction="LONG",
            mode="PAPER", order_type="MARKET", shares=10,
            entry_time="2026-05-27T10:00:00", price_at_scan=185.0,
            db_path=self.db,
        )
        migrate(db_path=self.db)
        signals = get_signals(db_path=self.db)
        self.assertEqual(signals[0]["sector"], "Technology")


if __name__ == "__main__":
    unittest.main()
