@echo off
REM PRIME v1.0 -- daily scan sequence (Sprint 18 Item 2).
REM Dependency order: UOA and PEAD must populate prime_signals first, then the
REM scanner bridge ingests them, then the short scanner reads UOA-put / PEAD-miss
REM triggers from prime_signals. Index and PSA scans run alongside.
REM PAPER mode only. Run from the repo root.

cd /d C:\Dev\PRIME1.0

echo [run_scan] UOA scan...
python -m prime_scanners.prime_uoa_scanner

echo [run_scan] PEAD scan...
python -m prime_scanners.prime_pead_scanner

echo [run_scan] PSA scan...
python -m prime_scanners.prime_psa_scanner

echo [run_scan] Index scan...
python -m prime_intelligence.prime_index_scanner

echo [run_scan] Bridge -- ingest latest scanner output into prime_signals...
python -m prime_bridge.prime_signal_bridge --ingest-latest

echo [run_scan] Short scanner (after UOA/PEAD; reads triggers from prime_signals)...
python -m prime_intelligence.prime_short_scanner

echo [run_scan] Done.
