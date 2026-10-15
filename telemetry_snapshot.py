"""
Persist production run snapshots for catalyst quality audits.

Writes under validation_telemetry/YYYY-MM-DD/ on successful email send.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytz

logger = logging.getLogger(__name__)

NY = pytz.timezone("America/New_York")
REPO_ROOT = Path(__file__).resolve().parent
TELEMETRY_ROOT = REPO_ROOT / "validation_telemetry"


def _mover_row(
    ticker: str,
    change_pct: float,
    catalyst: str = "",
    price: Optional[float] = None,
    volume: Optional[int] = None,
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "ticker": ticker.upper(),
        "change_percentage": float(change_pct),
        "catalyst": catalyst or "",
    }
    if price is not None:
        row["price"] = price
    if volume is not None:
        row["volume"] = volume
    return row


def _internals_movers_to_rows(
    internals: Dict[str, Any],
    context: Dict[str, Any],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Prefer market_internals movers; merge catalyst strings from context lists."""
    catalyst_by_ticker: Dict[str, str] = {}
    for side in ("top_gainers", "top_losers"):
        for m in context.get(side) or []:
            t = str(m.get("ticker") or m.get("symbol") or "").upper()
            if t and m.get("catalyst"):
                catalyst_by_ticker[t] = str(m.get("catalyst"))

    movers = (internals or {}).get("movers") or {}
    gainers_out: List[Dict[str, Any]] = []
    losers_out: List[Dict[str, Any]] = []

    for m in movers.get("gainers") or []:
        t = str(m.get("symbol") or m.get("ticker") or "").upper()
        if not t:
            continue
        gainers_out.append(
            _mover_row(
                t,
                float(m.get("pct") or m.get("change_percentage") or 0),
                catalyst_by_ticker.get(t, str(m.get("catalyst") or "")),
                m.get("last") or m.get("price"),
                m.get("volume"),
            )
        )
    for m in movers.get("losers") or []:
        t = str(m.get("symbol") or m.get("ticker") or "").upper()
        if not t:
            continue
        losers_out.append(
            _mover_row(
                t,
                float(m.get("pct") or m.get("change_percentage") or 0),
                catalyst_by_ticker.get(t, str(m.get("catalyst") or "")),
                m.get("last") or m.get("price"),
                m.get("volume"),
            )
        )

    if gainers_out or losers_out:
        return gainers_out, losers_out

    return list(context.get("top_gainers") or []), list(context.get("top_losers") or [])


def write_run_snapshot(
    time_period: str,
    context: Dict[str, Any],
    post_data: Dict[str, Any],
    *,
    emailed: bool = True,
) -> Optional[Path]:
    """
    Write JSON snapshots for a successful premarket/postmarket send.

    Returns the day directory path, or None on failure.
    """
    if not emailed:
        return None

    try:
        now = datetime.now(NY)
        day_dir = TELEMETRY_ROOT / now.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)

        internals = post_data.get("market_internals") or {}
        gainers, losers = _internals_movers_to_rows(internals, context)

        run_name = f"{time_period}_run.json"
        payload = {
            "time_period": time_period,
            "emailed_at": now.isoformat(),
            "featured_stock": post_data.get("featured_stock"),
            "post": post_data.get("post"),
            "style": post_data.get("style"),
            "char_count": post_data.get("char_count"),
            "top_gainers": gainers,
            "top_losers": losers,
            "context_timestamp": context.get("timestamp"),
            "market_internals": internals if internals else None,
        }
        (day_dir / run_name).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        (day_dir / "top_gainers.json").write_text(json.dumps(gainers, indent=2), encoding="utf-8")
        (day_dir / "top_losers.json").write_text(json.dumps(losers, indent=2), encoding="utf-8")

        logger.info("Wrote validation telemetry to %s", day_dir)
        return day_dir
    except Exception as exc:
        logger.warning("Failed to write validation telemetry: %s", exc)
        return None
