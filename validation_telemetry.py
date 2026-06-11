"""
Persistence helpers for production validation telemetry.

Daily JSON snapshots land in ``validation_telemetry/YYYY-MM-DD.json`` and are
updated when pre-market or post-market emails send successfully.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import pytz

logger = logging.getLogger(__name__)

NY = pytz.timezone("America/New_York")
TELEMETRY_DIR = Path(__file__).resolve().parent / "validation_telemetry"
REPORTS_DIR = TELEMETRY_DIR / "reports"
RESEARCH_DIR = TELEMETRY_DIR / "research"


def ensure_dirs() -> None:
    for path in (TELEMETRY_DIR, REPORTS_DIR, RESEARCH_DIR):
        path.mkdir(parents=True, exist_ok=True)


def today_iso() -> str:
    return datetime.now(tz=NY).date().isoformat()


def telemetry_path(session_date: Optional[str] = None) -> Path:
    ensure_dirs()
    return TELEMETRY_DIR / f"{session_date or today_iso()}.json"


def load_telemetry(session_date: Optional[str] = None) -> Dict[str, Any]:
    path = telemetry_path(session_date)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to read telemetry %s: %s", path, exc)
        return {}


def save_telemetry(payload: Dict[str, Any], session_date: Optional[str] = None) -> Path:
    ensure_dirs()
    path = telemetry_path(session_date)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def record_email_send(
    time_period: str,
    context: Dict[str, Any],
    post_data: Dict[str, Any],
    session_date: Optional[str] = None,
) -> Optional[Path]:
    """Merge a successful email send into the day's telemetry snapshot."""
    session = session_date or today_iso()
    payload = load_telemetry(session)
    payload.setdefault("date", session)
    payload["top_gainers"] = context.get("top_gainers", [])
    payload["top_losers"] = context.get("top_losers", [])
    payload.setdefault("posts", {})
    payload["posts"][time_period] = {
        "sent_at": datetime.now(tz=NY).isoformat(),
        "featured_stock": post_data.get("featured_stock"),
        "post": post_data.get("post", ""),
        "style": post_data.get("style", ""),
        "char_count": post_data.get("char_count", 0),
    }
    if post_data.get("market_internals"):
        payload["posts"][time_period]["market_internals"] = post_data["market_internals"]
    payload["updated_at"] = datetime.now(tz=NY).isoformat()
    path = save_telemetry(payload, session)
    logger.info("Wrote validation telemetry to %s", path)
    return path
