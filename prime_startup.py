"""
PRIME v1.0 Startup Validator (Sprint 16 Item 1).

Lightweight pre-flight check run before any AI-dependent PRIME process (notably
the API server) starts, so live Claude advisory calls have a key available.

ANTHROPIC_API_KEY resolution order:
  1. Already present in the environment  -> use it, pass silently.
  2. Missing from env but present as `anthropic_api_key` in ops_config.json
     -> load it into os.environ and emit a clear WARN (env is preferred).
  3. Missing from both                    -> emit a clear WARN; do NOT crash.
     AI calls then degrade gracefully to deterministic fallbacks.

The key value is NEVER committed: ops_config.json is gitignored and the key is
read from it only at runtime. This module never raises on a missing key.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent

ENV_VAR = "ANTHROPIC_API_KEY"
OPS_CONFIG_KEY = "anthropic_api_key"


def _read_ops_config(ops_config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Read ops_config.json as a read-only reference. Returns {} on any failure."""
    if ops_config_path is None:
        ops_config_path = _PROJECT_ROOT / "ops_config.json"
    try:
        with open(ops_config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.debug("could not read ops_config.json: %s", e)
        return {}


def ensure_anthropic_api_key(ops_config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Ensure ANTHROPIC_API_KEY is available, falling back to ops_config.json.

    Returns a status dict: {"present": bool, "source": "env"|"ops_config"|None}.
    Never raises; emits a WARN when the key is loaded from config or absent.
    """
    env_key = os.environ.get(ENV_VAR, "").strip()
    if env_key:
        logger.debug("%s present in environment", ENV_VAR)
        return {"present": True, "source": "env"}

    cfg_key = str(_read_ops_config(ops_config_path).get(OPS_CONFIG_KEY, "") or "").strip()
    if cfg_key:
        os.environ[ENV_VAR] = cfg_key
        msg = ("[WARN] {0} not set in environment; loaded from ops_config.json. "
               "Prefer setting it in the environment.".format(ENV_VAR))
        logger.warning(msg)
        print(msg)
        return {"present": True, "source": "ops_config"}

    msg = ("[WARN] {0} not found in environment or ops_config.json. Live AI "
           "advisory calls will fall back to deterministic placeholders. Set "
           "{0} in your environment to enable live Claude recommendations."
           .format(ENV_VAR))
    logger.warning(msg)
    print(msg)
    return {"present": False, "source": None}


def run_startup_checks(ops_config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Run all PRIME pre-flight checks. Returns a summary dict. Never raises."""
    return {"anthropic_api_key": ensure_anthropic_api_key(ops_config_path)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    result = run_startup_checks()
    status = result["anthropic_api_key"]
    if status["present"]:
        print("PRIME startup OK -- ANTHROPIC_API_KEY available (source: {0})."
              .format(status["source"]))
    else:
        print("PRIME startup -- ANTHROPIC_API_KEY missing; AI advisory degraded.")
