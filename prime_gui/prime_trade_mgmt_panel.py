"""
PRIME v1.0 Trade Management Panel.

Reusable panel component for all trader tabs (UOA, PEAD, MTS, SRS, IDX).
Displays the five-category Trade Factor Evaluation and Claude advisory.

Reference: PRIME Trade Intelligence Paper v1.0, Section 4.

Architectural rule: panel logic calls prime_intelligence functions.
No factor evaluation logic inside this GUI file.
"""

import json
import logging
import tkinter as tk
from tkinter import ttk
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

STATUS_COLORS = {
    "CLEAR": "#2e7d32",
    "SUSPECT": "#f9a825",
    "NULLIFIED": "#c62828",
}

RECOMMENDATION_COLORS = {
    "ENTER": "#2e7d32",
    "MONITOR": "#f9a825",
    "PASS": "#9e9e9e",
    "NULLIFY": "#c62828",
}


class TradeManagementPanel(ttk.LabelFrame):
    """Trade Management Panel displaying factor evaluation and Claude advisory.

    Embeddable in any trader tab. Constructed once, updated via update_display().
    """

    def __init__(self, parent, strategy: str, on_refresh_advisory=None, **kwargs):
        super().__init__(parent, text=f"{strategy} Trade Management", **kwargs)
        self.strategy = strategy
        self._on_refresh_advisory = on_refresh_advisory
        self._build_ui()

    def _build_ui(self):
        # Signal summary row
        summary_frame = ttk.Frame(self)
        summary_frame.pack(fill="x", padx=5, pady=2)

        self._lbl_symbol = ttk.Label(summary_frame, text="--", font=("Consolas", 11, "bold"))
        self._lbl_symbol.pack(side="left", padx=5)
        self._lbl_direction = ttk.Label(summary_frame, text="--")
        self._lbl_direction.pack(side="left", padx=5)
        self._lbl_price = ttk.Label(summary_frame, text="--")
        self._lbl_price.pack(side="left", padx=5)
        self._lbl_pnl = ttk.Label(summary_frame, text="--")
        self._lbl_pnl.pack(side="left", padx=5)

        # Duration class
        dur_frame = ttk.Frame(self)
        dur_frame.pack(fill="x", padx=5, pady=1)
        ttk.Label(dur_frame, text="Duration:").pack(side="left")
        self._lbl_duration = ttk.Label(dur_frame, text="--", font=("Consolas", 10, "bold"))
        self._lbl_duration.pack(side="left", padx=5)
        self._lbl_dur_rationale = ttk.Label(dur_frame, text="")
        self._lbl_dur_rationale.pack(side="left", padx=5)

        # Entry method
        entry_frame = ttk.Frame(self)
        entry_frame.pack(fill="x", padx=5, pady=1)
        ttk.Label(entry_frame, text="Entry:").pack(side="left")
        self._lbl_entry = ttk.Label(entry_frame, text="--")
        self._lbl_entry.pack(side="left", padx=5)

        # Nullifier status
        null_frame = ttk.Frame(self)
        null_frame.pack(fill="x", padx=5, pady=1)
        ttk.Label(null_frame, text="Nullifier:").pack(side="left")
        self._lbl_nullifier = ttk.Label(null_frame, text="CLEAR", font=("Consolas", 10, "bold"))
        self._lbl_nullifier.pack(side="left", padx=5)
        self._lbl_null_detail = ttk.Label(null_frame, text="")
        self._lbl_null_detail.pack(side="left", padx=5)

        # Exit triggers
        ttk.Label(self, text="Exit Triggers:").pack(anchor="w", padx=5, pady=(3, 0))
        self._txt_exits = tk.Text(self, height=4, width=70, font=("Consolas", 9),
                                  state="disabled", wrap="word")
        self._txt_exits.pack(fill="x", padx=5, pady=1)

        # Maintenance flags
        ttk.Label(self, text="Maintenance:").pack(anchor="w", padx=5, pady=(3, 0))
        self._txt_maint = tk.Text(self, height=2, width=70, font=("Consolas", 9),
                                  state="disabled", wrap="word")
        self._txt_maint.pack(fill="x", padx=5, pady=1)

        # Claude advisory
        adv_frame = ttk.Frame(self)
        adv_frame.pack(fill="x", padx=5, pady=(3, 0))
        ttk.Label(adv_frame, text="Claude Advisory:").pack(side="left")
        self._lbl_recommendation = ttk.Label(adv_frame, text="--", font=("Consolas", 10, "bold"))
        self._lbl_recommendation.pack(side="left", padx=5)
        self._lbl_conviction = ttk.Label(adv_frame, text="")
        self._lbl_conviction.pack(side="left", padx=5)

        if self._on_refresh_advisory:
            ttk.Button(adv_frame, text="Refresh Advisory",
                       command=self._on_refresh_advisory).pack(side="right", padx=5)

        self._txt_advisory = tk.Text(self, height=4, width=70, font=("Consolas", 9),
                                     state="disabled", wrap="word")
        self._txt_advisory.pack(fill="x", padx=5, pady=(1, 5))

    def update_display(
        self,
        trade_factors: Optional[Dict[str, Any]] = None,
        advisory: Optional[Dict[str, Any]] = None,
        current_price: float = 0.0,
        pnl: float = 0.0,
    ):
        """Refresh all panel elements with new data."""
        if trade_factors:
            self._update_factors(trade_factors, current_price, pnl)
        if advisory:
            self._update_advisory(advisory)

    def _update_factors(self, tf: Dict[str, Any], current_price: float, pnl: float):
        self._lbl_symbol.config(text=tf.get("symbol", "--"))
        self._lbl_direction.config(text=tf.get("direction", "--"))
        self._lbl_price.config(text=f"${current_price:.2f}" if current_price else "--")
        pnl_text = f"{'+'if pnl >= 0 else ''}{pnl:.2f}" if pnl != 0 else "--"
        self._lbl_pnl.config(text=pnl_text)

        dur = tf.get("duration", {})
        self._lbl_duration.config(text=dur.get("class", "--"))
        self._lbl_dur_rationale.config(text=dur.get("rationale", "")[:60])

        entry = tf.get("entry", {})
        self._lbl_entry.config(text=f"{entry.get('method', '--')} {entry.get('rationale', '')[:50]}")

        null = tf.get("nullifier", {})
        status = null.get("status", "CLEAR")
        self._lbl_nullifier.config(
            text=status,
            foreground=STATUS_COLORS.get(status, "#000000"),
        )
        self._lbl_null_detail.config(text=null.get("rationale", "")[:60])

        self._txt_exits.config(state="normal")
        self._txt_exits.delete("1.0", "end")
        for trigger in tf.get("exit_triggers", []):
            self._txt_exits.insert("end",
                f"  [{trigger.get('status', '')}] {trigger.get('type', '')}: "
                f"{trigger.get('description', '')}\n")
        self._txt_exits.config(state="disabled")

        self._txt_maint.config(state="normal")
        self._txt_maint.delete("1.0", "end")
        for flag in tf.get("maintenance_flags", []):
            self._txt_maint.insert("end", f"  - {flag}\n")
        self._txt_maint.config(state="disabled")

    def _update_advisory(self, adv: Dict[str, Any]):
        rec = adv.get("recommendation", "--")
        self._lbl_recommendation.config(
            text=rec,
            foreground=RECOMMENDATION_COLORS.get(rec, "#000000"),
        )
        self._lbl_conviction.config(text=f"[{adv.get('conviction', '--')}]")

        self._txt_advisory.config(state="normal")
        self._txt_advisory.delete("1.0", "end")
        narrative = adv.get("risk_narrative", "")
        if narrative:
            self._txt_advisory.insert("end", narrative)
        conf_note = adv.get("confidence_note", "")
        if conf_note:
            self._txt_advisory.insert("end", f"\n\nWhat would change: {conf_note}")
        self._txt_advisory.config(state="disabled")
