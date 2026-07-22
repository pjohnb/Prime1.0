"""WO-PRIME-SCAN-SUMMARY-01: Tests for evaluateScenarioAchievability() in scenarios.js."""
import json
import os
import subprocess

import pytest

_SCENARIOS_JS = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "prime_ui", "scenarios.js")
)

_NODE_PREAMBLE = """
global.document = {
  getElementById: () => ({ style: {}, innerHTML: '', scrollTop: 0, scrollHeight: 0 }),
  addEventListener: () => {},
  removeEventListener: () => {},
  querySelectorAll: () => [],
};
global.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
const fs = require('fs');
eval(fs.readFileSync(%s, 'utf8'));
""" % json.dumps(_SCENARIOS_JS)


def _run(signals):
    script = _NODE_PREAMBLE + (
        f"process.stdout.write(JSON.stringify(evaluateScenarioAchievability({json.dumps(signals)})));"
    )
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, f"Node error:\n{proc.stderr}"
    return json.loads(proc.stdout)


_IDX_STRONG = {"strategy": "IDX", "tier": "STRONG_LONG", "status": "APPROVED"}
_IDX_WEAK   = {"strategy": "IDX", "tier": "WEAK_LONG",   "status": "APPROVED"}
# AUDIT-005: real bridge_psa_result() output only ever carries tier
# WATCH/APPROVED/STRONG — these fixtures previously used stale placeholder
# tier strings ("PSA_CONFIRMED"/"PSA_WATCH") that predate the tier-based
# evaluateScenarioAchievability() check and never matched it.
_PSA_APPR   = {"strategy": "PSA", "tier": "APPROVED", "status": "APPROVED"}
_PSA_WATCH  = {"strategy": "PSA", "tier": "WATCH",     "status": "APPROVED"}
_UOA_STRONG = {"strategy": "UOA", "tier": "STRONG",   "status": "APPROVED"}
_PEAD_STRONG = {"strategy": "PEAD", "tier": "STRONG",  "status": "APPROVED"}
_MTFA_STRONG = {"strategy": "MTFA", "tier": "STRONG",  "status": "APPROVED"}
_SRS_RECOVERING = {"strategy": "SRS",  "tier": "RECOVERING", "status": "APPROVED"}
_SRS        = {"strategy": "SRS",  "tier": "CONFIRMED", "status": "APPROVED"}
_MMR_T2     = {"strategy": "MMR",  "tier": "TRANCHE_2", "status": "APPROVED"}
_MMR        = {"strategy": "MMR",  "tier": "TRANCHE_1", "status": "APPROVED"}


def test_empty_signals_all_not_met():
    result = _run([])
    assert all(r == "NOT_MET" for r in result), result
    assert len(result) == 11


def test_idx_strong_type1_achievable():
    result = _run([_IDX_STRONG])
    assert result[0] == "ACHIEVABLE"  # Type 1


def test_idx_weak_type1_partial():
    result = _run([_IDX_WEAK])
    assert result[0] == "PARTIAL"  # Type 1 — IDX present but not STRONG


def test_idx_psa_approved_uoa_strong_types_2_3_achievable():
    signals = [_IDX_STRONG, _PSA_APPR, _UOA_STRONG]
    result = _run(signals)
    assert result[1] == "ACHIEVABLE"  # Type 2
    assert result[2] == "ACHIEVABLE"  # Type 3


def test_idx_strong_psa_approved_uoa_strong_types_2_3_4_achievable():
    signals = [_IDX_STRONG, _PSA_APPR, _UOA_STRONG]
    result = _run(signals)
    assert result[1] == "ACHIEVABLE"  # Type 2
    assert result[2] == "ACHIEVABLE"  # Type 3
    assert result[3] == "ACHIEVABLE"  # Type 4


def test_idx_mtfa_strong_type9_achievable():
    result = _run([_IDX_STRONG, _MTFA_STRONG])
    assert result[8] == "ACHIEVABLE"  # Type 9


def test_all_four_type10_achievable():
    signals = [_IDX_STRONG, _PSA_APPR, _UOA_STRONG, _PEAD_STRONG]
    result = _run(signals)
    assert result[9] == "ACHIEVABLE"  # Type 10


def test_type11_always_not_met():
    for signals in [[], [_IDX_STRONG], [_IDX_STRONG, _PSA_APPR, _UOA_STRONG, _PEAD_STRONG, _MTFA_STRONG, _SRS, _MMR]]:
        result = _run(signals)
        assert result[10] == "NOT_MET", f"Type 11 must always be NOT_MET, signals={signals}"


def test_any_signal_type5_achievable():
    for sig in [_IDX_WEAK, _PSA_WATCH, _UOA_STRONG, _SRS, _MMR]:
        result = _run([sig])
        assert result[4] == "ACHIEVABLE", f"Type 5 must be ACHIEVABLE with any signal: {sig}"


def test_type5_not_met_when_empty():
    result = _run([])
    assert result[4] == "NOT_MET"


def test_psa_approved_uoa_strong_type6_achievable_no_idx():
    result = _run([_PSA_APPR, _UOA_STRONG])
    assert result[5] == "ACHIEVABLE"  # Type 6 — no IDX required


def test_mmr_type8_achievable():
    result = _run([_MMR_T2, _PSA_APPR])
    assert result[7] == "ACHIEVABLE"


def test_mmr_tranche1_only_type8_partial():
    result = _run([_MMR])
    assert result[7] == "PARTIAL"


def test_idx_srs_type7_achievable():
    result = _run([_SRS_RECOVERING, _PSA_APPR])
    assert result[6] == "ACHIEVABLE"


def test_srs_non_recovering_type7_partial():
    result = _run([_SRS])
    assert result[6] == "PARTIAL"


def test_psa_watch_not_approved_makes_type2_partial():
    result = _run([_IDX_STRONG, _PSA_WATCH])
    assert result[1] == "PARTIAL"  # IDX present but PSA not APPROVED


def test_returns_eleven_elements():
    assert len(_run([])) == 11
    assert len(_run([_IDX_STRONG, _PSA_APPR])) == 11
