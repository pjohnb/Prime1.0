# PRIME v1.0

**PRIME** (Portfolio Risk & Intelligence Management Engine) is a signal-led algorithmic trading assistant for a single-operator equity trading desk running on Windows with a Schwab/ThinkOrSwim account in PAPER mode.

PRIME is not a technical scanner. Every signal requires a **predictive trigger** (unusual options activity or post-earnings drift) before technical confirmation is evaluated, then a **DK three-state modifier** (institutional dark-pool intelligence) determines final tier and disposition. The result is a dramatically lower false-signal rate compared to standard technical scanners.

---

## v1.0 Release (Sprint 25 — 2026-06-08)

**What v1.0 delivers:**
- 8 live strategies: PSA, PEAD, UOA, SRS, MTS, IDX, DK, SHORT
- Signal-led architecture: UOA_CALL / UOA_PUT / PEAD_BEAT / PEAD_MISS triggers required
- DK three-state modifier: CONFIRMING / NEUTRAL / NULLIFYING propagated to all signals
- AI advisory layer: position advisor (HOLD/TRIM/EXIT), signal ranker, daily briefing
- Browser UI: Dashboard, Scans, Positions, Signals, Settings, Portfolio tabs with live API
- Scan Control: trigger any scanner from the browser; APScheduler manages daily schedule
- Schwab connection management: mode toggle (PAPER/LIVE), account balances, token status
- PEAD guidance flag: six-value classification (BEAT_RAISE/BEAT_HOLD/BEAT_CUT/MISS_RAISE/MISS_CUT/UNKNOWN)
- In-app help system: ? modal, strategy ⓘ popovers, Start of Day checklist, searchable glossary
- PAPER mode default — all execution simulated via Schwab paper trading account

**Deferred to v1.1/v1.2:** Unusual Whales live DK feed, ML signal scoring, UII instrument detail.

---

## How to Start PRIME

Open two terminals in `C:\Dev\PRIME1.0\`:

**1. API Server (port 5001)**
```powershell
python prime_api/prime_api_server.py
```

**2. UI Server (port 5002)**
```powershell
python prime_ui/prime_ui_server.py
```

**3. Open the Lovable UI**
Navigate to `http://localhost:5002` in your browser.

That is all. The browser UI is the complete daily workflow.
Scan execution, Schwab connection, and schedule management are all in the **Scans** and **Settings** tabs.

> **Note:** The v0.9 Tkinter GUI at `C:\Dev\PRIME\` is frozen as of Sprint 25. Do not use it for daily operation — it will not receive further updates. See `C:\Dev\PRIME\RETIRED.md` for details.

---

## Documentation

All reference documents are in `PRIME1.0 Reference\`:

| Document | Purpose |
|---|---|
| `PRIME_UserManual_v1_0_2026-06-04.docx` | User manual for Christy beta — daily routine, all strategies, DK guide, position management |
| `PRIME_UI_Audit_2026-06-04_v1_0.docx` | UI audit report — Sprint 22 execution roadmap |
| `PRIME_ReleaseNotes_v1_0_2026-06-04.docx` | v1.0 release notes and v1.1 roadmap |
| `PRIME_v1_0_FeatureMatrix.docx` | Feature status table — all strategies and systems |
| `PRIME_WorkOrder_Sprint*.docx` | Per-sprint work orders |
| `PRIME_ChatHandoff_Sprint*.docx` | Per-sprint handoff documents |

---

## Architecture

### Signal Flow
```
Predictive Trigger (UOA / PEAD)
        ↓
Technical Confirmation (A-B-C-D, SMA, RS ratio)
        ↓
DK Three-State Modifier (dark-pool intelligence)
        ↓
prime_signals DB  →  PSA gate  →  short scanner  →  Lovable UI  →  AI advisory
```

### Module Map

| Directory | Purpose |
|---|---|
| `prime_scanners/` | Scanner modules — PSA, PEAD, UOA, SRS, MTS, IDX |
| `prime_intelligence/` | DK trader, short scanner, signal triggers, AI advisory |
| `prime_ai/` | Claude-powered position advisor, signal ranker, briefing panel |
| `prime_data/` | Database layer — prime_db.py, prime_dk_feed.py, prime_signals_db.py |
| `prime_analytics/` | Unified signals DB layer, analytics queries |
| `prime_api/` | Flask REST API (port 5001) serving the Lovable UI |
| `prime_ui/` | Lovable web UI — index.html, dashboard.js, signals.js, help.js |
| `prime_trading/` | Schwab execution, MATA account routing, borrow check |
| `prime_config/` | Config reader, OpsConfig schema |
| `prime_ops/` | Scheduler, health monitor, ops events |
| `prime_gui/` | v1.0 trade management panel (v0.9 GUI is at C:\Dev\PRIME) |
| `tests/` | 790 tests — one file per module |
| `scan_results/` | Scanner output and DK print files |
| `PRIME1.0 Reference/` | Sprint handoffs, work orders, user manual, release notes |

---

## Setup

### Prerequisites
- Python 3.10+
- `pip install -r requirements.txt`
- Schwab account with PAPER trading enabled
- `ops_config.json` (gitignored) — see `ops_config.json.example` for schema

### ANTHROPIC_API_KEY (AI advisory)

PRIME's AI features call `claude-sonnet-4-20250514`. Without a key the advisory
degrades gracefully to deterministic placeholders. Set the key before starting:

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
# or persist:
setx ANTHROPIC_API_KEY "sk-ant-..."
```

The startup check runs automatically when the API server starts. Verify first:
```powershell
python prime_startup.py
```

### ops_config.json (never committed)
Controls runtime toggles:
```json
{
  "use_signal_led_psa": true,
  "use_ai_ranker": true,
  "polygon_api_key": "...",
  "anthropic_api_key": "..."
}
```

---

## Key Design Principles

1. **Signal-led, not technical-led.** No signal ever enters `prime_signals` without a predictive trigger. Technical-only candidates are rejected or held at WATCH.

2. **All DB access through `prime_db.py`.** No direct SQLite calls outside the data layer.

3. **No trading logic in `prime_gui/`.** The GUI renders state; it does not compute signals.

4. **PAPER mode default.** `config.json` defaults to PAPER. LIVE mode requires an explicit confirmation in the Settings → Broker Connection panel.

5. **`config.json` and `ops_config.json` are never committed.** Both are gitignored.

---

*© 2026 xFormative AI · Provisional Patent #63/954,078 · INTERNAL*
