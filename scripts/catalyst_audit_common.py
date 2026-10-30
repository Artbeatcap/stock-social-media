"""
Shared helpers for catalyst recall and featured-mover quality audits.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional

import pytz

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from massive_client import enrich_movers_with_news, get_news_for_ticker  # noqa: E402

NY = pytz.timezone("America/New_York")
TELEMETRY_ROOT = ROOT / "validation_telemetry"
REPORTS_DIR = TELEMETRY_ROOT / "reports"
RESEARCH_LOG_DIR = TELEMETRY_ROOT / "research_log"

HIT_OVERLAP_THRESHOLD = 0.35
RECALL_AUDIT_CUTOFF = time(21, 15)  # 9:15pm ET
FEATURED_AUDIT_CUTOFF = time(17, 30)  # 5:30pm ET

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def now_ny() -> datetime:
    return datetime.now(tz=NY)


def is_weekday(d: date) -> bool:
    return d.weekday() < 5


def previous_weekday(d: date) -> date:
    cur = d - timedelta(days=1)
    while not is_weekday(cur):
        cur -= timedelta(days=1)
    return cur


def resolve_audit_trade_date(when: Optional[datetime] = None) -> date:
    """
    Trade date for quality audits. Before 5:30pm ET on a weekday, use the prior
    session (skipping weekends). After 5:30pm ET, use today if weekday else prior.
    """
    when = when or now_ny()
    local_date = when.date()
    if not is_weekday(local_date):
        return previous_weekday(local_date)
    if when.time() < FEATURED_AUDIT_CUTOFF:
        return previous_weekday(local_date)
    return local_date


def audit_as_of_for_trade_date(trade_date: date, when: Optional[datetime] = None) -> datetime:
    """
    End of the news lookback window for catalyst recall. Normally 9:15pm ET on
    trade_date; if we're still before that cutoff on trade_date, cap at `when`.
    """
    when = when or now_ny()
    end = NY.localize(datetime.combine(trade_date, RECALL_AUDIT_CUTOFF))
    if when.date() == trade_date and when.time() < RECALL_AUDIT_CUTOFF:
        return when
    if when < end:
        return when
    return end


def telemetry_dir_for(trade_date: date) -> Path:
    return TELEMETRY_ROOT / trade_date.isoformat()


def ensure_telemetry_dirs() -> None:
    for path in (TELEMETRY_ROOT, REPORTS_DIR, RESEARCH_LOG_DIR):
        path.mkdir(parents=True, exist_ok=True)


def tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def token_overlap(a: str, b: str) -> float:
    ta, tb = tokenize(a), tokenize(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def sentiment_matches(headline: str, sentiment: str, is_gainer: bool) -> bool:
    sent = (sentiment or "").lower()
    if sent == "positive":
        return is_gainer
    if sent == "negative":
        return not is_gainer
    return True


def score_headline(
    item: dict[str, Any],
    *,
    is_gainer: bool,
    window_start: datetime,
    window_end: datetime,
) -> float:
    published = item.get("published_utc") or item.get("datetime")
    ts: Optional[datetime] = None
    if isinstance(published, (int, float)):
        ts = datetime.fromtimestamp(int(published), tz=NY)
    elif isinstance(published, str):
        try:
            ts = datetime.fromisoformat(published.replace("Z", "+00:00")).astimezone(NY)
        except ValueError:
            ts = None

    if ts is None:
        recency = 0.0
    elif ts < window_start or ts > window_end:
        recency = 0.0
    else:
        span = max((window_end - window_start).total_seconds(), 1.0)
        recency = max(0.0, 1.0 - (window_end - ts).total_seconds() / span)

    sentiment_ok = sentiment_matches(
        str(item.get("headline") or ""),
        str(item.get("sentiment") or ""),
        is_gainer,
    )
    sentiment_score = 1.0 if sentiment_ok else 0.0
    return 0.55 * recency + 0.45 * sentiment_score


def load_json(path: Path) -> Any:
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")


def normalize_mover(raw: dict[str, Any], side: str) -> dict[str, Any]:
    ticker = str(raw.get("ticker") or raw.get("symbol") or "").upper()
    pct = raw.get("change_percentage")
    if pct is None:
        pct = raw.get("pct")
    return {
        "ticker": ticker,
        "change_percentage": float(pct or 0.0),
        "catalyst": str(raw.get("catalyst") or raw.get("why") or "").strip(),
        "side": side,
    }


def movers_from_telemetry(trade_date: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    base = telemetry_dir_for(trade_date)
    gainers = load_json(base / "top_gainers.json") or []
    losers = load_json(base / "top_losers.json") or []
    post = load_json(base / "postmarket_run.json") or {}
    if gainers or losers:
        return (
            [normalize_mover(m, "gainer") for m in gainers],
            [normalize_mover(m, "loser") for m in losers],
            post if isinstance(post, dict) else {},
        )
    return [], [], post if isinstance(post, dict) else {}


def bootstrap_telemetry_if_missing(trade_date: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    gainers, losers, post = movers_from_telemetry(trade_date)
    if gainers or losers:
        return gainers, losers, post

    try:
        from market_internals import get_market_internals
    except Exception:
        get_market_internals = None  # type: ignore

    raw_gainers: list[dict[str, Any]] = []
    raw_losers: list[dict[str, Any]] = []
    if get_market_internals is not None:
        internals = get_market_internals()
        movers = (internals or {}).get("movers") or {}
        raw_gainers = list(movers.get("gainers") or [])
        raw_losers = list(movers.get("losers") or [])

    if not raw_gainers and not raw_losers:
        from massive_client import fetch_top_movers_with_news

        combined = fetch_top_movers_with_news(limit=10)
        raw_gainers = [m for m in combined if float(m.get("change_percentage") or 0) > 0][:5]
        raw_losers = sorted(
            [m for m in combined if float(m.get("change_percentage") or 0) < 0],
            key=lambda x: abs(float(x.get("change_percentage") or 0)),
            reverse=True,
        )[:5]
    else:
        enriched_g = enrich_movers_with_news(
            [
                {
                    "symbol": m.get("symbol"),
                    "change_percentage": m.get("pct"),
                    "last": m.get("last"),
                }
                for m in raw_gainers
            ],
            limit=len(raw_gainers) or 5,
        )
        enriched_l = enrich_movers_with_news(
            [
                {
                    "symbol": m.get("symbol"),
                    "change_percentage": m.get("pct"),
                    "last": m.get("last"),
                }
                for m in raw_losers
            ],
            limit=len(raw_losers) or 5,
        )
        raw_gainers, raw_losers = enriched_g, enriched_l

    gainers = [normalize_mover(m, "gainer") for m in raw_gainers[:5]]
    losers = [normalize_mover(m, "loser") for m in raw_losers[:5]]
    featured = max(gainers + losers, key=lambda m: abs(m["change_percentage"]), default=None)
    post = {
        "trade_date": trade_date.isoformat(),
        "bootstrapped": True,
        "featured_stock": featured["ticker"] if featured else None,
    }
    return gainers, losers, post


def news_in_window(ticker: str, window_start: datetime, window_end: datetime, limit: int = 20) -> list[dict[str, Any]]:
    items = get_news_for_ticker(ticker, limit=limit)
    filtered: list[dict[str, Any]] = []
    for item in items:
        ts_val = item.get("datetime")
        if isinstance(ts_val, (int, float)):
            ts = datetime.fromtimestamp(int(ts_val), tz=NY)
        else:
            continue
        if window_start <= ts <= window_end:
            filtered.append(item)
    return filtered


def rank_top_headlines(
    items: Iterable[dict[str, Any]],
    *,
    is_gainer: bool,
    window_start: datetime,
    window_end: datetime,
    top_n: int = 5,
) -> list[dict[str, Any]]:
    scored = []
    for item in items:
        score = score_headline(
            item,
            is_gainer=is_gainer,
            window_start=window_start,
            window_end=window_end,
        )
        scored.append({**item, "_score": score})
    scored.sort(key=lambda x: x["_score"], reverse=True)
    return scored[:top_n]


def best_match(pipeline_catalyst: str, headlines: list[dict[str, Any]]) -> tuple[Optional[dict[str, Any]], float]:
    if not pipeline_catalyst or not headlines:
        return None, 0.0
    best_item: Optional[dict[str, Any]] = None
    best_overlap = 0.0
    for item in headlines:
        overlap = token_overlap(pipeline_catalyst, str(item.get("headline") or ""))
        if overlap > best_overlap:
            best_overlap = overlap
            best_item = item
    return best_item, best_overlap


def write_run_snapshot(
    *,
    trade_date: date,
    run_kind: str,
    context: dict[str, Any],
    post_data: dict[str, Any],
    sent_ok: bool,
) -> None:
    """Persist mover catalyst strings after a successful email send."""
    if not sent_ok:
        return
    ensure_telemetry_dirs()
    base = telemetry_dir_for(trade_date)
    base.mkdir(parents=True, exist_ok=True)

    gainers = context.get("top_gainers") or []
    losers = context.get("top_losers") or []
    internals = post_data.get("market_internals") or context.get("market_internals") or {}
    if internals.get("movers") and (not gainers and not losers):
        movers = internals["movers"]
        raw_g = movers.get("gainers") or []
        raw_l = movers.get("losers") or []
        gainers = enrich_movers_with_news(
            [{"symbol": m.get("symbol"), "change_percentage": m.get("pct"), "last": m.get("last")} for m in raw_g],
            limit=5,
        )
        losers = enrich_movers_with_news(
            [{"symbol": m.get("symbol"), "change_percentage": m.get("pct"), "last": m.get("last")} for m in raw_l],
            limit=5,
        )

    save_json(base / "top_gainers.json", gainers[:5])
    save_json(base / "top_losers.json", losers[:5])
    payload = {
        "trade_date": trade_date.isoformat(),
        "run_kind": run_kind,
        "sent_at": now_ny().isoformat(),
        "featured_stock": post_data.get("featured_stock"),
        "featured_mover": context.get("featured_mover"),
        "post_style": post_data.get("style"),
        "char_count": post_data.get("char_count"),
    }
    filename = f"{run_kind}_run.json"
    save_json(base / filename, payload)
