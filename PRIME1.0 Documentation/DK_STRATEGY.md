# DK (Dark-Pool) Strategy

Sprint 16 Item 4. PRIME's DK strategy classifies off-exchange / dark-pool
activity per symbol into one of:

- **SIGNAL** — confirming off-exchange accumulation; a tradeable bullish DK row.
- **NULLIFIER** — activity that *suppresses* other strategies' signals for the
  same symbol (those APPROVED non-DK signals are flipped to `SUPPRESSED`).
- **NEUTRAL / UNAVAILABLE** — no DK row written.

## Two scoring layers

### 1. Legacy composite — `prime_dark_pool.score_dk_signal(symbol)`

Combines three FINRA/tape sources into a 0–100 `dk_score` with `dk_status`:

| Component | Rule | Contribution |
|---|---|---|
| FINRA ATS % | `ats_pct > 45` **and** rising | +30 |
| Block tape prints | `min(block_count * 25, 50)` | up to +50 |
| Short-volume spike | `short_pct > 1.5 ×` 20-day avg | **NULLIFYING override** (score → 0) |

`dk_score ≥ 50` → `CONFIRMING`; `> 0` → `NEUTRAL`; all sources `None` →
`UNAVAILABLE`.

### 2. Matured print factors — `prime_dark_pool.score_dk_prints(prints, reference_price, total_volume)`

Sprint 16 expands classification beyond the composite by reading the dark-pool
**prints** supplied by the single DK data entry point
`prime_data.prime_dk_feed.get_dk_prints()`. Three factors:

| Factor | Definition |
|---|---|
| `volume_ratio` | dark-pool print volume ÷ total session volume (0–1) |
| `price_proximity` | fraction of prints within **0.5%** of the reference (current) price |
| `repeat_activity` | number of prints for the symbol this session |

**Composite `print_score` (0–100):**

```
proximity_component = price_proximity * 40
repeat_component    = min(repeat_activity, 5)/5 * 30
volume_component    = min(volume_ratio, 0.5)/0.5 * 30
print_score         = proximity_component + repeat_component + volume_component
```

**Matured verdict thresholds:**

| Verdict | Condition |
|---|---|
| `SIGNAL` | `print_score ≥ 50` **and** `price_proximity ≥ 0.5` (genuine accumulation at price) |
| `NULLIFIER` | `volume_ratio ≥ 0.5` **and** `price_proximity < 0.3` (heavy off-exchange volume scattered away from price → absorption/distribution) |
| `None` | otherwise (NEUTRAL) |

Constants live in `prime_intelligence/prime_dark_pool.py`:
`PRICE_PROXIMITY_PCT=0.5`, `SIGNAL_PRINT_SCORE=50`, `SIGNAL_MIN_PROXIMITY=0.5`,
`NULLIFIER_VOL_RATIO=0.5`, `NULLIFIER_MAX_PROXIMITY=0.3`.

## Combining the two layers — `prime_dk_trader._combine_verdicts`

1. A matured **NULLIFIER** always wins (suppresses regardless of the composite).
2. A matured **SIGNAL** upgrades a non-nullified symbol to SIGNAL.
3. Otherwise the legacy `classify_dk()` verdict (from `score_dk_signal`) stands.

When the matured verdict drives the classification, `dk_status` / `dk_score` are
updated so the written `prime_signals` row stays internally consistent. The
matured factors are recorded in the row's `factors` JSON under `matured`.

## Data feed abstraction — `prime_data/prime_dk_feed.py`

`get_dk_prints(symbols, date)` is the **single entry point for all DK print
data**, returning records shaped as:

```json
{"symbol": "AAPL", "price": 185.0, "volume": 15000,
 "timestamp": "2026-06-03T14:31:00", "venue": "ATS"}
```

- **Stub (current):** reads `scan_results/dk_prints_*.json`, filtered by symbol
  and date; returns `[]` when none present.
- **Unusual Whales (deferred):** flip `_USE_UNUSUAL_WHALES = True` (with
  `UW_API_KEY` configured) to swap `_get_prints_unusual_whales()` in behind the
  same `get_dk_prints()` signature — a one-commit swap, no caller changes.

`UW_API_KEY` resolves from the environment first, then `ops_config.json`
(`uw_api_key`). The value is never committed.
