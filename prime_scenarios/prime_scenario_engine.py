"""
PRIME v1.0 Scenario Convergence Engine (WO-PRIME-SCENARIOS-01).

Detects convergence events across scanner signals. Reads scanner output
but never modifies signal records. Engine failure does not affect individual
signal delivery.

Scenario types (Types 4+ and 9 deferred — require MTFA scanner):
  1  Sniper — Pure             IDX STRONG (volume confirmed)
  2  Sniper — Confirmed        IDX (any) + PSA APPROVED
  3  Sniper — Institutional    IDX (any) + UOA/PEAD + PSA APPROVED
  4  Sniper — Trifecta         IDX STRONG + UOA/PEAD + PSA APPROVED
  5  Watch                     Any single signal alone (monitor for convergence)
  6  Sniper — Anomalous        UOA/PEAD + PSA APPROVED (no IDX)
  7  Sniper — Sector Phase     SRS + PSA APPROVED (same symbol)
  8  Sniper — Metals MR        MMR TRANCHE_2 + PSA APPROVED (same symbol)

Staleness framework:
  - PEAD: EXEMPT (always eligible, multi-session drift play)
  - All others: scan_ts date < today (ET midnight) → VETOED (hard 1-session veto)
  - Within scanner soft window: FRESH; beyond soft window: SOFT_STALE
  - Session boundary: ET midnight (America/New_York); handles EST/EDT automatically
  - Naive `now` arguments are always treated as UTC then converted to ET

Signal direction matching:
  - IDX tiers use STRONG-LONG / WEAK-LONG / STRONG-SHORT / WEAK-SHORT
  - UOA/PSA/PEAD use direction field ('LONG' or 'SHORT')
  - SRS: always LONG (bridge only passes RECOVERING phase)
  - MMR TRANCHE_2 → LONG; SHORT_TRANCHE_2 → SHORT
"""

import logging
import pytz
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ET = pytz.timezone("America/New_York")

# ---------------------------------------------------------------------------
# Scenario type registry
# ---------------------------------------------------------------------------

SCENARIO_TYPES: Dict[str, Dict[str, str]] = {
    "1": {"name": "Sniper — Pure",             "conviction": "HIGH"},
    "2": {"name": "Sniper — Confirmed",         "conviction": "HIGH"},
    "3": {"name": "Sniper — Institutional",     "conviction": "HIGH"},
    "4": {"name": "Sniper — Trifecta",          "conviction": "HIGHEST"},
    "5": {"name": "Watch",                       "conviction": "LOW"},
    "6": {"name": "Sniper — Anomalous",         "conviction": "HIGH"},
    "7": {"name": "Sniper — Sector Phase",      "conviction": "HIGH"},
    "8": {"name": "Sniper — Metals MR",         "conviction": "HIGH"},
}

# Soft windows per scanner (beyond this → SOFT_STALE, still eligible)
_SOFT_WINDOWS: Dict[str, timedelta] = {
    "UOA":  timedelta(hours=1),
    "PSA":  timedelta(hours=1),
    "IDX":  timedelta(hours=8),
    "MMR":  timedelta(hours=8),
    "SRS":  timedelta(hours=8),
    "PEAD": timedelta(days=365),  # PEAD is EXEMPT; this sentinel is never hit
}

PEAD_MAX_SESSIONS = 5  # sessions PEAD remains eligible (no specific number given in WO)


# ---------------------------------------------------------------------------
# Staleness helpers
# ---------------------------------------------------------------------------

def _et_now_naive(now: Optional[datetime] = None) -> datetime:
    """Return the current time in ET as a naive datetime.

    Naive `now` is treated as UTC before conversion. This is the correct
    reference for all session-boundary decisions in a US equity trading system.
    """
    if now is None:
        reference_aware = datetime.utcnow().replace(tzinfo=timezone.utc)
    elif now.tzinfo is None:
        reference_aware = now.replace(tzinfo=timezone.utc)
    else:
        reference_aware = now
    et = reference_aware.astimezone(_ET)
    return et.replace(tzinfo=None)


def _parse_ts(ts_str: str) -> Optional[datetime]:
    """Parse an ISO or 'YYYY-MM-DD HH:MM' timestamp to a naive datetime."""
    if not ts_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(ts_str[:len(fmt) + 2].strip(), fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(ts_str.split("+")[0].split("Z")[0].strip())
    except Exception:
        return None


def get_signal_staleness(signal: Dict[str, Any], now: Optional[datetime] = None) -> str:
    """Return 'FRESH', 'SOFT_STALE', or 'VETOED' for a signal.

    PEAD is EXEMPT from the hard veto and remains FRESH up to PEAD_MAX_SESSIONS.
    All other strategies are VETOED if their scan_ts date precedes today in ET.
    Session boundary is ET midnight (America/New_York) — handles EST/EDT automatically.
    Naive `now` is treated as UTC then converted to ET.
    """
    strategy = signal.get("strategy", "")
    scan_ts = _parse_ts(signal.get("scan_ts", ""))
    if scan_ts is None:
        return "VETOED"

    et_ref = _et_now_naive(now)
    today = datetime(et_ref.year, et_ref.month, et_ref.day)  # ET midnight, naive

    if strategy == "PEAD":
        age_days = (et_ref - scan_ts).days
        if age_days > PEAD_MAX_SESSIONS:
            return "VETOED"
        return "FRESH"

    # Hard veto: scan_ts date is before today (ET)
    scan_date = datetime(scan_ts.year, scan_ts.month, scan_ts.day)
    if scan_date < today:
        return "VETOED"

    # Soft window check (elapsed time measured in ET)
    soft_window = _SOFT_WINDOWS.get(strategy, timedelta(hours=8))
    age = et_ref - scan_ts
    if age > soft_window:
        return "SOFT_STALE"

    return "FRESH"


# ---------------------------------------------------------------------------
# Direction helpers
# ---------------------------------------------------------------------------

def _signal_direction(signal: Dict[str, Any]) -> Optional[str]:
    """Resolve the effective direction of a signal.

    IDX uses tier-encoded direction (STRONG-LONG, WEAK-SHORT, etc.).
    All others use the direction field.
    """
    strategy = signal.get("strategy", "")
    if strategy == "IDX":
        tier = (signal.get("tier") or "").upper()
        if tier.endswith("-LONG") or tier.endswith("LONG"):
            return "LONG"
        if tier.endswith("-SHORT") or tier.endswith("SHORT"):
            return "SHORT"
        return None
    direction = (signal.get("direction") or "").upper()
    return direction if direction in ("LONG", "SHORT") else None


def _is_strong(signal: Dict[str, Any]) -> bool:
    """True when a signal is at the STRONG conviction tier."""
    strategy = signal.get("strategy", "")
    tier = (signal.get("tier") or "").upper()
    if strategy == "IDX":
        return "STRONG" in tier
    if strategy in ("UOA", "PEAD"):
        return tier == "STRONG" or signal.get("status", "") == "APPROVED"
    return False


def _is_tranche2(signal: Dict[str, Any]) -> bool:
    """True when an MMR signal is at TRANCHE_2 or SHORT_TRANCHE_2 conviction."""
    tier = (signal.get("tier") or "").upper()
    return tier in ("TRANCHE_2", "SHORT_TRANCHE_2")


# ---------------------------------------------------------------------------
# Scenario builder
# ---------------------------------------------------------------------------

def _build_scenario(
    type_num: str,
    direction: str,
    primary_symbol: str,
    constituent_signals: List[Dict[str, Any]],
    staleness_status: str = "FRESH",
    detected_at: Optional[str] = None,
) -> Dict[str, Any]:
    spec = SCENARIO_TYPES[type_num]
    ts = detected_at or datetime.utcnow().isoformat()
    slim_constituents = [
        {
            "signal_id": s.get("signal_id", ""),
            "strategy": s.get("strategy", ""),
            "tier": s.get("tier", ""),
            "symbol": s.get("symbol", ""),
            "scan_ts": s.get("scan_ts", ""),
            "staleness": s.get("_staleness", "FRESH"),
        }
        for s in constituent_signals
    ]
    from prime_scenarios.prime_scenarios_db import make_scenario_id
    return {
        "scenario_id": make_scenario_id(type_num, primary_symbol, direction, ts),
        "type_num": type_num,
        "type_name": spec["name"],
        "direction": direction,
        "conviction": spec["conviction"],
        "primary_symbol": primary_symbol.upper(),
        "constituent_signals": slim_constituents,
        "staleness_status": staleness_status,
        "detected_at": ts,
    }


def _overall_staleness(signals: List[Dict[str, Any]]) -> str:
    """Worst-case staleness across a set of constituent signals."""
    statuses = [s.get("_staleness", "FRESH") for s in signals]
    if "VETOED" in statuses:
        return "VETOED"
    if "SOFT_STALE" in statuses:
        return "SOFT_STALE"
    return "FRESH"


# ---------------------------------------------------------------------------
# Core detection
# ---------------------------------------------------------------------------

def detect_scenarios(
    signals: List[Dict[str, Any]],
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Detect convergence scenarios from a list of APPROVED signals.

    Returns scenario dicts ready for insert_scenario(). Does not write to DB.
    Signals are never modified — staleness is attached as '_staleness' key
    on a shallow copy for internal use only.

    Detection priority per direction (highest conviction wins for overlapping
    signal sets):
      Types 4 > 3 > 2 > 1  (IDX-based, PSA as individual-stock anchor)
      Type 6 (UOA/PEAD + PSA, no IDX)
      Type 7 (SRS + PSA, same symbol)
      Type 8 (MMR TRANCHE_2 + PSA, same symbol)
      Type 5 (single signal watch — emitted only when no higher type fires)
    """
    detected_at = (now or datetime.utcnow()).isoformat()

    # Annotate each signal with staleness and resolve direction
    annotated: List[Dict[str, Any]] = []
    for raw in signals:
        sig = dict(raw)
        sig["_staleness"] = get_signal_staleness(sig, now)
        sig["_direction"] = _signal_direction(sig)
        annotated.append(sig)

    # Only eligible (non-vetoed) signals participate
    eligible = [s for s in annotated if s["_staleness"] != "VETOED"]

    scenarios: List[Dict[str, Any]] = []
    # Track which signal_ids have already been anchored in a higher scenario
    # so we don't double-emit Type 5 for the same signal
    anchored_ids: set = set()

    for direction in ("LONG", "SHORT"):
        dir_sigs = [s for s in eligible if s["_direction"] == direction]

        idx_all   = [s for s in dir_sigs if s["strategy"] == "IDX"]
        idx_strong = [s for s in idx_all if _is_strong(s)]
        uoa_pead  = [s for s in dir_sigs if s["strategy"] in ("UOA", "PEAD")]
        psa       = [s for s in dir_sigs if s["strategy"] == "PSA"
                     and s.get("status") == "APPROVED"]
        srs       = [s for s in dir_sigs if s["strategy"] == "SRS"]
        mmr_t2    = [s for s in dir_sigs if s["strategy"] == "MMR" and _is_tranche2(s)]

        # --- IDX-based types (Types 1-4): PSA anchors the individual-stock symbol ---
        for psa_sig in psa:
            sym = psa_sig["symbol"]

            if idx_strong and uoa_pead:
                # Type 4: IDX STRONG + UOA/PEAD + PSA
                constituents = [psa_sig, idx_strong[0], uoa_pead[0]]
                st = _overall_staleness(constituents)
                sc = _build_scenario("4", direction, sym, constituents, st, detected_at)
                scenarios.append(sc)
                for s in constituents:
                    anchored_ids.add(s.get("signal_id"))
                continue

            if idx_all and uoa_pead:
                # Type 3: IDX (any) + UOA/PEAD + PSA
                constituents = [psa_sig, idx_all[0], uoa_pead[0]]
                st = _overall_staleness(constituents)
                sc = _build_scenario("3", direction, sym, constituents, st, detected_at)
                scenarios.append(sc)
                for s in constituents:
                    anchored_ids.add(s.get("signal_id"))
                continue

            if idx_all:
                # Type 2: IDX (any) + PSA
                constituents = [psa_sig, idx_all[0]]
                st = _overall_staleness(constituents)
                sc = _build_scenario("2", direction, sym, constituents, st, detected_at)
                scenarios.append(sc)
                for s in constituents:
                    anchored_ids.add(s.get("signal_id"))
                continue

            if uoa_pead:
                # Type 6: UOA/PEAD + PSA on same symbol (no IDX)
                matching_inst = [s for s in uoa_pead if s["symbol"] == sym]
                if matching_inst:
                    constituents = [psa_sig, matching_inst[0]]
                    st = _overall_staleness(constituents)
                    sc = _build_scenario("6", direction, sym, constituents, st, detected_at)
                    scenarios.append(sc)
                    for s in constituents:
                        anchored_ids.add(s.get("signal_id"))
                    continue

        # --- Type 1: IDX STRONG alone (symbol = index ETF) ---
        for idx_sig in idx_strong:
            sym = idx_sig["symbol"]
            st = idx_sig["_staleness"]
            sc = _build_scenario("1", direction, sym, [idx_sig], st, detected_at)
            scenarios.append(sc)
            anchored_ids.add(idx_sig.get("signal_id"))

        # --- Type 7: SRS + PSA on same ETF symbol ---
        for srs_sig in srs:
            sym = srs_sig["symbol"]
            matching_psa = [s for s in psa if s["symbol"] == sym]
            if matching_psa:
                constituents = [srs_sig, matching_psa[0]]
                st = _overall_staleness(constituents)
                sc = _build_scenario("7", direction, sym, constituents, st, detected_at)
                scenarios.append(sc)
                for s in constituents:
                    anchored_ids.add(s.get("signal_id"))

        # --- Type 8: MMR TRANCHE_2 + PSA on same symbol ---
        for mmr_sig in mmr_t2:
            sym = mmr_sig["symbol"]
            matching_psa = [s for s in psa if s["symbol"] == sym]
            if matching_psa:
                constituents = [mmr_sig, matching_psa[0]]
                st = _overall_staleness(constituents)
                sc = _build_scenario("8", direction, sym, constituents, st, detected_at)
                scenarios.append(sc)
                for s in constituents:
                    anchored_ids.add(s.get("signal_id"))

    # --- Type 5: Watch — any single eligible signal not already in a scenario ---
    for sig in eligible:
        if sig.get("signal_id") not in anchored_ids:
            direction = sig["_direction"]
            if not direction:
                continue
            sym = sig["symbol"]
            st = sig["_staleness"]
            sc = _build_scenario("5", direction, sym, [sig], st, detected_at)
            scenarios.append(sc)

    # Remove internal annotation keys before returning
    for sc in scenarios:
        for c in sc.get("constituent_signals", []):
            c.pop("_staleness", None)
            c.pop("_direction", None)

    return scenarios


def run_detection(
    signals: List[Dict[str, Any]],
    db_path=None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Detect scenarios and persist new ones. Returns a results summary.

    This is the primary entry point called from the API layer.
    Engine failure is caught and reported; it does not affect signal delivery.
    """
    from prime_scenarios.prime_scenarios_db import (
        init_scenarios_table,
        insert_scenario,
        expire_old_scenarios,
    )
    try:
        init_scenarios_table(db_path)
        expire_old_scenarios(db_path)
        scenarios = detect_scenarios(signals, now)
        inserted = 0
        for sc in scenarios:
            sid = insert_scenario(sc, db_path)
            if sid:
                inserted += 1
        logger.info(
            "Scenario detection: %d candidates, %d new inserted",
            len(scenarios), inserted,
        )
        return {
            "scenarios_detected": len(scenarios),
            "scenarios_inserted": inserted,
            "breakdown": {
                t: sum(1 for s in scenarios if s["type_num"] == t)
                for t in SCENARIO_TYPES
            },
        }
    except Exception as e:
        logger.error("Scenario engine error (non-fatal): %s", e)
        return {"error": str(e), "scenarios_detected": 0, "scenarios_inserted": 0}
