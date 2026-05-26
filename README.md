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
