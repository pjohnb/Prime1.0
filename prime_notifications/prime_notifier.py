"""
PRIME v1.0 Notification Dispatcher (Ops Sprint 2, Phase 1).

Sends digest and per-signal notifications via configured channel.
Email (SMTP) first; falls back to file write if SMTP not configured or fails.
"""

import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DIGEST_DIR = _PROJECT_ROOT / "logs" / "digests"


def _get_smtp_config() -> Optional[Dict[str, Any]]:
    """Load SMTP config from config.json. Returns None if not configured."""
    try:
        from prime_config.prime_config import get_config
        cfg = get_config()
        raw_path = cfg.project_root / "config.json"
        import json
        with open(raw_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        smtp = raw.get("smtp", {})
        if smtp.get("host") and smtp.get("from_addr") and smtp.get("to_addr"):
            return smtp
    except Exception:
        pass
    return None


def _write_digest_file(scanner_name: str, text: str) -> Path:
    """Write digest to logs/digests/ as fallback."""
    _DIGEST_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{ts}_{scanner_name}.txt"
    filepath = _DIGEST_DIR / filename
    filepath.write_text(text, encoding="utf-8")
    logger.info("Digest written to %s", filepath)
    return filepath


def _send_smtp(smtp_cfg: Dict[str, Any], subject: str, body: str) -> bool:
    """Send email via SMTP. Returns True on success."""
    msg = MIMEMultipart()
    msg["From"] = smtp_cfg["from_addr"]
    msg["To"] = smtp_cfg["to_addr"]
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    host = smtp_cfg["host"]
    port = smtp_cfg.get("port", 587)
    username = smtp_cfg.get("username", "")
    password = smtp_cfg.get("password", "")
    use_tls = smtp_cfg.get("use_tls", True)

    server = smtplib.SMTP(host, port, timeout=30)
    try:
        if use_tls:
            server.starttls()
        if username and password:
            server.login(username, password)
        server.sendmail(smtp_cfg["from_addr"], [smtp_cfg["to_addr"]], msg.as_string())
        return True
    finally:
        server.quit()


def send_digest(digest: Dict[str, Any], text: str) -> bool:
    """Deliver a scan digest via SMTP if configured, else write to file.

    Returns True on success. Never raises unhandled exceptions.
    """
    scanner = digest.get("scanner", "unknown")
    subject = f"PRIME Digest: {scanner.upper()} - {digest.get('signal_count', 0)} signals"

    smtp_cfg = _get_smtp_config()

    if smtp_cfg:
        try:
            _send_smtp(smtp_cfg, subject, text)
            logger.info("Digest sent via SMTP for %s", scanner)
            return True
        except Exception as e:
            logger.error("SMTP delivery failed for %s: %s -- falling back to file", scanner, e)

    try:
        _write_digest_file(scanner, text)
        return True
    except Exception as e:
        logger.error("Digest file write failed for %s: %s", scanner, e)
        return False


def send_signal_alert(alert: Dict[str, Any], text: str) -> bool:
    """Deliver a per-signal alert via SMTP if configured, else write to file.

    Returns True on success. Never raises unhandled exceptions.
    """
    symbol = alert.get("symbol", "???")
    strategy = alert.get("strategy", "???")
    subject = f"PRIME Signal: {symbol} ({strategy})"

    smtp_cfg = _get_smtp_config()

    if smtp_cfg:
        try:
            _send_smtp(smtp_cfg, subject, text)
            logger.info("Signal alert sent via SMTP for %s", symbol)
            return True
        except Exception as e:
            logger.error("SMTP alert failed for %s: %s -- falling back to file", symbol, e)

    try:
        _DIGEST_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = _DIGEST_DIR / f"{ts}_signal_{symbol}.txt"
        filepath.write_text(text, encoding="utf-8")
        logger.info("Signal alert written to %s", filepath)
        return True
    except Exception as e:
        logger.error("Signal alert file write failed for %s: %s", symbol, e)
        return False
