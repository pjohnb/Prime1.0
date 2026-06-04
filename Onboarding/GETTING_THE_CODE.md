# Getting the Code

PRIME v1.0 uses two repositories that must be cloned to specific paths.
The paths are hardcoded in batch scripts and internal references — use these exact locations.

## Step 1 — Clone PRIME1.0 (API server + Lovable UI + all scanners)

```powershell
git clone https://github.com/pjohnb/Prime1.0.git C:\Dev\PRIME1.0
```

This is the main application. It contains:
- `prime_api/` — Flask REST API (port 5001)
- `prime_ui/` — Lovable web UI (port 5002)
- `prime_scanners/` — PSA, PEAD, UOA, SRS, MTS, IDX
- `prime_intelligence/` — DK trader, short scanner, AI advisory
- `prime_data/` — Database layer
- `prime_trading/` — Schwab execution + MATA routing
- `tests/` — Full test suite (790 tests)
- `Onboarding/` — This folder

## Step 2 — Clone PRIME (v0.9 Tkinter desktop GUI)

```powershell
git clone https://github.com/pjohnb/Prime.git C:\Dev\PRIME
```

This is the v0.9 desktop GUI (`prime_gui_app.py`) where you run scans and
configure strategy parameters. It is the active scan-execution interface.
The Lovable web UI (PRIME1.0) displays and acts on the results.

## Step 3 — Verify the directory structure

After cloning, your C:\Dev\ directory should look like this:

```
C:\Dev\
  PRIME\           <- v0.9 Tkinter GUI (pjohnb/Prime)
    prime_gui_app.py
    prime_config.json
    ...
  PRIME1.0\        <- v1.0 API + Lovable UI (pjohnb/Prime1.0)
    prime_api\
    prime_ui\
    Onboarding\    <- you are here
    ...
```

## Step 4 — Continue with the Onboarding Guide

Open `PRIME_BetaOnboardingGuide_v1_0_2026-06-04.docx` in this folder.
It walks through Python environment setup, API keys, Schwab OAuth,
config file creation, and the startup sequence.

## Staying Up to Date

```powershell
cd C:\Dev\PRIME1.0
git pull origin master

cd C:\Dev\PRIME
git pull origin main
```

Both repositories use their respective default branches (`master` for PRIME1.0,
`main` for PRIME). Sprint tags (`v1.0-sprint22`, etc.) mark stable release points.
