"""Read/write production telemetry snapshots for catalyst quality audits."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytz

logger = logging.getLogger(__name__)

NY = pytz.timezone("America/New_York")
REPO_ROOT = Path(__file__).resolve().parent.parent


def telemetry_root() -> Path:
    configured = os.getenv("VALIDATION_TELEMETRY_DIR")
    if configured:
        return Path(configured)
    return REPO_ROOT / "validation_telemetry"


def day_dir(trade_date: Optional[str] = None) -> Path:
    date_str = trade_date or session_date_et()
    return telemetry_root() / date_str


def reports_dir() -> Path:
    path = telemetry_root() / "reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def research_log_dir() -> Path:
    path = telemetry_root() / "research_log"
    path.mkdir(parents=True, exist_ok=True)
    return path


def session_date_et(when: Optional[datetime] = None) -> str:
    dt = when or datetime.now(tz=NY)
    if dt.tzinfo is None:
        dt = NY.localize(dt)
    else:
        dt = dt.astimezone(NY)
    return dt.strftime("%Y-%m-%d")


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    logger.info("Wrote telemetry snapshot: %s", path)


def _catalyst_by_ticker(movers: List[Dict[str, Any]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for mover in movers:
        ticker = str(mover.get("ticker") or mover.get("symbol") or "").upper()
        catalyst = str(mover.get("catalyst") or "").strip()
        if ticker and catalyst:
            out[ticker] = catalyst
    return out


def _internals_movers_to_telemetry(
    internals_movers: List[Dict[str, Any]],
    catalyst_by_ticker: Dict[str, str],
) -> List[Dict[str, Any]]:
    """Normalize market_internals movers and attach pipeline catalysts when known."""
    normalized: List[Dict[str, Any]] = []
    for mover in internals_movers:
        ticker = str(mover.get("symbol") or mover.get("ticker") or "").upper()
        if not ticker:
            continue
        normalized.append(
            {
                "ticker": ticker,
                "change_percentage": mover.get("pct", mover.get("change_percentage", 0)),
                "price": mover.get("last", mover.get("price")),
                "catalyst": catalyst_by_ticker.get(ticker, ""),
                "volume": mover.get("volume"),
            }
        )
    return normalized


def _resolve_movers_for_snapshot(
    time_period: str,
    context: Dict[str, Any],
    post_data: Dict[str, Any],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Pick movers shown in production and attach catalyst strings when available."""
    catalyst_map = _catalyst_by_ticker(
        (context.get("top_gainers") or []) + (context.get("top_losers") or [])
    )
    featured = context.get("featured_mover") or {}
    featured_ticker = str(featured.get("ticker") or "").upper()
    if featured_ticker and featured.get("catalyst"):
        catalyst_map.setdefault(featured_ticker, str(featured.get("catalyst")))

    internals = post_data.get("market_internals") or {}
    movers_block = internals.get("movers") or {}
    if time_period == "postmarket" and movers_block:
        gainers = _internals_movers_to_telemetry(
            movers_block.get("gainers") or [], catalyst_map
        )
        losers = _internals_movers_to_telemetry(
            movers_block.get("losers") or [], catalyst_map
        )
        if gainers or losers:
            return gainers, losers

    return context.get("top_gainers") or [], context.get("top_losers") or []


def write_run_snapshot(
    time_period: str,
    context: Dict[str, Any],
    post_data: Dict[str, Any],
    *,
    trade_date: Optional[str] = None,
    email_sent: bool = True,
) -> Path:
    """Persist movers, catalysts, and post metadata after a pipeline run."""
    date_str = trade_date or session_date_et()
    out_dir = day_dir(date_str)
    top_gainers, top_losers = _resolve_movers_for_snapshot(time_period, context, post_data)
    payload = {
        "date": date_str,
        "time_period": time_period,
        "recorded_at": datetime.now(NY).isoformat(timespec="seconds"),
        "email_sent": email_sent,
        "top_gainers": top_gainers,
        "top_losers": top_losers,
        "featured_mover": context.get("featured_mover"),
        "featured_stock": post_data.get("featured_stock"),
        "post": post_data.get("post"),
        "style": post_data.get("style"),
        "market_internals": post_data.get("market_internals"),
    }
    path = out_dir / f"{time_period}_run.json"
    _write_json(path, payload)

    _write_json(out_dir / "top_gainers.json", {"date": date_str, "movers": top_gainers})
    _write_json(out_dir / "top_losers.json", {"date": date_str, "movers": top_losers})
    return path


def save_run_telemetry(
    *,
    time_period: str,
    context: dict[str, Any],
    post_data: dict[str, Any],
    email_sent: bool,
    when: Optional[datetime] = None,
) -> Path:
    """Alias used by twitter_auto_emailer; delegates to write_run_snapshot."""
    trade_date = session_date_et(when)
    return write_run_snapshot(
        time_period,
        context,
        post_data,
        trade_date=trade_date,
        email_sent=email_sent,
    )


def load_run_snapshot(
    time_period: str = "postmarket",
    *,
    trade_date: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    path = day_dir(trade_date) / f"{time_period}_run.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Failed to read telemetry %s: %s", path, exc)
        return None


def load_movers(side: str, *, trade_date: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load top_gainers or top_losers for a trade date."""
    filename = f"top_{side}.json" if side in ("gainers", "losers") else side
    path = day_dir(trade_date) / filename
    if not path.exists():
        run = load_run_snapshot(trade_date=trade_date)
        if not run:
            return []
        key = "top_gainers" if "gainers" in side else "top_losers"
        return run.get(key) or []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Failed to read movers %s: %s", path, exc)
        return []
    return data.get("movers") or data.get(side) or []


def load_featured_stock(*, trade_date: Optional[str] = None) -> Optional[str]:
    run = load_run_snapshot("postmarket", trade_date=trade_date)
    if not run:
        return None
    featured = run.get("featured_stock")
    return str(featured).upper() if featured else None
