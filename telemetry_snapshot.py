"""Write per-run telemetry snapshots after successful email delivery."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytz

from validation_telemetry import day_dir, ensure_telemetry_dirs, is_us_market_weekday

logger = logging.getLogger(__name__)
NY = pytz.timezone("America/New_York")


def _normalize_mover_row(raw: Dict[str, Any], catalyst: str = "") -> Dict[str, Any]:
    ticker = str(raw.get("ticker") or raw.get("symbol") or "").upper()
    pct = raw.get("change_percentage")
    if pct is None:
        pct = raw.get("pct")
    return {
        "ticker": ticker,
        "change_percentage": float(pct or 0),
        "price": float(raw.get("price") or raw.get("last") or 0),
        "catalyst": catalyst or str(raw.get("catalyst") or ""),
        "volume": int(raw.get("volume") or 0),
    }


def _catalyst_by_ticker(movers: List[Dict[str, Any]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for row in movers:
        ticker = str(row.get("ticker") or row.get("symbol") or "").upper()
        if ticker and row.get("catalyst"):
            out[ticker] = str(row["catalyst"])
    return out


def _movers_from_context(context: Dict[str, Any]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    gainers = [_normalize_mover_row(m) for m in context.get("top_gainers") or []]
    losers = [_normalize_mover_row(m) for m in context.get("top_losers") or []]
    return gainers, losers


def _movers_from_internals(internals: Dict[str, Any]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    movers = internals.get("movers") or {}
    gainers = [
        _normalize_mover_row({"symbol": m["symbol"], "pct": m["pct"], "last": m.get("last")})
        for m in movers.get("gainers") or []
        if m.get("symbol")
    ]
    losers = [
        _normalize_mover_row({"symbol": m["symbol"], "pct": m["pct"], "last": m.get("last")})
        for m in movers.get("losers") or []
        if m.get("symbol")
    ]
    return gainers, losers


def _merge_catalysts(
    gainers: List[Dict[str, Any]],
    losers: List[Dict[str, Any]],
    catalyst_map: Dict[str, str],
) -> None:
    for row in gainers + losers:
        ticker = row.get("ticker", "")
        if ticker in catalyst_map and not row.get("catalyst"):
            row["catalyst"] = catalyst_map[ticker]


def write_run_snapshot(
    time_period: str,
    context: Dict[str, Any],
    post_data: Dict[str, Any],
    trade_date: Optional[datetime] = None,
) -> Path:
    """
    Persist telemetry for a successful premarket/postmarket email send.

    Post-market runs prefer ``market_internals`` movers and merge pipeline
    catalyst strings from context enrichment by ticker.
    """
    ensure_telemetry_dirs()
    now = trade_date or datetime.now(tz=NY)
    if now.tzinfo is None:
        now = NY.localize(now)
    else:
        now = now.astimezone(NY)

    session_date = now.date()
    if not is_us_market_weekday(session_date):
        logger.info("Skipping telemetry snapshot on non-weekday %s", session_date)
        return day_dir(session_date)

    out_dir = day_dir(session_date)
    out_dir.mkdir(parents=True, exist_ok=True)

    catalyst_map = _catalyst_by_ticker(
        list(context.get("top_gainers") or []) + list(context.get("top_losers") or [])
    )
    featured = context.get("featured_mover") or {}
    if featured.get("ticker") and featured.get("catalyst"):
        catalyst_map[str(featured["ticker"]).upper()] = str(featured["catalyst"])

    internals = post_data.get("market_internals") or {}
    if time_period == "postmarket" and internals.get("movers"):
        gainers, losers = _movers_from_internals(internals)
    else:
        gainers, losers = _movers_from_context(context)

    _merge_catalysts(gainers, losers, catalyst_map)

    try:
        from massive_client import enrich_movers_with_news

        combined = enrich_movers_with_news(gainers + losers, limit=len(gainers) + len(losers))
        enrich_map = {r["ticker"]: r.get("catalyst", "") for r in combined}
        for row in gainers + losers:
            if not row.get("catalyst") and enrich_map.get(row["ticker"]):
                row["catalyst"] = enrich_map[row["ticker"]]
    except Exception as exc:
        logger.warning("Could not enrich movers with Massive news: %s", exc)

    payload = {
        "time_period": time_period,
        "sent_at_et": now.strftime("%Y-%m-%d %H:%M:%S ET"),
        "featured_stock": post_data.get("featured_stock")
        or (featured.get("ticker") if featured else None),
        "featured_mover": _normalize_mover_row(featured) if featured else None,
        "post_style": post_data.get("style"),
        "char_count": post_data.get("char_count"),
        "top_gainers": gainers,
        "top_losers": losers,
        "market_internals": internals or None,
    }

    run_name = f"{time_period}_run.json"
    run_path = out_dir / run_name
    with run_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)

    with (out_dir / "top_gainers.json").open("w", encoding="utf-8") as fh:
        json.dump(gainers, fh, indent=2)
    with (out_dir / "top_losers.json").open("w", encoding="utf-8") as fh:
        json.dump(losers, fh, indent=2)

    logger.info("Wrote validation telemetry to %s", out_dir)
    return out_dir


def load_json(path: Path) -> Any:
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)
