"""
Shared helpers for catalyst recall and featured-mover quality audits.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytz

logger = logging.getLogger(__name__)

NY = pytz.timezone("America/New_York")
REPO_ROOT = Path(__file__).resolve().parents[1]
TELEMETRY_ROOT = REPO_ROOT / "validation_telemetry"
REPORTS_DIR = TELEMETRY_ROOT / "reports"
RESEARCH_LOG_DIR = TELEMETRY_ROOT / "research_log"

HIT_OVERLAP_THRESHOLD = 0.35
RECENCY_WEIGHT = 0.55
SENTIMENT_WEIGHT = 0.45

_STOPWORDS = {
    "a", "an", "the", "and", "or", "for", "to", "of", "in", "on", "at", "by",
    "with", "from", "as", "is", "are", "was", "were", "be", "its", "it", "s",
    "after", "before", "over", "up", "down", "vs", "amid", "about", "into",
}


def trade_date_today() -> str:
    return resolve_audit_trade_date().strftime("%Y-%m-%d")


def resolve_audit_trade_date(now: Optional[datetime] = None) -> date:
    """Default audit session: prior weekday before 5:30pm ET; skip back over weekends."""
    now = now or datetime.now(NY)
    d = now.date()
    if d.weekday() >= 5:
        return d - timedelta(days=d.weekday() - 4)
    post_close_cutoff = NY.localize(datetime.combine(d, time(17, 30)))
    if now < post_close_cutoff:
        d = d - timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
    return d


def telemetry_day_dir(trade_date: str) -> Path:
    return TELEMETRY_ROOT / trade_date


def audit_as_of_for_trade_date(trade_date: str, now: Optional[datetime] = None) -> datetime:
    """
    When auditing before 9:15pm ET on the trade date, cap the news window at ``now``
    so we do not score against headlines that were not published yet at audit time.
    """
    now = now or datetime.now(NY)
    day = datetime.strptime(trade_date, "%Y-%m-%d").date()
    recall_cutoff = NY.localize(
        datetime.combine(day, datetime.strptime("21:15", "%H:%M").time())
    )
    if now.date() == day and now < recall_cutoff:
        return now
    return recall_cutoff


def _token_set(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]{3,}", (text or "").lower())
    return {t for t in tokens if t not in _STOPWORDS}


def token_overlap_ratio(a: str, b: str) -> float:
    sa, sb = _token_set(a), _token_set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def normalize_mover(raw: Dict[str, Any]) -> Dict[str, Any]:
    ticker = str(raw.get("ticker") or raw.get("symbol") or "").upper()
    pct = raw.get("change_percentage")
    if pct is None:
        pct = raw.get("pct", 0)
    return {
        "ticker": ticker,
        "change_percentage": float(pct or 0),
        "catalyst": str(raw.get("catalyst") or "").strip(),
        "price": raw.get("price") or raw.get("last"),
        "volume": raw.get("volume"),
    }


def _load_json(path: Path) -> Any:
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def load_movers_from_telemetry(trade_date: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    day_dir = telemetry_day_dir(trade_date)
    gainers = _load_json(day_dir / "top_gainers.json") or []
    losers = _load_json(day_dir / "top_losers.json") or []
    return [normalize_mover(m) for m in gainers], [normalize_mover(m) for m in losers]


def load_postmarket_run(trade_date: str) -> Optional[Dict[str, Any]]:
    return _load_json(telemetry_day_dir(trade_date) / "postmarket_run.json")


def bootstrap_telemetry_if_missing(trade_date: str) -> bool:
    """Build mover snapshots from live internals when production telemetry is absent."""
    day_dir = telemetry_day_dir(trade_date)
    if (day_dir / "top_gainers.json").is_file() and (day_dir / "top_losers.json").is_file():
        return False

    try:
        from market_internals import get_market_internals
        from massive_client import enrich_movers_with_news
    except ImportError as exc:
        logger.warning("Cannot bootstrap telemetry: %s", exc)
        return False

    internals = get_market_internals()
    movers = internals.get("movers") or {}
    raw_gainers = movers.get("gainers") or []
    raw_losers = movers.get("losers") or []

    def _to_quote(m: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "symbol": m.get("symbol"),
            "change_percentage": m.get("pct", 0),
            "last": m.get("last"),
        }

    gainers = enrich_movers_with_news([_to_quote(m) for m in raw_gainers], limit=len(raw_gainers) or 5)
    losers = enrich_movers_with_news([_to_quote(m) for m in raw_losers], limit=len(raw_losers) or 5)

    day_dir.mkdir(parents=True, exist_ok=True)
    with (day_dir / "top_gainers.json").open("w", encoding="utf-8") as fh:
        json.dump(gainers, fh, indent=2)
    with (day_dir / "top_losers.json").open("w", encoding="utf-8") as fh:
        json.dump(losers, fh, indent=2)

    bootstrap_meta = {
        "bootstrapped_at": datetime.now(NY).isoformat(),
        "source": "market_internals+enrich_movers_with_news",
        "trade_date": trade_date,
    }
    with (day_dir / "bootstrap_meta.json").open("w", encoding="utf-8") as fh:
        json.dump(bootstrap_meta, fh, indent=2)
    logger.info("Bootstrapped telemetry for %s (%d gainers, %d losers)", trade_date, len(gainers), len(losers))
    return True


def write_run_snapshot(
    time_period: str,
    context: Dict[str, Any],
    post_data: Dict[str, Any],
) -> None:
    """Persist production mover + post metadata after a successful email send."""
    trade_date = datetime.now(NY).strftime("%Y-%m-%d")
    day_dir = telemetry_day_dir(trade_date)
    day_dir.mkdir(parents=True, exist_ok=True)

    internals = post_data.get("market_internals") or {}
    movers = internals.get("movers") or {}
    catalyst_by_ticker: Dict[str, str] = {}
    for m in context.get("top_gainers", []) + context.get("top_losers", []):
        sym = str(m.get("ticker") or m.get("symbol") or "").upper()
        if sym and m.get("catalyst"):
            catalyst_by_ticker[sym] = str(m["catalyst"])

    def _build_side(side: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for m in movers.get(side, []):
            sym = str(m.get("symbol") or "").upper()
            out.append(
                normalize_mover(
                    {
                        "ticker": sym,
                        "change_percentage": m.get("pct", 0),
                        "price": m.get("last"),
                        "catalyst": catalyst_by_ticker.get(sym, ""),
                    }
                )
            )
        return out

    gainers = _build_side("gainers")
    losers = _build_side("losers")
    if not gainers and not losers:
        gainers = [normalize_mover(m) for m in context.get("top_gainers", [])]
        losers = [normalize_mover(m) for m in context.get("top_losers", [])]

    try:
        from massive_client import enrich_movers_with_news

        missing_g = [m for m in gainers if not m.get("catalyst")]
        missing_l = [m for m in losers if not m.get("catalyst")]
        if missing_g:
            enriched = enrich_movers_with_news(
                [{"symbol": m["ticker"], "change_percentage": m["change_percentage"]} for m in missing_g],
                limit=len(missing_g),
            )
            emap = {e["ticker"]: e.get("catalyst", "") for e in enriched}
            for m in gainers:
                if not m.get("catalyst"):
                    m["catalyst"] = emap.get(m["ticker"], "")
        if missing_l:
            enriched = enrich_movers_with_news(
                [{"symbol": m["ticker"], "change_percentage": m["change_percentage"]} for m in missing_l],
                limit=len(missing_l),
            )
            emap = {e["ticker"]: e.get("catalyst", "") for e in enriched}
            for m in losers:
                if not m.get("catalyst"):
                    m["catalyst"] = emap.get(m["ticker"], "")
    except Exception as exc:
        logger.warning("Could not enrich movers for telemetry: %s", exc)

    with (day_dir / "top_gainers.json").open("w", encoding="utf-8") as fh:
        json.dump(gainers, fh, indent=2)
    with (day_dir / "top_losers.json").open("w", encoding="utf-8") as fh:
        json.dump(losers, fh, indent=2)

    run_name = f"{time_period}_run.json"
    payload = {
        "time_period": time_period,
        "sent_at": datetime.now(NY).isoformat(),
        "trade_date": trade_date,
        "featured_stock": post_data.get("featured_stock"),
        "style": post_data.get("style"),
        "char_count": post_data.get("char_count"),
        "post": post_data.get("post") or post_data.get("short_version"),
        "market_internals_as_of": internals.get("as_of"),
    }
    with (day_dir / run_name).open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


def filter_news_last_hours(
    items: List[Dict[str, Any]],
    hours: float,
    as_of: datetime,
) -> List[Dict[str, Any]]:
    cutoff = as_of - timedelta(hours=hours)
    filtered: List[Dict[str, Any]] = []
    for item in items:
        ts = item.get("published_dt")
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = NY.localize(ts)
        else:
            ts = ts.astimezone(NY)
        if cutoff <= ts <= as_of:
            filtered.append(item)
    return filtered


def parse_news_items(raw_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    parsed: List[Dict[str, Any]] = []
    for item in raw_items:
        published = item.get("published_utc") or item.get("datetime")
        published_dt: Optional[datetime] = None
        if isinstance(published, str):
            try:
                published_dt = datetime.fromisoformat(published.replace("Z", "+00:00")).astimezone(NY)
            except ValueError:
                pass
        elif isinstance(published, (int, float)):
            published_dt = datetime.fromtimestamp(published, tz=NY)
        headline = item.get("title") or item.get("headline") or ""
        sentiment = str(item.get("sentiment") or "").lower()
        if not sentiment:
            insights = item.get("insights") or []
            if isinstance(insights, list) and insights:
                sentiment = str(insights[0].get("sentiment") or "").lower()
        parsed.append(
            {
                "headline": headline,
                "sentiment": sentiment,
                "published_dt": published_dt,
                "url": item.get("article_url") or item.get("url") or "",
                "publisher": item.get("publisher"),
            }
        )
    return parsed


def score_headline(
    headline: Dict[str, Any],
    as_of: datetime,
    want_positive: bool,
    window_hours: float = 24.0,
) -> float:
    published = headline.get("published_dt")
    if not published:
        recency = 0.0
    else:
        age_hours = max(0.0, (as_of - published).total_seconds() / 3600.0)
        recency = max(0.0, 1.0 - (age_hours / window_hours))

    sentiment = headline.get("sentiment") or ""
    if want_positive:
        sentiment_score = 1.0 if sentiment == "positive" else (0.5 if sentiment in ("neutral", "") else 0.0)
    else:
        sentiment_score = 1.0 if sentiment == "negative" else (0.5 if sentiment in ("neutral", "") else 0.0)

    return RECENCY_WEIGHT * recency + SENTIMENT_WEIGHT * sentiment_score


def rank_top_headlines(
    headlines: List[Dict[str, Any]],
    as_of: datetime,
    want_positive: bool,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    scored = []
    for h in headlines:
        scored.append({**h, "score": score_headline(h, as_of, want_positive)})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]


def classify_recall(
    pipeline_catalyst: str,
    ranked_headlines: List[Dict[str, Any]],
) -> Tuple[str, str, float]:
    """Return verdict (hit/miss), best Massive headline, overlap ratio."""
    if not pipeline_catalyst:
        best = ranked_headlines[0]["headline"] if ranked_headlines else "(no headlines)"
        return "miss", best, 0.0

    best_headline = ""
    best_overlap = 0.0
    for h in ranked_headlines:
        overlap = token_overlap_ratio(pipeline_catalyst, h.get("headline", ""))
        if overlap > best_overlap:
            best_overlap = overlap
            best_headline = h.get("headline", "")

    if not ranked_headlines:
        return "miss", "(no headlines in window)", 0.0

    verdict = "hit" if best_overlap >= HIT_OVERLAP_THRESHOLD else "miss"
    if not best_headline and ranked_headlines:
        best_headline = ranked_headlines[0].get("headline", "")
    return verdict, best_headline, best_overlap


def is_weekday_et(now: Optional[datetime] = None) -> bool:
    now = now or datetime.now(NY)
    return now.weekday() < 5


def et_window_match(now: datetime, hour: int, minute: int, window_minutes: int = 45) -> bool:
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    delta = abs((now - target).total_seconds()) / 60.0
    return delta <= window_minutes
