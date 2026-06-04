# PRIME v1.0 — Onboarding Package

Everything you need to get started with PRIME v1.0 in PAPER mode.
Read the files in the order listed below.

---

## Reading Order

### 1. GETTING_THE_CODE.md (this folder)
Start here if you do not yet have the code on your machine.
Clone instructions for both repositories (`pjohnb/Prime1.0` and `pjohnb/Prime`)
to the required paths (`C:\Dev\PRIME1.0` and `C:\Dev\PRIME`).

### 2. PRIME_BetaOnboardingGuide_v1_0_2026-06-04.docx
Full step-by-step setup guide. Covers:
- Prerequisites (Python 3.10+, Windows 10/11)
- Directory structure and why both repos are needed
- Broker capability tiers — choose your starting point:
  - **Monitor-Only**: Polygon.io key only. Signals + dashboard work. No order entry.
  - **Partial**: Add Schwab PAPER account. Full UI including order entry and position tracking.
  - **Full**: Add Anthropic API key. Live AI advisory, signal ranker, DK conviction scoring.
- Python virtual environment setup
- API key setup: Anthropic (AI advisory) + Polygon.io (market data)
- Schwab OAuth flow: app registration → credentials → initial auth → account hash
- `ops_config.json` field reference table
- Startup sequence: three terminals (API server, UI server, Tkinter GUI)
- Config file templates
- Quick troubleshooting table

### 3. PRIME_UserManual_v1_0_2026-06-04.docx
Operational manual for daily use. Assumes you are set up and running.
Covers daily routine, all 8 strategies, the DK three-state guide,
position management, short selling rules, AI advisory, MATA account routing,
and a complete glossary. Written peer-to-peer — advanced trader knowledge assumed.

---

## Config File Templates (also in this folder)

| File | What to do with it |
|---|---|
| `config.json.template` | Copy to `C:\Dev\PRIME1.0\config.json`. Fill in your Schwab app key/secret and set `api_token` to a random secret string. **Never commit config.json.** |
| `ops_config.example.json` | Copy to `C:\Dev\PRIME1.0\ops_config.json`. Fill in API keys, Schwab credentials, and MATA account IDs. **Never commit ops_config.json.** |

Both files are gitignored in the PRIME1.0 repo. The templates (this folder) are safe
to commit — they contain only PLACEHOLDER values and documentation, no real credentials.

---

## Quick Reference: Three Terminals to Start PRIME

```powershell
# Terminal 1 — API server (port 5001)
cd C:\Dev\PRIME1.0
$env:ANTHROPIC_API_KEY = "sk-ant-..."   # if not already in environment
python prime_api/prime_api_server.py

# Terminal 2 — UI server (port 5002)
cd C:\Dev\PRIME1.0
python prime_ui/prime_ui_server.py

# Terminal 3 — Tkinter desktop GUI (scan execution)
cd C:\Dev\PRIME
python prime_gui_app.py
```

Then open **http://localhost:5002** in your browser.

---

## In-App Help

Once the Lovable UI is running, click the **?** button in the top-right corner
of the UI to access:
- Daily Routine schedule (pre-market through close, with exact times)
- Signal Tiers reference (STRONG / WATCH / SUPPRESSED)
- DK Three-State guide (CONFIRMING / NEUTRAL / NULLIFYING)
- Risk Rules (stops, sizing, short-selling constraints)
- Searchable Glossary (25 PRIME-specific terms)
- Strategy ⓘ popovers on the Signals tab (trigger/confirmation/DK/stop per strategy)
- Start of Day Checklist on the Dashboard

---

*PRIME v1.0 · © 2026 xFormative AI · Provisional Patent #63/954,078 · INTERNAL*
