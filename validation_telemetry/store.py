"""Read/write production telemetry snapshots for quality audits."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pytz

logger = logging.getLogger(__name__)

NY = pytz.timezone("America/New_York")

# Repo-relative default; override with VALIDATION_TELEMETRY_DIR on production hosts.
_DEFAULT_ROOT = Path(__file__).resolve().parent


def telemetry_root() -> Path:
    configured = os.getenv("VALIDATION_TELEMETRY_DIR")
    if configured:
        return Path(configured)
    return _DEFAULT_ROOT


def daily_dir() -> Path:
    path = telemetry_root() / "daily"
    path.mkdir(parents=True, exist_ok=True)
    return path


def reports_dir() -> Path:
    path = telemetry_root() / "reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def session_date_et(when: Optional[datetime] = None) -> str:
    dt = when or datetime.now(tz=NY)
    if dt.tzinfo is None:
        dt = NY.localize(dt)
    else:
        dt = dt.astimezone(NY)
    return dt.strftime("%Y-%m-%d")


def daily_path(session_date: str, time_period: str) -> Path:
    return daily_dir() / f"{session_date}_{time_period}.json"


def save_run_telemetry(
    *,
    time_period: str,
    context: dict[str, Any],
    post_data: dict[str, Any],
    email_sent: bool,
    when: Optional[datetime] = None,
) -> Path:
    """Persist one pipeline run for later catalyst-quality audits."""
    dt = when or datetime.now(tz=NY)
    if dt.tzinfo is None:
        dt = NY.localize(dt)
    else:
        dt = dt.astimezone(NY)

    session = session_date_et(dt)
    payload: dict[str, Any] = {
        "session_date": session,
        "captured_at": dt.isoformat(),
        "time_period": time_period,
        "email_sent": email_sent,
        "top_gainers": _normalize_movers(context.get("top_gainers") or []),
        "top_losers": _normalize_movers(context.get("top_losers") or []),
        "featured_mover": _normalize_mover(context.get("featured_mover")),
        "featured_stock": post_data.get("featured_stock"),
        "post": post_data.get("post") or post_data.get("short_version"),
        "style": post_data.get("style"),
        "market_internals": post_data.get("market_internals"),
    }

    path = daily_path(session, time_period)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    logger.info("Wrote validation telemetry to %s", path)
    return path


def load_daily_telemetry(session_date: str, time_period: str = "postmarket") -> Optional[dict[str, Any]]:
    path = daily_path(session_date, time_period)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Failed to read telemetry %s: %s", path, exc)
        return None


def _normalize_movers(movers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for mover in movers:
        normalized = _normalize_mover(mover)
        if normalized:
            out.append(normalized)
    return out


def _normalize_mover(mover: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not mover:
        return None
    ticker = str(mover.get("ticker") or mover.get("symbol") or "").upper()
    if not ticker:
        return None
    return {
        "ticker": ticker,
        "change_percentage": float(mover.get("change_percentage") or mover.get("pct") or 0),
        "price": mover.get("price") or mover.get("last"),
        "volume": mover.get("volume"),
        "catalyst": mover.get("catalyst") or mover.get("why") or "",
    }
