# PRIME 1.0

PRIME (Portfolio Risk & Intelligence Management Engine) is an automated equity trading system that scans for unusual options activity, evaluates multi-factor signals via Claude-powered intelligence, manages positions through Schwab and TradeStation APIs, and delivers real-time notifications and daily digest reports. Built for a single-operator trading desk running on Windows.

## Module Map

| Directory | Purpose |
|---|---|
| `prime_scanners/` | Scanner modules (UOA, PEAD, dark pool, sector rotation) |
| `prime_intelligence/` | Factor evaluation, Claude advisor, dark pool analysis |
| `prime_data/` | Database layer (SQLite trades DB, schema, queries) |
| `prime_notifications/` | Notifier service, daily digest generator |
| `prime_ops/` | Scheduler, health monitor, ops automation |
| `prime_trading/` | Schwab/TradeStation execution, MATA (Multi-Asset Trading Adapter) |
| `prime_gui/` | Tkinter UI app and all trader tabs |
| `prime_config/` | Config reader and validation |
| `tests/` | One test file per module |
| `data/` | Runtime data (prime_trades.db) -- not committed |
| `scan_results/` | Scanner output files |
| `logs/` | Run logs -- not committed |
| `PRIME1.0 Roadmap/` | Strategy docs, sprint briefs, requirements |
| `PRIME1.0 Documentation/` | Technical specs, architecture docs, TIP paper |
| `PRIME1.0 Reference/` | CIL, handoff docs, digest archive |

## Setup

### ANTHROPIC_API_KEY (live AI advisory)

PRIME's AI advisory features (position advisory, signal ranker, briefing panel)
call Claude (`claude-sonnet-4-20250514`). Without a key, these features degrade
gracefully to deterministic placeholders. To enable live recommendations, set
the key **in your environment** before starting the API server:

```powershell
# PowerShell (per-session)
$env:ANTHROPIC_API_KEY = "sk-ant-..."
# or persist for the user:
setx ANTHROPIC_API_KEY "sk-ant-..."
```

The environment is the preferred and authoritative source. As a fallback, the
key may be placed in the gitignored `ops_config.json` under `anthropic_api_key`
(never committed). On startup, `prime_startup.run_startup_checks()` runs first:
it uses the env var if present, otherwise loads the key from `ops_config.json`
(with a WARN), otherwise prints a clear WARN and continues in degraded mode.

```powershell
# Verify the key resolves before starting the server:
python prime_startup.py
# Start the API server (runs the startup check first):
python prime_api/prime_api_server.py
```

The startup check is wired as the first call in `prime_api_server.py`. With a
key set, `GET /api/v1/advisory/positions` returns live Claude recommendations.
