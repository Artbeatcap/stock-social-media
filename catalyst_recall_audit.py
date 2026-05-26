#!/usr/bin/env python3
"""
Catalyst recall audit — weekdays ~9:15pm ET.

For each ticker in today's validation_telemetry top_gainers / top_losers,
compare the pipeline's ``catalyst`` headline to Massive news (last 24h),
scoring top headlines by recency + sentiment alignment, and write a report to
``validation_telemetry/reports/catalyst_recall_YYYY-MM-DD.md``.
"""

from __future__ import annotations

import argparse
import logging
import re
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytz

from massive_client import get_news_for_ticker
from validation_telemetry.store import (
    all_movers_for_audit,
    load_daily_telemetry,
    reports_dir,
)

logger = logging.getLogger(__name__)
NY = pytz.timezone("America/New_York")

STOPWORDS = {
    "a", "an", "the", "and", "or", "for", "to", "of", "in", "on", "at", "is",
    "are", "was", "were", "with", "as", "by", "from", "its", "it", "that", "this",
    "after", "before", "into", "over", "up", "down", "vs", "s", "t", "re",
}


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if len(w) > 2 and w not in STOPWORDS}


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    ta, tb = _tokenize(a), _tokenize(b)
    jaccard = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    seq = SequenceMatcher(None, a.lower(), b.lower()).ratio()
    return max(jaccard, seq)


def _ticker_sentiment(headline: str, summary: str, ticker: str) -> str:
    """Best-effort sentiment for this ticker from headline text."""
    blob = f"{headline} {summary}".lower()
    pos = ("surge", "soar", "beat", "upgrade", "rally", "jump", "gain", "record high", "bull")
    neg = ("fall", "drop", "plunge", "miss", "downgrade", "cut", "selloff", "warn", "bear", "low")
    score = sum(1 for w in pos if w in blob) - sum(1 for w in neg if w in blob)
    if score > 0:
        return "positive"
    if score < 0:
        return "negative"
    return "neutral"


def _recency_score(published_ts: int, *, hours: int = 24) -> float:
    now = int(datetime.now(tz=NY).timestamp())
    age_h = max(0.0, (now - published_ts) / 3600.0)
    return max(0.0, 1.0 - age_h / float(hours))


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def score_headlines(
    articles: List[dict[str, Any]],
    *,
    direction: str,
    pipeline_catalyst: str,
) -> List[dict[str, Any]]:
    want = "positive" if direction == "gainer" else "negative"
    scored: list[dict[str, Any]] = []

    for article in articles[:5]:
        headline = str(article.get("headline") or "")
        summary = str(article.get("summary") or "")
        published = int(article.get("datetime") or 0)
        item_sentiment = str(article.get("sentiment") or "").lower()
        if not item_sentiment or item_sentiment == "neutral":
            item_sentiment = _ticker_sentiment(headline, summary, "")

        recency = _recency_score(published)
        sentiment_match = 1.0 if item_sentiment == want else (0.35 if item_sentiment == "neutral" else 0.0)
        text_match = _similarity(pipeline_catalyst, headline)
        total = recency * 0.45 + sentiment_match * 0.35 + text_match * 0.20

        scored.append(
            {
                "headline": headline,
                "published": published,
                "sentiment": item_sentiment,
                "recency": round(recency, 3),
                "sentiment_match": round(sentiment_match, 3),
                "text_match": round(text_match, 3),
                "score": round(total, 3),
                "url": article.get("url") or "",
            }
        )

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


def audit_mover(mover: dict[str, Any]) -> dict[str, Any]:
    ticker = str(mover.get("ticker") or mover.get("symbol") or "").upper()
    direction = mover.get("direction") or ("gainer" if _as_float(mover.get("change_percentage")) >= 0 else "loser")
    pct = _as_float(mover.get("change_percentage"))
    pipeline = str(mover.get("catalyst") or "").strip()

    articles = get_news_for_ticker(ticker, limit=5, hours_back=24) if ticker else []
    ranked = score_headlines(articles, direction=direction, pipeline_catalyst=pipeline)
    best = ranked[0] if ranked else None

    hit = False
    match_reason = "no news"
    if not pipeline:
        match_reason = "pipeline empty"
    elif best:
        if _similarity(pipeline, best["headline"]) >= 0.28:
            hit = True
            match_reason = "headline overlap with top Massive story"
        elif any(_similarity(pipeline, row["headline"]) >= 0.22 for row in ranked):
            hit = True
            match_reason = "overlap with top-5 Massive story"
        elif best["sentiment_match"] >= 1.0 and best["recency"] >= 0.35:
            hit = True
            match_reason = "recency + sentiment aligned (no direct headline match)"

    return {
        "ticker": ticker,
        "pct_move": pct,
        "direction": direction,
        "pipeline_catalyst": pipeline or "(none)",
        "massive_best": best["headline"] if best else "(no articles in 24h)",
        "massive_ranked": ranked,
        "verdict": "hit" if hit else "miss",
        "match_reason": match_reason,
    }


def render_report(results: List[dict[str, Any]], day: str) -> str:
    hits = sum(1 for r in results if r["verdict"] == "hit")
    lines = [
        f"# Catalyst recall audit — {day}",
        "",
        f"Generated: {datetime.now(tz=NY).strftime('%Y-%m-%d %H:%M ET')}",
        f"Tickers audited: {len(results)} | Hits: {hits} | Misses: {len(results) - hits}",
        "",
        "| Ticker | % move | Pipeline said | Massive (best match) | Verdict |",
        "|--------|--------|---------------|----------------------|---------|",
    ]
    for row in results:
        pipe = (row["pipeline_catalyst"] or "")[:80].replace("|", "/")
        massive = (row["massive_best"] or "")[:80].replace("|", "/")
        lines.append(
            f"| {row['ticker']} | {row['pct_move']:+.2f}% | {pipe} | {massive} | **{row['verdict']}** |"
        )

    lines.extend(["", "## Details", ""])
    for row in results:
        lines.append(f"### {row['ticker']} ({row['pct_move']:+.2f}%) — {row['verdict']}")
        lines.append(f"- **Pipeline:** {row['pipeline_catalyst']}")
        lines.append(f"- **Massive (top):** {row['massive_best']}")
        lines.append(f"- **Reason:** {row['match_reason']}")
        if row.get("massive_ranked"):
            lines.append("- **Top 5 scored headlines:**")
            for item in row["massive_ranked"]:
                lines.append(
                    f"  - score={item['score']} rec={item['recency']} sent={item['sentiment']} "
                    f"— {item['headline'][:120]}"
                )
        lines.append("")

    return "\n".join(lines)


def run_audit(day: Optional[str] = None) -> Tuple[Path, List[dict[str, Any]]]:
    day = day or datetime.now(tz=NY).date().isoformat()
    record = load_daily_telemetry(day)
    movers = all_movers_for_audit(record)

    if not movers:
        logger.warning("No movers in telemetry for %s — run postmarket email first or seed telemetry", day)

    results = [audit_mover(m) for m in movers]
    report = render_report(results, day)
    out = reports_dir() / f"catalyst_recall_{day}.md"
    out.write_text(report, encoding="utf-8")
    logger.info("Wrote catalyst recall report: %s", out)
    return out, results


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Catalyst recall audit vs Massive news")
    parser.add_argument("--date", help="Trading day YYYY-MM-DD (default: today ET)")
    args = parser.parse_args()
    path, results = run_audit(args.date)
    print(f"Report: {path} ({len(results)} tickers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
