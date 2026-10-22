#!/usr/bin/env python3
"""
Catalyst recall audit — compare pipeline catalyst strings to Massive headlines (24h).
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.catalyst_audit_common import (  # noqa: E402
    NY,
    REPORTS_DIR,
    audit_as_of_for_trade_date,
    bootstrap_telemetry_if_missing,
    classify_recall,
    filter_news_last_hours,
    load_movers_from_telemetry,
    parse_news_items,
    rank_top_headlines,
    trade_date_today,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def fetch_ticker_news(ticker: str, limit: int = 20) -> list:
    from massive_client import get_news_for_ticker

    return get_news_for_ticker(ticker, limit=limit)


def run_audit(trade_date: str) -> Path:
    bootstrap_telemetry_if_missing(trade_date)
    gainers, losers = load_movers_from_telemetry(trade_date)
    as_of = audit_as_of_for_trade_date(trade_date)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"catalyst_recall_{trade_date}.md"

    lines = [
        f"# Catalyst recall audit — {trade_date}",
        "",
        f"_Generated {datetime.now(NY).strftime('%Y-%m-%d %H:%M ET')}_",
        f"_News window: 24h ending {as_of.strftime('%Y-%m-%d %H:%M ET')}_",
        "",
        "| Ticker | Move | Pipeline catalyst | Massive (best match) | Overlap | Verdict |",
        "| --- | ---: | --- | --- | ---: | --- |",
    ]

    rows = 0
    for mover in gainers + losers:
        ticker = mover["ticker"]
        if not ticker:
            continue
        want_positive = mover["change_percentage"] >= 0
        raw = fetch_ticker_news(ticker, limit=25)
        parsed = parse_news_items(
            [
                {
                    "title": i.get("headline"),
                    "published_utc": datetime.fromtimestamp(i["datetime"], tz=NY).isoformat()
                    if isinstance(i.get("datetime"), int)
                    else None,
                    "sentiment": i.get("sentiment"),
                    "article_url": i.get("url"),
                }
                for i in raw
            ]
        )
        in_window = filter_news_last_hours(parsed, 24.0, as_of)
        ranked = rank_top_headlines(in_window, as_of, want_positive, limit=5)
        verdict, best_headline, overlap = classify_recall(mover.get("catalyst", ""), ranked)

        def _esc(s: str) -> str:
            return (s or "").replace("|", "/").replace("\n", " ")[:200]

        lines.append(
            f"| {ticker} | {mover['change_percentage']:+.2f}% | {_esc(mover.get('catalyst', ''))} "
            f"| {_esc(best_headline)} | {overlap:.0%} | **{verdict}** |"
        )
        rows += 1

    if rows == 0:
        lines.append("")
        lines.append("_No movers found in telemetry for this date._")

    lines.extend(["", "## Top Massive headlines (per ticker)", ""])
    for mover in gainers + losers:
        ticker = mover["ticker"]
        if not ticker:
            continue
        want_positive = mover["change_percentage"] >= 0
        raw = fetch_ticker_news(ticker, limit=25)
        parsed = parse_news_items(
            [
                {
                    "title": i.get("headline"),
                    "published_utc": datetime.fromtimestamp(i["datetime"], tz=NY).isoformat()
                    if isinstance(i.get("datetime"), int)
                    else None,
                    "sentiment": i.get("sentiment"),
                }
                for i in raw
            ]
        )
        in_window = filter_news_last_hours(parsed, 24.0, as_of)
        ranked = rank_top_headlines(in_window, as_of, want_positive, limit=5)
        lines.append(f"### {ticker} ({mover['change_percentage']:+.2f}%)")
        if not ranked:
            lines.append("- _(no headlines in 24h window)_")
        for idx, h in enumerate(ranked, 1):
            when = ""
            if h.get("published_dt"):
                when = h["published_dt"].strftime("%m/%d %H:%M ET")
            lines.append(
                f"{idx}. [{when}] ({h.get('sentiment') or 'n/a'}) {h.get('headline', '')}"
            )
        lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Wrote %s (%d movers)", report_path, rows)
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Catalyst recall audit vs Massive headlines")
    parser.add_argument("--date", default=trade_date_today(), help="Trade date YYYY-MM-DD")
    args = parser.parse_args()
    run_audit(args.date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
