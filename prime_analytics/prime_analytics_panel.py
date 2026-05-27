"""
PRIME v1.0 Analytics Panel (CIL-101).

Orthogonal tab architecture -- each tab is independent, not a drill-down.
All data sourced from prime_signals + prime_trade_log via prime_signals_db.

Tabs:
  1. Overview -- aggregate P&L, win rate, avg hold, total trades, by strategy
  2. By Strategy -- individual PEAD/UOA/SRS/PSA/MTS/IDX breakdowns
  3. By Sector -- performance grouped by GICS sector
  4. Signal History -- unified prime_signals view with filter/sort
  5. Factor Analysis -- entry quality, stop accuracy, duration classification

No logic in this file -- all data from prime_analytics.prime_signals_db.
"""

import logging
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

STRATEGIES = ["UOA", "PEAD", "SRS", "PSA", "MTS", "IDX"]


class AnalyticsPanel(ttk.Frame):
    """Analytics panel with five orthogonal tabs."""

    def __init__(
        self,
        parent,
        get_summary: Optional[Callable] = None,
        get_signals: Optional[Callable] = None,
        get_sector_analytics: Optional[Callable] = None,
        get_factor_analysis: Optional[Callable] = None,
        **kwargs,
    ):
        super().__init__(parent, **kwargs)
        self._get_summary = get_summary
        self._get_signals = get_signals
        self._get_sector_analytics = get_sector_analytics
        self._get_factor_analysis = get_factor_analysis
        self._build_ui()

    def _build_ui(self):
        self._notebook = ttk.Notebook(self)
        self._notebook.pack(fill="both", expand=True)

        self._tab_overview = ttk.Frame(self._notebook)
        self._tab_strategy = ttk.Frame(self._notebook)
        self._tab_sector = ttk.Frame(self._notebook)
        self._tab_signals = ttk.Frame(self._notebook)
        self._tab_factors = ttk.Frame(self._notebook)

        self._notebook.add(self._tab_overview, text="Overview")
        self._notebook.add(self._tab_strategy, text="By Strategy")
        self._notebook.add(self._tab_sector, text="By Sector")
        self._notebook.add(self._tab_signals, text="Signal History")
        self._notebook.add(self._tab_factors, text="Factor Analysis")

        self._build_overview_tab()
        self._build_strategy_tab()
        self._build_sector_tab()
        self._build_signals_tab()
        self._build_factors_tab()

        ttk.Button(self, text="Refresh All", command=self.refresh_all).pack(pady=5)

    def _build_overview_tab(self):
        cols = ("strategy", "signals", "traded", "win_rate", "total_pnl", "avg_score", "avg_hold")
        self._overview_tree = ttk.Treeview(self._tab_overview, columns=cols,
                                           show="headings", height=8)
        headers = ("Strategy", "Signals", "Traded", "Win Rate %", "Total P&L", "Avg Score", "Avg Hold (min)")
        for col, hdr in zip(cols, headers):
            self._overview_tree.heading(col, text=hdr)
            self._overview_tree.column(col, width=100, anchor="center")
        self._overview_tree.pack(fill="both", expand=True, padx=5, pady=5)

        self._lbl_overview_totals = ttk.Label(self._tab_overview, text="", font=("Consolas", 10))
        self._lbl_overview_totals.pack(padx=5, pady=2)

    def _build_strategy_tab(self):
        self._strategy_notebook = ttk.Notebook(self._tab_strategy)
        self._strategy_notebook.pack(fill="both", expand=True)
        self._strategy_trees = {}
        for strat in STRATEGIES:
            frame = ttk.Frame(self._strategy_notebook)
            self._strategy_notebook.add(frame, text=strat)
            cols = ("field", "value")
            tree = ttk.Treeview(frame, columns=cols, show="headings", height=8)
            tree.heading("field", text="Metric")
            tree.heading("value", text="Value")
            tree.column("field", width=200)
            tree.column("value", width=200)
            tree.pack(fill="both", expand=True, padx=5, pady=5)
            self._strategy_trees[strat] = tree

    def _build_sector_tab(self):
        cols = ("sector", "signals", "traded", "win_rate", "total_pnl", "avg_score")
        self._sector_tree = ttk.Treeview(self._tab_sector, columns=cols,
                                         show="headings", height=12)
        headers = ("Sector", "Signals", "Traded", "Win Rate %", "Total P&L", "Avg Score")
        for col, hdr in zip(cols, headers):
            self._sector_tree.heading(col, text=hdr)
            self._sector_tree.column(col, width=110, anchor="center")
        self._sector_tree.pack(fill="both", expand=True, padx=5, pady=5)

    def _build_signals_tab(self):
        filter_frame = ttk.Frame(self._tab_signals)
        filter_frame.pack(fill="x", padx=5, pady=2)

        ttk.Label(filter_frame, text="Strategy:").pack(side="left")
        self._sig_strategy_var = tk.StringVar(value="ALL")
        combo = ttk.Combobox(filter_frame, textvariable=self._sig_strategy_var,
                             values=["ALL"] + STRATEGIES, width=8, state="readonly")
        combo.pack(side="left", padx=5)

        ttk.Label(filter_frame, text="Symbol:").pack(side="left")
        self._sig_symbol_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=self._sig_symbol_var, width=8).pack(side="left", padx=5)

        ttk.Button(filter_frame, text="Filter", command=self._refresh_signals).pack(side="left", padx=5)

        cols = ("scan_ts", "symbol", "strategy", "score", "sector", "status", "trade_id")
        self._signals_tree = ttk.Treeview(self._tab_signals, columns=cols,
                                          show="headings", height=15)
        headers = ("Scan Time", "Symbol", "Strategy", "Score", "Sector", "Status", "Trade ID")
        for col, hdr in zip(cols, headers):
            self._signals_tree.heading(col, text=hdr)
            self._signals_tree.column(col, width=100, anchor="center")
        self._signals_tree.pack(fill="both", expand=True, padx=5, pady=2)

    def _build_factors_tab(self):
        ttk.Label(self._tab_factors, text="Duration Classification Breakdown",
                  font=("Consolas", 10, "bold")).pack(anchor="w", padx=5, pady=(5, 0))
        cols_d = ("class", "count", "avg_score")
        self._duration_tree = ttk.Treeview(self._tab_factors, columns=cols_d,
                                           show="headings", height=4)
        for col in cols_d:
            self._duration_tree.heading(col, text=col.replace("_", " ").title())
            self._duration_tree.column(col, width=120, anchor="center")
        self._duration_tree.pack(fill="x", padx=5, pady=2)

        ttk.Label(self._tab_factors, text="Entry Method Breakdown",
                  font=("Consolas", 10, "bold")).pack(anchor="w", padx=5, pady=(5, 0))
        cols_e = ("method", "count", "total_pnl")
        self._entry_tree = ttk.Treeview(self._tab_factors, columns=cols_e,
                                        show="headings", height=5)
        for col in cols_e:
            self._entry_tree.heading(col, text=col.replace("_", " ").title())
            self._entry_tree.column(col, width=120, anchor="center")
        self._entry_tree.pack(fill="x", padx=5, pady=2)

    # --- Refresh methods ---

    def refresh_all(self):
        self._refresh_overview()
        self._refresh_strategies()
        self._refresh_sectors()
        self._refresh_signals()
        self._refresh_factors()

    def _refresh_overview(self):
        for item in self._overview_tree.get_children():
            self._overview_tree.delete(item)
        if not self._get_summary:
            return
        try:
            summary = self._get_summary()
            for s in summary.get("strategies", []):
                self._overview_tree.insert("", "end", values=(
                    s["strategy"], s["signal_count"], s["traded_count"],
                    f"{s['win_rate']}%", f"${s['total_pnl']:,.2f}",
                    s["avg_score"], int(s["avg_hold_minutes"]),
                ))
            self._lbl_overview_totals.config(
                text=f"Total Signals: {summary['total_signals']}  |  "
                     f"Total P&L: ${summary['total_pnl']:,.2f}"
            )
        except Exception as e:
            logger.warning("Overview refresh failed: %s", e)

    def _refresh_strategies(self):
        if not self._get_summary:
            return
        try:
            for strat in STRATEGIES:
                tree = self._strategy_trees[strat]
                for item in tree.get_children():
                    tree.delete(item)
                summary = self._get_summary(strategy=strat)
                strats = summary.get("strategies", [])
                if not strats:
                    tree.insert("", "end", values=("No data", "--"))
                    continue
                s = strats[0]
                for field, val in [
                    ("Signal Count", s["signal_count"]),
                    ("Traded Count", s["traded_count"]),
                    ("Win Rate", f"{s['win_rate']}%"),
                    ("Total P&L", f"${s['total_pnl']:,.2f}"),
                    ("Avg Score", s["avg_score"]),
                    ("Wins", s["wins"]),
                    ("Losses", s["losses"]),
                    ("Avg Hold (min)", int(s["avg_hold_minutes"])),
                ]:
                    tree.insert("", "end", values=(field, val))
        except Exception as e:
            logger.warning("Strategy refresh failed: %s", e)

    def _refresh_sectors(self):
        for item in self._sector_tree.get_children():
            self._sector_tree.delete(item)
        if not self._get_sector_analytics:
            return
        try:
            sectors = self._get_sector_analytics()
            for s in sectors:
                self._sector_tree.insert("", "end", values=(
                    s.get("sector", "Unknown"),
                    s.get("signal_count", 0),
                    s.get("traded_count", 0),
                    f"{s.get('win_rate', 0)}%",
                    f"${s.get('total_pnl', 0):,.2f}",
                    s.get("avg_score", 0),
                ))
        except Exception as e:
            logger.warning("Sector refresh failed: %s", e)

    def _refresh_signals(self):
        for item in self._signals_tree.get_children():
            self._signals_tree.delete(item)
        if not self._get_signals:
            return
        try:
            strat = self._sig_strategy_var.get()
            sym = self._sig_symbol_var.get().strip().upper() or None
            kwargs = {}
            if strat != "ALL":
                kwargs["strategy"] = strat
            if sym:
                kwargs["symbol"] = sym
            signals = self._get_signals(**kwargs)
            for s in signals:
                self._signals_tree.insert("", "end", values=(
                    s.get("scan_ts", ""),
                    s.get("symbol", ""),
                    s.get("strategy", ""),
                    s.get("score", 0),
                    s.get("sector", ""),
                    s.get("status", ""),
                    s.get("trade_id", "") or "",
                ))
        except Exception as e:
            logger.warning("Signal history refresh failed: %s", e)

    def _refresh_factors(self):
        for tree in (self._duration_tree, self._entry_tree):
            for item in tree.get_children():
                tree.delete(item)
        if not self._get_factor_analysis:
            return
        try:
            fa = self._get_factor_analysis()
            for d in fa.get("duration_breakdown", []):
                self._duration_tree.insert("", "end", values=(
                    d["class"], d["count"], d["avg_score"],
                ))
            for e in fa.get("entry_method_breakdown", []):
                self._entry_tree.insert("", "end", values=(
                    e["method"], e["count"], f"${e['total_pnl']:,.2f}",
                ))
        except Exception as e:
            logger.warning("Factor analysis refresh failed: %s", e)
