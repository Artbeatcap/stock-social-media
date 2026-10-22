#!/usr/bin/env python3
"""
Catalyst recall audit — weekdays 9:15pm ET.

Compares each mover's pipeline catalyst against Massive headlines from the last
24 hours and writes validation_telemetry/reports/catalyst_recall_YYYY-MM-DD.md.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytz

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from massive_client import get_news_for_ticker_window  # noqa: E402
from validation_telemetry import bootstrap_telemetry_if_missing, load_movers, reports_dir, resolve_audit_trade_date  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NY = pytz.timezone("America/New_York")
HIT_SIMILARITY = 0.35


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (text or "").lower())).strip()


def _token_set(text: str) -> set[str]:
    return {token for token in _normalize(text).split() if len(token) > 2}


def headline_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    left_norm = _normalize(left)
    right_norm = _normalize(right)
    if left_norm in right_norm or right_norm in left_norm:
        return 1.0
    left_tokens = _token_set(left)
    right_tokens = _token_set(right)
    if not left_tokens or not right_tokens:
        return SequenceMatcher(None, left_norm, right_norm).ratio()
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def score_headline(
    headline: dict[str, Any],
    pct_move: float,
    now_ts: float,
    *,
    max_age_hours: float = 24.0,
) -> Optional[float]:
    published = int(headline.get("datetime") or 0)
    if not published:
        return None
    age_hours = max(0.05, (now_ts - published) / 3600.0)
    if age_hours > max_age_hours:
        return None

    recency = max(0.0, max_age_hours - age_hours) / max_age_hours
    wanted = "positive" if pct_move >= 0 else "negative"
    sentiment = str(headline.get("sentiment") or "").lower()
    if sentiment == wanted:
        sentiment_score = 1.0
    elif sentiment in ("", "neutral"):
        sentiment_score = 0.5
    else:
        sentiment_score = 0.0
    return recency * 0.55 + sentiment_score * 0.45


def pick_massive_headline(
    headlines: list[dict[str, Any]],
    pct_move: float,
    now_ts: float,
) -> tuple[str, float, list[dict[str, Any]]]:
    scored: list[tuple[dict[str, Any], float]] = []
    for item in headlines[:5]:
        score = score_headline(item, pct_move, now_ts)
        if score is not None:
            scored.append((item, score))
    if not scored:
        return "No qualifying headlines in last 24h", 0.0, headlines[:5]

    best_item, best_score = max(scored, key=lambda pair: pair[1])
    return str(best_item.get("headline") or ""), best_score, headlines[:5]


def audit_mover(mover: dict[str, Any], side: str) -> dict[str, Any]:
    ticker = str(mover.get("ticker") or mover.get("symbol") or "").upper()
    pct = float(mover.get("change_percentage") or mover.get("pct") or 0.0)
    pipeline_catalyst = str(mover.get("catalyst") or "").strip()

    now_ts = datetime.now(NY).timestamp()
    headlines = get_news_for_ticker_window(ticker, hours=24, limit=20)[:5]
    massive_headline, massive_score, top5 = pick_massive_headline(headlines, pct, now_ts)

    best_sim = 0.0
    best_match = ""
    for item in top5:
        sim = headline_similarity(pipeline_catalyst, str(item.get("headline") or ""))
        if sim > best_sim:
            best_sim = sim
            best_match = str(item.get("headline") or "")

    if not pipeline_catalyst:
        verdict = "miss"
        reason = "Pipeline cited no catalyst"
    elif not top5:
        verdict = "miss"
        reason = "No Massive headlines in 24h window"
    elif best_sim >= HIT_SIMILARITY:
        verdict = "hit"
        reason = f"Headline overlap {best_sim:.0%}"
    elif headline_similarity(pipeline_catalyst, massive_headline) >= HIT_SIMILARITY:
        verdict = "hit"
        reason = f"Matches top-scored Massive headline ({massive_score:.2f})"
    else:
        verdict = "miss"
        reason = f"Best overlap {best_sim:.0%} vs top-scored headline"

    return {
        "ticker": ticker,
        "side": side,
        "pct_move": pct,
        "pipeline_catalyst": pipeline_catalyst or "(none)",
        "massive_headline": massive_headline,
        "massive_score": massive_score,
        "best_match_headline": best_match or massive_headline,
        "similarity": best_sim,
        "verdict": verdict,
        "reason": reason,
        "top5": top5,
    }


def render_report(results: list[dict[str, Any]], trade_date: str) -> str:
    hits = sum(1 for row in results if row["verdict"] == "hit")
    misses = len(results) - hits
    lines = [
        f"# Catalyst Recall Audit — {trade_date}",
        "",
        f"Generated: {datetime.now(NY).isoformat(timespec='seconds')}",
        "",
        f"Summary: **{hits} hit / {misses} miss** across {len(results)} movers",
        "",
        "| Ticker | Move | Pipeline catalyst | Massive headline (top-scored) | Result |",
        "| --- | ---: | --- | --- | --- |",
    ]
    for row in results:
        move = f"{row['pct_move']:+.2f}%"
        pipeline = row["pipeline_catalyst"][:80].replace("|", "/")
        massive = row["massive_headline"][:80].replace("|", "/")
        lines.append(
            f"| {row['ticker']} | {move} | {pipeline} | {massive} | **{row['verdict']}** |"
        )

    lines.extend(["", "## Details", ""])
    for row in results:
        lines.extend(
            [
                f"### {row['ticker']} ({row['side']}, {row['pct_move']:+.2f}%)",
                "",
                f"- **Pipeline said:** {row['pipeline_catalyst']}",
                f"- **Massive top-scored:** {row['massive_headline']} (score {row['massive_score']:.2f})",
                f"- **Best headline match:** {row['best_match_headline']} (similarity {row['similarity']:.0%})",
                f"- **Verdict:** {row['verdict'].upper()} — {row['reason']}",
                "",
            ]
        )
        if row["top5"]:
            lines.append("Top Massive headlines (24h):")
            for idx, item in enumerate(row["top5"], start=1):
                published = datetime.fromtimestamp(int(item.get("datetime") or 0), tz=NY)
                sentiment = item.get("sentiment") or "n/a"
                lines.append(
                    f"{idx}. [{published.strftime('%H:%M ET')}] "
                    f"({sentiment}) {item.get('headline', '')}"
                )
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def run_audit(trade_date: Optional[str] = None) -> Path:
    trade_date = trade_date or resolve_audit_trade_date()
    bootstrap_telemetry_if_missing(trade_date)
    gainers = load_movers("gainers", trade_date=trade_date)
    losers = load_movers("losers", trade_date=trade_date)

    if not gainers and not losers:
        raise FileNotFoundError(
            f"No telemetry movers found for {trade_date}. "
            "Ensure postmarket/premarket runs wrote validation_telemetry/ snapshots."
        )

    results: list[dict[str, Any]] = []
    for mover in gainers:
        if mover.get("ticker") or mover.get("symbol"):
            results.append(audit_mover(mover, "gainer"))
    for mover in losers:
        if mover.get("ticker") or mover.get("symbol"):
            results.append(audit_mover(mover, "loser"))

    report_path = reports_dir() / f"catalyst_recall_{trade_date}.md"
    report_path.write_text(render_report(results, trade_date), encoding="utf-8")
    logger.info("Wrote catalyst recall report: %s", report_path)
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run daily catalyst recall audit")
    parser.add_argument("--date", help="Trade date YYYY-MM-DD (default: today ET)")
    args = parser.parse_args()
    try:
        run_audit(args.date)
        return 0
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 1
    except Exception as exc:
        logger.exception("Catalyst recall audit failed: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
