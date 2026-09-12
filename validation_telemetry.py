"""Read/write production validation telemetry for catalyst quality audits."""

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
REPO_ROOT = Path(__file__).resolve().parent
TELEMETRY_ROOT = Path(
    os.getenv("VALIDATION_TELEMETRY_DIR", REPO_ROOT / "validation_telemetry")
)


def _today() -> str:
    return datetime.now(NY).date().isoformat()


def day_dir(trade_date: Optional[str] = None) -> Path:
    return TELEMETRY_ROOT / (trade_date or _today())


def reports_dir() -> Path:
    path = TELEMETRY_ROOT / "reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def research_log_dir() -> Path:
    path = TELEMETRY_ROOT / "research_log"
    path.mkdir(parents=True, exist_ok=True)
    return path


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
    """Pick the movers shown in production and attach catalyst strings when available."""
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
) -> Path:
    """Persist movers, catalysts, and post metadata after a successful email run."""
    date_str = trade_date or _today()
    out_dir = day_dir(date_str)
    top_gainers, top_losers = _resolve_movers_for_snapshot(time_period, context, post_data)
    payload = {
        "date": date_str,
        "time_period": time_period,
        "recorded_at": datetime.now(NY).isoformat(timespec="seconds"),
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

    gainers_path = out_dir / "top_gainers.json"
    losers_path = out_dir / "top_losers.json"
    _write_json(gainers_path, {"date": date_str, "movers": payload["top_gainers"]})
    _write_json(losers_path, {"date": date_str, "movers": payload["top_losers"]})
    return path


def load_run_snapshot(
    time_period: str = "postmarket",
    *,
    trade_date: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    path = day_dir(trade_date) / f"{time_period}_run.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


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
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("movers") or data.get(side) or []


def load_featured_stock(*, trade_date: Optional[str] = None) -> Optional[str]:
    run = load_run_snapshot("postmarket", trade_date=trade_date)
    if not run:
        return None
    featured = run.get("featured_stock")
    return str(featured).upper() if featured else None


def resolve_audit_trade_date() -> str:
    """Pick the trade date to audit based on current ET time."""
    from datetime import timedelta

    now = datetime.now(NY)
    # Before 9:15pm ET, today's postmarket telemetry is not ready yet.
    if now.hour < 21 or (now.hour == 21 and now.minute < 15):
        target = (now - timedelta(days=1)).date()
    else:
        target = now.date()
    # Weekend runs should audit the last weekday session.
    while target.weekday() >= 5:
        target -= timedelta(days=1)
    return target.isoformat()


def bootstrap_telemetry_if_missing(trade_date: Optional[str] = None) -> Optional[Path]:
    """
    Build telemetry from market_internals + Massive catalyst enrichment when
    production snapshots are unavailable (e.g. cloud agent without prod sync).
    """
    date_str = trade_date or resolve_audit_trade_date()
    if load_movers("gainers", trade_date=date_str) or load_movers("losers", trade_date=date_str):
        return None

    try:
        from market_internals import get_market_internals
        from massive_client import enrich_movers_with_news
    except ImportError as exc:
        logger.warning("Cannot bootstrap telemetry: %s", exc)
        return None

    internals = get_market_internals()
    movers = internals.get("movers") or {}
    gainers = enrich_movers_with_news(movers.get("gainers") or [], limit=10)
    losers = enrich_movers_with_news(movers.get("losers") or [], limit=10)
    if not gainers and not losers:
        return None

    featured_mover = next((g for g in gainers if g.get("catalyst")), gainers[0] if gainers else None)
    featured_stock = str((featured_mover or {}).get("ticker") or "").upper() or None

    context = {
        "top_gainers": gainers,
        "top_losers": losers,
        "featured_mover": featured_mover,
    }
    post_data = {
        "featured_stock": featured_stock,
        "post": f"Bootstrapped telemetry for catalyst audit ({date_str})",
        "style": "bootstrap",
        "char_count": 0,
        "market_internals": internals,
    }
    path = write_run_snapshot("postmarket", context, post_data, trade_date=date_str)
    logger.info("Bootstrapped validation telemetry for %s", date_str)
    return path
