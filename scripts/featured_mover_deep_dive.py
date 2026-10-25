#!/usr/bin/env python3
"""Featured-mover research log: multi-day news, intraday move, options flags."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
for p in (str(ROOT), str(SCRIPTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

from massive_client import (
    analyze_options_unusual_activity,
    find_largest_intraday_move,
    get_news_for_ticker,
)

from catalyst_audit_common import (
    NY,
    RESEARCH_LOG_DIR,
    bootstrap_telemetry_if_missing,
    featured_ticker_from_telemetry,
    resolve_audit_trade_date,
    should_skip_weekend,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _format_news_bullets(items: List[Dict[str, Any]], max_items: int = 8) -> List[str]:
    lines: List[str] = []
    for item in items[:max_items]:
        ts = int(item.get("datetime") or 0)
        when = datetime.fromtimestamp(ts, tz=NY).strftime("%Y-%m-%d %H:%M ET")
        headline = str(item.get("headline") or "").strip()
        if headline:
            lines.append(f"- **{when}** — {headline}")
    return lines or ["- (no headlines in window)"]


def append_featured_section(trade_date: date, ticker: str) -> Path:
    as_of = datetime.now(tz=NY)
    window_start = as_of - timedelta(days=7)

    news = get_news_for_ticker(ticker, limit=40)
    filtered = [
        n
        for n in news
        if datetime.fromtimestamp(int(n.get("datetime") or 0), tz=NY) >= window_start
    ]
    filtered.sort(key=lambda n: int(n.get("datetime") or 0), reverse=True)

    move_info = find_largest_intraday_move(ticker, trade_date)
    options_notes = analyze_options_unusual_activity(ticker)

    movers, mover_source = bootstrap_telemetry_if_missing(trade_date)
    pipeline_catalyst = next(
        (m.get("catalyst") for m in movers if m.get("ticker") == ticker),
        "",
    )

    section_lines = [
        f"## Featured: ${ticker}",
        "",
        f"_Trade date {trade_date.isoformat()} — log appended {as_of.strftime('%Y-%m-%d %H:%M ET')}_",
        "",
        "### Pipeline attribution",
        f"- Telemetry movers source: `{mover_source}`",
        f"- Recorded catalyst: {pipeline_catalyst or '_(none)_'}",
        "",
        "### Massive news (last 7 days)",
        *_format_news_bullets(filtered),
        "",
        "### Intraday price action (1m bars, session)",
    ]

    if move_info.get("error"):
        section_lines.append(f"- {move_info['error']}")
    else:
        section_lines.append(
            f"- Largest 1m move: **{move_info.get('pct_change', 0):+.2f}%** at "
            f"**{move_info.get('time_et', '?')} ET** "
            f"(${move_info.get('open', 0):.2f} → ${move_info.get('close', 0):.2f})"
        )
        if move_info.get("nearest_headline"):
            section_lines.append(f"- Nearest headline (±2h): {move_info['nearest_headline']}")

    section_lines.extend(["", "### Options snapshot"])
    for note in options_notes:
        section_lines.append(f"- {note}")

    section_lines.append("")

    RESEARCH_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = RESEARCH_LOG_DIR / f"{trade_date.isoformat()}.md"
    header = ""
    if not log_path.exists():
        header = (
            f"# Featured mover research log — {trade_date.isoformat()}\n\n"
            f"Post-market deep dives for catalyst phrasing sanity checks.\n\n"
        )
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(header + "\n".join(section_lines) + "\n")

    logger.info("Appended featured section for %s to %s", ticker, log_path)
    return log_path


def run_featured_deep_dive(trade_date: date, ticker: Optional[str] = None) -> Optional[Path]:
    if not ticker:
        ticker, source = featured_ticker_from_telemetry(trade_date)
        if not ticker:
            movers, _ = bootstrap_telemetry_if_missing(trade_date)
            if movers:
                ticker = max(
                    movers,
                    key=lambda m: abs(float(m.get("change_percentage") or 0)),
                )["ticker"]
                source = "largest_abs_move"
            else:
                logger.warning("No featured ticker for %s", trade_date)
                return None
        logger.info("Featured ticker %s (source=%s)", ticker, source)
    return append_featured_section(trade_date, ticker)


def main() -> int:
    parser = argparse.ArgumentParser(description="Featured mover deep dive")
    parser.add_argument("--trade-date", help="YYYY-MM-DD (default: resolved session)")
    parser.add_argument("--ticker", help="Override featured ticker")
    parser.add_argument("--force-weekend", action="store_true")
    args = parser.parse_args()

    if should_skip_weekend() and not args.force_weekend:
        logger.info("Weekend — skipping featured deep dive")
        return 0

    if args.trade_date:
        trade_date = datetime.strptime(args.trade_date, "%Y-%m-%d").date()
    else:
        trade_date = resolve_audit_trade_date()

    path = run_featured_deep_dive(trade_date, args.ticker.upper() if args.ticker else None)
    return 0 if path else 1


if __name__ == "__main__":
    raise SystemExit(main())
