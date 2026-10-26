"""
Shared helpers for catalyst recall and featured-mover quality audits.

Telemetry layout (per trade date):
  validation_telemetry/YYYY-MM-DD/top_gainers.json
  validation_telemetry/YYYY-MM-DD/top_losers.json
  validation_telemetry/YYYY-MM-DD/postmarket_run.json
  validation_telemetry/YYYY-MM-DD/premarket_run.json
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pytz

logger = logging.getLogger(__name__)

NY = pytz.timezone("America/New_York")
REPO_ROOT = Path(__file__).resolve().parents[1]
TELEMETRY_ROOT = REPO_ROOT / "validation_telemetry"
REPORTS_DIR = TELEMETRY_ROOT / "reports"
RESEARCH_LOG_DIR = TELEMETRY_ROOT / "research_log"

RECALL_HIT_OVERLAP = 0.35
RECALL_RECENCY_WEIGHT = 0.55
RECALL_SENTIMENT_WEIGHT = 0.45

FEATURED_RECALL_WINDOW = timedelta(days=7)
CATALYST_NEWS_WINDOW = timedelta(hours=24)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _parse_trade_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return datetime.strptime(value, "%Y-%m-%d").date()


def is_us_market_weekday(d: date) -> bool:
    return d.weekday() < 5


def prior_us_session(from_day: date) -> date:
    d = from_day - timedelta(days=1)
    while not is_us_market_weekday(d):
        d -= timedelta(days=1)
    return d


def resolve_audit_trade_date(now: Optional[datetime] = None) -> date:
    """
    Default trade date for audits.

    Before 5:30pm ET on a weekday, use the prior US session (post-market email
    for "today" has not run yet). Weekends map to Friday.
    """
    now = now or datetime.now(tz=NY)
    local_date = now.date()
    if not is_us_market_weekday(local_date):
        while not is_us_market_weekday(local_date):
            local_date -= timedelta(days=1)
        return local_date

    if now.time() < time(17, 30):
        return prior_us_session(local_date)
    return local_date


def audit_as_of_for_trade_date(trade_date: date, now: Optional[datetime] = None) -> datetime:
    """
    Upper bound for news lookbacks on `trade_date`.

    When running before 9:15pm ET on the same calendar day as the session, cap
    at `now` so we do not score against headlines that appear after the audit
    would normally run.
    """
    now = now or datetime.now(tz=NY)
    session_close = NY.localize(datetime.combine(trade_date, time(16, 15)))
    recall_cutoff = NY.localize(datetime.combine(trade_date, time(21, 15)))
    if now.date() == trade_date and now < recall_cutoff:
        return now
    return max(session_close, recall_cutoff)


def telemetry_day_dir(trade_date: date | str) -> Path:
    d = _parse_trade_date(trade_date) if isinstance(trade_date, str) else trade_date
    return TELEMETRY_ROOT / d.isoformat()


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")


def normalize_mover_row(raw: Dict[str, Any], direction: str) -> Dict[str, Any]:
    ticker = str(raw.get("ticker") or raw.get("symbol") or "").upper()
    pct = raw.get("change_percentage")
    if pct is None:
        pct = raw.get("pct")
    try:
        change_percentage = float(pct)
    except (TypeError, ValueError):
        change_percentage = 0.0
    catalyst = raw.get("catalyst") or raw.get("why") or ""
    return {
        "ticker": ticker,
        "change_percentage": change_percentage,
        "catalyst": str(catalyst).strip(),
        "direction": direction,
    }


def merge_catalyst_from_enriched(
    movers: List[Dict[str, Any]],
    enriched: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    by_ticker = {
        str(m.get("ticker") or m.get("symbol") or "").upper(): m for m in enriched
    }
    out: List[Dict[str, Any]] = []
    for row in movers:
        merged = dict(row)
        enrich = by_ticker.get(row.get("ticker", ""))
        if enrich and not merged.get("catalyst"):
            merged["catalyst"] = str(enrich.get("catalyst") or "").strip()
        out.append(merged)
    return out


def load_movers_for_trade_date(trade_date: date) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
    """
    Load gainers/losers for the session. Prefer persisted telemetry; fall back to
    market_internals + Massive headline enrichment.
    """
    day_dir = telemetry_day_dir(trade_date)
    gainers_raw = _read_json(day_dir / "top_gainers.json")
    losers_raw = _read_json(day_dir / "top_losers.json")
    source = "telemetry"

    if gainers_raw is None and losers_raw is None:
        gainers_raw, losers_raw = bootstrap_telemetry_if_missing(trade_date)
        source = "bootstrap"

    gainers = [
        normalize_mover_row(m, "gainer")
        for m in (gainers_raw or [])
        if isinstance(m, dict)
    ]
    losers = [
        normalize_mover_row(m, "loser")
        for m in (losers_raw or [])
        if isinstance(m, dict)
    ]
    return gainers, losers, source


def bootstrap_telemetry_if_missing(trade_date: date) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Build mover lists from market_internals when production snapshots are absent."""
    from massive_client import enrich_movers_with_news

    try:
        from market_internals import fetch_movers_and_breadth
    except ImportError as exc:
        logger.error("market_internals unavailable for bootstrap: %s", exc)
        return [], []

    mb = fetch_movers_and_breadth(n=5)
    movers = mb.get("movers") or {}
    gainers = [
        {
            "ticker": m.get("symbol"),
            "change_percentage": m.get("pct"),
            "last": m.get("last"),
        }
        for m in movers.get("gainers") or []
    ]
    losers = [
        {
            "ticker": m.get("symbol"),
            "change_percentage": m.get("pct"),
            "last": m.get("last"),
        }
        for m in movers.get("losers") or []
    ]
    combined = gainers + losers
    enriched = enrich_movers_with_news(
        [
            {
                "symbol": r["ticker"],
                "ticker": r["ticker"],
                "change_percentage": r.get("change_percentage"),
                "last": r.get("last"),
            }
            for r in combined
        ],
        limit=len(combined),
    )
    gainers_out = merge_catalyst_from_enriched(
        [normalize_mover_row(g, "gainer") for g in gainers],
        enriched,
    )
    losers_out = merge_catalyst_from_enriched(
        [normalize_mover_row(l, "loser") for l in losers],
        enriched,
    )
    day_dir = telemetry_day_dir(trade_date)
    _write_json(day_dir / "top_gainers.json", gainers_out)
    _write_json(day_dir / "top_losers.json", losers_out)
    _write_json(
        day_dir / "bootstrap_meta.json",
        {
            "trade_date": trade_date.isoformat(),
            "session_date": (mb.get("breadth") or {}).get("session_date"),
            "bootstrapped_at": datetime.now(tz=NY).isoformat(),
        },
    )
    return gainers_out, losers_out


def write_run_snapshot(
    time_period: str,
    trade_date: date,
    post_data: Dict[str, Any],
    context: Dict[str, Any],
) -> None:
    """Persist telemetry after a successful email send."""
    day_dir = telemetry_day_dir(trade_date)
    day_dir.mkdir(parents=True, exist_ok=True)

    internals = post_data.get("market_internals") or {}
    movers = internals.get("movers") or {}
    gainers_ctx = context.get("top_gainers") or []
    losers_ctx = context.get("top_losers") or []

    gainers_rows = _movers_from_internals_or_context(movers, gainers_ctx, "gainer")
    losers_rows = _movers_from_internals_or_context(movers, losers_ctx, "loser")

    if gainers_rows:
        _write_json(day_dir / "top_gainers.json", gainers_rows)
    if losers_rows:
        _write_json(day_dir / "top_losers.json", losers_rows)

    run_payload = {
        "time_period": time_period,
        "trade_date": trade_date.isoformat(),
        "sent_at": datetime.now(tz=NY).isoformat(),
        "featured_stock": post_data.get("featured_stock"),
        "post_style": post_data.get("style"),
        "char_count": post_data.get("char_count"),
        "market_internals_session": (internals.get("breadth") or {}).get("session_date"),
    }
    filename = f"{time_period}_run.json"
    _write_json(day_dir / filename, run_payload)


def _movers_from_internals_or_context(
    internals_movers: Dict[str, Any],
    context_rows: Sequence[Dict[str, Any]],
    side: str,
) -> List[Dict[str, Any]]:
    key = "gainers" if side == "gainer" else "losers"
    raw = internals_movers.get(key) or []
    if raw:
        catalyst_by_ticker = {
            str(c.get("ticker") or "").upper(): str(c.get("catalyst") or "")
            for c in context_rows
            if c.get("ticker")
        }
        rows: List[Dict[str, Any]] = []
        for m in raw:
            ticker = str(m.get("symbol") or m.get("ticker") or "").upper()
            pct = m.get("pct", m.get("change_percentage"))
            rows.append(
                {
                    "ticker": ticker,
                    "change_percentage": pct,
                    "catalyst": catalyst_by_ticker.get(ticker, ""),
                    "last": m.get("last"),
                }
            )
        if any(r.get("catalyst") for r in rows):
            return rows
        try:
            from massive_client import enrich_movers_with_news

            enriched = enrich_movers_with_news(
                [
                    {
                        "symbol": r["ticker"],
                        "ticker": r["ticker"],
                        "change_percentage": r.get("change_percentage"),
                        "last": r.get("last"),
                    }
                    for r in rows
                ],
                limit=len(rows),
            )
            return merge_catalyst_from_enriched(
                [normalize_mover_row(r, side) for r in rows],
                enriched,
            )
        except Exception as exc:
            logger.warning("enrich_movers_with_news failed: %s", exc)
            return [normalize_mover_row(r, side) for r in rows]

    return [normalize_mover_row(m, side) for m in context_rows]


def trade_date_from_context(context: Dict[str, Any]) -> date:
    """Best-effort trade date from email context timestamp."""
    ts = context.get("timestamp") or ""
    match = re.search(r"(\d{4}-\d{2}-\d{2})", str(ts))
    if match:
        return _parse_trade_date(match.group(1))
    return datetime.now(tz=NY).date()


def token_set(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def token_overlap(a: str, b: str) -> float:
    sa, sb = token_set(a), token_set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def wanted_sentiment(direction: str) -> str:
    return "positive" if direction == "gainer" else "negative"


def score_headline(
    headline: Dict[str, Any],
    *,
    direction: str,
    window_start: datetime,
    as_of: datetime,
) -> float:
    published = headline.get("published_dt")
    if not isinstance(published, datetime):
        return 0.0
    if published < window_start or published > as_of:
        return 0.0
    span = max((as_of - window_start).total_seconds(), 1.0)
    age = (as_of - published).total_seconds()
    recency = max(0.0, 1.0 - age / span)
    sentiment = str(headline.get("sentiment") or "").lower()
    wanted = wanted_sentiment(direction)
    if sentiment == wanted:
        sentiment_score = 1.0
    elif sentiment in ("", "neutral"):
        sentiment_score = 0.5
    else:
        sentiment_score = 0.0
    return RECALL_RECENCY_WEIGHT * recency + RECALL_SENTIMENT_WEIGHT * sentiment_score


def news_in_window(
    ticker: str,
    *,
    window_start: datetime,
    as_of: datetime,
    limit: int = 25,
) -> List[Dict[str, Any]]:
    from massive_client import get_news_for_ticker

    items = get_news_for_ticker(ticker, limit=limit)
    out: List[Dict[str, Any]] = []
    for item in items:
        published = _news_datetime(item)
        if published is None:
            continue
        if published < window_start or published > as_of:
            continue
        row = dict(item)
        row["published_dt"] = published
        out.append(row)
    out.sort(key=lambda x: x["published_dt"], reverse=True)
    return out


def _news_datetime(item: Dict[str, Any]) -> Optional[datetime]:
    ts = item.get("datetime")
    if isinstance(ts, (int, float)) and ts > 0:
        return datetime.fromtimestamp(ts, tz=NY)
    published = item.get("published_utc") or item.get("published")
    if isinstance(published, str) and published:
        try:
            dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
            return dt.astimezone(NY)
        except ValueError:
            return None
    return None


@dataclass
class RecallRow:
    ticker: str
    change_percentage: float
    pipeline_catalyst: str
    massive_best: str
    massive_top5: List[str]
    verdict: str
    overlap: float
    headline_score: float

    def to_markdown(self) -> str:
        top5 = "\n".join(f"  - {h}" for h in self.massive_top5) if self.massive_top5 else "  - (none in window)"
        said = self.pipeline_catalyst or "(empty)"
        best = self.massive_best or "(no scored headline)"
        return (
            f"### {self.ticker} ({self.change_percentage:+.2f}%)\n"
            f"- **Pipeline catalyst:** {said}\n"
            f"- **Massive best match:** {best}\n"
            f"- **Top headlines (24h):**\n{top5}\n"
            f"- **Overlap:** {self.overlap:.0%} | **Score:** {self.headline_score:.2f} | **{self.verdict}**\n"
        )


def evaluate_mover_recall(
    mover: Dict[str, Any],
    *,
    trade_date: date,
    as_of: Optional[datetime] = None,
) -> RecallRow:
    as_of = as_of or audit_as_of_for_trade_date(trade_date)
    window_start = as_of - CATALYST_NEWS_WINDOW
    direction = mover.get("direction") or ("gainer" if mover.get("change_percentage", 0) >= 0 else "loser")
    ticker = mover["ticker"]
    pipeline = mover.get("catalyst") or ""

    headlines = news_in_window(ticker, window_start=window_start, as_of=as_of, limit=25)
    scored = sorted(
        headlines,
        key=lambda h: score_headline(h, direction=direction, window_start=window_start, as_of=as_of),
        reverse=True,
    )
    top5 = scored[:5]
    best = top5[0] if top5 else None
    best_headline = str(best.get("headline") or "") if best else ""
    overlap = token_overlap(pipeline, best_headline) if best_headline else 0.0
    if not pipeline:
        verdict = "MISS (no pipeline catalyst)"
    elif not best_headline:
        verdict = "MISS (no Massive headlines)"
    elif overlap >= RECALL_HIT_OVERLAP or token_overlap(pipeline, " ".join(h.get("headline", "") for h in top5)) >= RECALL_HIT_OVERLAP:
        verdict = "HIT"
    else:
        verdict = "MISS"

    return RecallRow(
        ticker=ticker,
        change_percentage=float(mover.get("change_percentage") or 0),
        pipeline_catalyst=pipeline,
        massive_best=best_headline,
        massive_top5=[str(h.get("headline") or "") for h in top5],
        verdict=verdict,
        overlap=overlap,
        headline_score=score_headline(best, direction=direction, window_start=window_start, as_of=as_of) if best else 0.0,
    )


def load_featured_ticker(trade_date: date) -> Optional[str]:
    day_dir = telemetry_day_dir(trade_date)
    post = _read_json(day_dir / "postmarket_run.json") or {}
    ticker = post.get("featured_stock")
    if ticker:
        return str(ticker).upper()
    pre = _read_json(day_dir / "premarket_run.json") or {}
    ticker = pre.get("featured_stock")
    if ticker:
        return str(ticker).upper()
    gainers, losers, _ = load_movers_for_trade_date(trade_date)
    combined = gainers + losers
    if not combined:
        return None
    top = max(combined, key=lambda m: abs(float(m.get("change_percentage") or 0)))
    return str(top.get("ticker") or "").upper() or None


def in_scheduled_window(now: datetime, target_h: int, target_m: int, grace_minutes: int = 45) -> bool:
    target = now.replace(hour=target_h, minute=target_m, second=0, microsecond=0)
    delta = abs((now - target).total_seconds())
    return delta <= grace_minutes * 60


def should_run_weekday(now: Optional[datetime] = None, force_weekend: bool = False) -> bool:
    now = now or datetime.now(tz=NY)
    if force_weekend:
        return True
    return is_us_market_weekday(now.date())
