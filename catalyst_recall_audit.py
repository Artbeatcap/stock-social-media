#!/usr/bin/env python3
"""
Catalyst recall audit — weekdays 9:15pm ET.

Compares each mover catalyst cited by the production pipeline against Massive
headlines for the same ticker, scoring by recency and sentiment alignment.
"""

from __future__ import annotations

import argparse
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Tuple

import pytz

from massive_client import get_news_for_ticker
from validation_telemetry import REPORTS_DIR, ensure_dirs, load_telemetry, today_iso

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

NY = pytz.timezone("America/New_York")
STOPWORDS = {
    "a", "an", "the", "and", "or", "for", "to", "of", "in", "on", "at", "by",
    "is", "are", "was", "were", "with", "from", "as", "its", "it", "that",
    "this", "after", "before", "over", "under", "up", "down", "s", "t",
}


def _tokenize(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]{3,}", (text or "").lower())
    return {token for token in tokens if token not in STOPWORDS}


def text_overlap(a: str, b: str) -> float:
    left = _tokenize(a)
    right = _tokenize(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def score_headline(headline: Dict[str, Any], pct_move: float, now_ts: int) -> float:
    wanted = "positive" if pct_move >= 0 else "negative"
    sentiment = str(headline.get("sentiment") or "").lower()
    if sentiment == wanted:
        sentiment_score = 1.0
    elif sentiment in ("", "neutral"):
        sentiment_score = 0.5
    else:
        sentiment_score = 0.0

    pub_ts = int(headline.get("datetime") or 0)
    age_hours = max(0.0, (now_ts - pub_ts) / 3600.0) if pub_ts else 24.0
    recency_score = max(0.0, 1.0 - min(age_hours, 24.0) / 24.0)
    return recency_score * 0.6 + sentiment_score * 0.4


def pick_best_headline(headlines: List[Dict[str, Any]], pct_move: float, now_ts: int) -> Tuple[Dict[str, Any], float]:
    if not headlines:
        return {}, 0.0
    scored = [(score_headline(item, pct_move, now_ts), item) for item in headlines]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[0][1], scored[0][0]


def audit_mover(mover: Dict[str, Any], direction: str) -> Dict[str, Any]:
    ticker = str(mover.get("ticker") or mover.get("symbol") or "").upper()
    pct = float(mover.get("change_percentage", mover.get("pct", 0)) or 0)
    pipeline_catalyst = str(mover.get("catalyst") or "").strip()
    now_ts = int(datetime.now(tz=NY).timestamp())

    headlines = get_news_for_ticker(ticker, limit=10)
    cutoff = now_ts - 24 * 3600
    headlines = [item for item in headlines if int(item.get("datetime") or 0) >= cutoff][:5]
    best, best_score = pick_best_headline(headlines, pct, now_ts)
    massive_headline = str(best.get("headline") or "").strip()

    overlaps = [text_overlap(pipeline_catalyst, item.get("headline", "")) for item in headlines]
    max_overlap = max(overlaps) if overlaps else 0.0

    hit = False
    reason = "miss"
    if not pipeline_catalyst and not massive_headline:
        reason = "no catalyst on either side"
    elif not pipeline_catalyst:
        reason = "pipeline had no catalyst"
    elif not massive_headline:
        reason = "massive returned no headlines"
    elif max_overlap >= 0.35:
        hit = True
        reason = f"headline overlap {max_overlap:.0%}"
    elif pipeline_catalyst.lower() in massive_headline.lower() or massive_headline.lower() in pipeline_catalyst.lower():
        hit = True
        reason = "substring match"
    elif best_score >= 0.75 and text_overlap(pipeline_catalyst, massive_headline) >= 0.2:
        hit = True
        reason = "top scored headline partial match"

    top_massive = [
        {
            "headline": item.get("headline", ""),
            "sentiment": item.get("sentiment", ""),
            "score": round(score_headline(item, pct, now_ts), 2),
        }
        for item in headlines
    ]

    return {
        "ticker": ticker,
        "direction": direction,
        "pct_move": pct,
        "pipeline_catalyst": pipeline_catalyst or "(none)",
        "massive_best": massive_headline or "(none)",
        "massive_top5": top_massive,
        "result": "hit" if hit else "miss",
        "reason": reason,
    }


def build_report(session_date: str) -> str:
    telemetry = load_telemetry(session_date)
    gainers = telemetry.get("top_gainers", [])
    losers = telemetry.get("top_losers", [])

    rows = [audit_mover(mover, "gainer") for mover in gainers]
    rows.extend(audit_mover(mover, "loser") for mover in losers)

    hits = sum(1 for row in rows if row["result"] == "hit")
    total = len(rows)
    recall = (hits / total * 100.0) if total else 0.0

    lines = [
        f"# Catalyst Recall Audit — {session_date}",
        "",
        f"Generated: {datetime.now(tz=NY).strftime('%Y-%m-%d %H:%M ET')}",
        f"Recall: **{hits}/{total}** ({recall:.0f}%)",
        "",
        "| Ticker | Move | Pipeline catalyst | Massive best headline | Result | Notes |",
        "| --- | ---: | --- | --- | --- | --- |",
    ]

    for row in rows:
        move = f"{row['pct_move']:+.2f}%"
        pipeline = row["pipeline_catalyst"].replace("|", "/")[:80]
        massive = row["massive_best"].replace("|", "/")[:80]
        lines.append(
            f"| {row['ticker']} | {move} | {pipeline} | {massive} | {row['result']} | {row['reason']} |"
        )

    if not rows:
        lines.extend(
            [
                "",
                "_No movers found in telemetry. Ensure the post-market email ran and wrote "
                f"`validation_telemetry/{session_date}.json`._",
            ]
        )
    else:
        lines.extend(["", "## Massive top-5 detail", ""])
        for row in rows:
            lines.append(f"### {row['ticker']} ({row['direction']}, {row['pct_move']:+.2f}%)")
            if not row["massive_top5"]:
                lines.append("- No Massive headlines in the last 24h window.")
            else:
                for idx, item in enumerate(row["massive_top5"], start=1):
                    lines.append(
                        f"{idx}. ({item['score']}) [{item['sentiment'] or 'n/a'}] {item['headline']}"
                    )
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run catalyst recall audit against Massive headlines.")
    parser.add_argument("--date", default=today_iso(), help="Session date (YYYY-MM-DD)")
    parser.add_argument("--output", default="", help="Optional output path override")
    args = parser.parse_args()

    ensure_dirs()
    report = build_report(args.date)
    output = (
        REPORTS_DIR / f"catalyst_recall_{args.date}.md"
        if not args.output
        else __import__("pathlib").Path(args.output)
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    logger.info("Wrote catalyst recall report to %s", output)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
