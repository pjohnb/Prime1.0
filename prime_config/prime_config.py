"""
PRIME v1.0 configuration reader.
Single source of truth for all settings — no hardcoded paths, schedules, or credentials.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional


_PROJECT_ROOT = Path(__file__).resolve().parent.parent

REQUIRED_CONFIG_KEYS = [
    "polygon_api_key",
    "tradestation",
    "schwab_snapshot",
    "execution",
    "risk_management",
    "finnhub_api_key",
]

REQUIRED_OPS_KEYS = [
    "scan_schedule",
    "notification_channels",
    "health_check_interval",
]


class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


@dataclass
class TradeStationConfig:
    client_id: str = ""
    client_secret: str = ""
    account_id: str = ""
    access_token: str = ""
    refresh_token: str = ""
    token_expiry: str = ""
    last_refresh_date: str = ""


@dataclass
class OrderBookConfig:
    enabled: bool = True
    mode: str = "supplement"
    access_token: str = ""
    api_base_url: str = "https://api.tradestation.com/v3"
    quotes_endpoint: str = "/marketdata/quotes"
    max_spread_percentage: float = 0.5
    min_liquidity_score: float = 7.0
    max_imbalance_ratio: float = 3.0
    min_stability: float = 5.0
    confidence_threshold: float = 0.7
    timeout: int = 10
    retry_attempts: int = 2
    bypass_reason: str = ""


@dataclass
class PolygonConfig:
    include_extended_hours: bool = True
    market_status: str = "ALL"
    after_hours: bool = True
    pre_market: bool = True


@dataclass
class SchwabSnapshotConfig:
    schwab_token_path: str = ""
    schwab_app_key: str = ""
    schwab_app_secret: str = ""
    snapshot_batch_size: int = 500
    snapshot_request_timeout: int = 30
    snapshot_max_retries: int = 2
    snapshot_retry_delay: int = 2
    schwab_callback_url: str = "https://127.0.0.1"


@dataclass
class DataCollectionConfig:
    extended_hours_tracking: bool = True
    continue_after_market_close: bool = True


@dataclass
class ExecutionConfig:
    trailing_stop_pct: float = 5.0
    position_size_pct: float = 2.0
    max_position_value: int = 10000
    auto_execute: bool = False
    market_orders_only: bool = True


@dataclass
class RiskManagementConfig:
    max_portfolio_risk: float = 10.0
    max_daily_orders: int = 5
    account_balance: int = 100000
    emergency_stop_loss: float = 8.0


@dataclass
class OpsConfig:
    scan_schedule: Any = field(default_factory=dict)
    notification_channels: Any = "TBD"
    health_check_interval: int = 900
    retry: Any = field(default_factory=lambda: {"max_attempts": 3, "interval_seconds": 300})
    # Sprint 16 Item 1: fallback source for ANTHROPIC_API_KEY when not in env.
    # Documented here as schema; the actual value lives only in the gitignored
    # ops_config.json and is NEVER committed. Prefer the environment variable.
    anthropic_api_key: str = ""
    # Sprint 16 Item 2: toggle the AI signal ranker (select_top_n) in the PSA
    # execution path. When false, PSA uses the deterministic score-sort selector.
    use_ai_ranker: bool = True
    # Sprint 16 Item 4: Unusual Whales API key for the future live DK feed.
    # Schema-only; value lives in the gitignored ops_config.json, never committed.
    uw_api_key: str = ""
    # Sprint 16 Item 3: MATA routing account for Index Trader (IDX) signals.
    # Index trades route to a single account (default "Joint Brokerage").
    index_account: str = "Joint Brokerage"
    # Sprint 17 Item 2: short-side risk parameters. short_size_multiplier scales
    # the equivalent long size (hard-capped at 2% of account in the sizer);
    # short_stop_loss_pct is the +5% adverse-move stop (price rises = exit);
    # short_time_stop_minutes mirrors the long time stop.
    short_size_multiplier: float = 0.5
    short_stop_loss_pct: float = 0.05
    short_time_stop_minutes: int = 1950
    # Sprint 17 Item 4: MATA account profiles for direction-aware allocation.
    # Each entry: {name, type, buying_power, margin_available, weight}. SHORT
    # routing excludes IRA-type accounts and sizes against margin_available.
    mata_accounts: Any = field(default_factory=list)
    # Sprint 18 Item 1: signal-led PSA approval. When true (default), PSA requires
    # a UOA-call or PEAD-beat trigger before APPROVED; false = legacy technical-only.
    use_signal_led_psa: bool = True


@dataclass
class PrimeConfig:
    polygon_api_key: str = ""
    finnhub_api_key: str = ""
    access_token: str = ""
    refresh_token: str = ""
    # Sprint 14 Item 2: write-path auth + mode guard. Never committed.
    api_token: str = ""
    trading_mode: str = "PAPER"
    tradestation: TradeStationConfig = field(default_factory=TradeStationConfig)
    order_book: OrderBookConfig = field(default_factory=OrderBookConfig)
    polygon: PolygonConfig = field(default_factory=PolygonConfig)
    schwab_snapshot: SchwabSnapshotConfig = field(default_factory=SchwabSnapshotConfig)
    data_collection: DataCollectionConfig = field(default_factory=DataCollectionConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    risk_management: RiskManagementConfig = field(default_factory=RiskManagementConfig)
    ops: OpsConfig = field(default_factory=OpsConfig)

    # Derived paths
    project_root: Path = field(default_factory=lambda: _PROJECT_ROOT)
    db_path: Path = field(default_factory=lambda: _PROJECT_ROOT / "data" / "prime_trades.db")
    log_dir: Path = field(default_factory=lambda: _PROJECT_ROOT / "logs")
    scan_results_dir: Path = field(default_factory=lambda: _PROJECT_ROOT / "scan_results")


def _build_dataclass(cls, raw: dict):
    """Build a dataclass from a dict, ignoring unknown keys."""
    valid = {f.name for f in cls.__dataclass_fields__.values()}
    return cls(**{k: v for k, v in raw.items() if k in valid})


def _validate_keys(data: dict, required: list[str], source: str) -> None:
    missing = [k for k in required if k not in data]
    if missing:
        raise ConfigError(
            f"{source} is missing required keys: {', '.join(missing)}"
        )


def load_config(
    config_path: Optional[Path] = None,
    ops_config_path: Optional[Path] = None,
) -> PrimeConfig:
    """Load config.json and ops_config.json, return a typed PrimeConfig.

    Raises ConfigError if files are missing or required keys are absent.
    """
    if config_path is None:
        config_path = _PROJECT_ROOT / "config.json"
    if ops_config_path is None:
        ops_config_path = _PROJECT_ROOT / "ops_config.json"

    if not config_path.exists():
        raise ConfigError(f"config.json not found at {config_path}")
    if not ops_config_path.exists():
        raise ConfigError(f"ops_config.json not found at {ops_config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    with open(ops_config_path, "r", encoding="utf-8") as f:
        ops_raw = json.load(f)

    _validate_keys(raw, REQUIRED_CONFIG_KEYS, "config.json")
    _validate_keys(ops_raw, REQUIRED_OPS_KEYS, "ops_config.json")

    cfg = PrimeConfig(
        polygon_api_key=raw.get("polygon_api_key", ""),
        finnhub_api_key=raw.get("finnhub_api_key", ""),
        access_token=raw.get("access_token", ""),
        refresh_token=raw.get("refresh_token", ""),
        api_token=raw.get("api_token", ""),
        trading_mode=raw.get("trading_mode", "PAPER"),
        tradestation=_build_dataclass(TradeStationConfig, raw.get("tradestation", {})),
        order_book=_build_dataclass(OrderBookConfig, raw.get("order_book", {})),
        polygon=_build_dataclass(PolygonConfig, raw.get("polygon", {})),
        schwab_snapshot=_build_dataclass(SchwabSnapshotConfig, raw.get("schwab_snapshot", {})),
        data_collection=_build_dataclass(DataCollectionConfig, raw.get("data_collection", {})),
        execution=_build_dataclass(ExecutionConfig, raw.get("execution", {})),
        risk_management=_build_dataclass(RiskManagementConfig, raw.get("risk_management", {})),
        ops=_build_dataclass(OpsConfig, ops_raw),
    )

    return cfg


# Module-level singleton — import and use directly
_config: Optional[PrimeConfig] = None


def get_config() -> PrimeConfig:
    """Return the cached config singleton. Loads on first call."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reload_config() -> PrimeConfig:
    """Force-reload config from disk."""
    global _config
    _config = load_config()
    return _config
