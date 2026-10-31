#!/usr/bin/env python3
"""Featured mover research log: multi-day news, intraday move timing, options."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from massive_client import _as_float, get_news_for_ticker, get_snapshot  # noqa: E402
from scripts.catalyst_audit_common import (  # noqa: E402
    ET,
    RESEARCH_LOG_DIR,
    analyze_price_move,
    bootstrap_telemetry_if_missing,
    ensure_telemetry_dirs,
    fetch_options_chain,
    fetch_ticker_news_window,
    get_minute_bars_for_date,
    resolve_audit_trade_date,
    resolve_featured_mover,
    summarize_options_unusual,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _append_section(path: Path, section: str, ticker: str) -> bool:
    marker = f"## Featured: ${ticker.upper()}"
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if marker in existing:
            logger.info("Featured section already present in %s; skipping duplicate", path)
            return False
        path.write_text(existing.rstrip() + "\n\n" + section + "\n", encoding="utf-8")
    else:
        path.write_text(section + "\n", encoding="utf-8")
    return True


def run_featured_deep_dive(trade_date=None) -> Path:
    ensure_telemetry_dirs()
    trade_date = trade_date or resolve_audit_trade_date()
    bootstrap_telemetry_if_missing(trade_date)

    mover, source = resolve_featured_mover(trade_date)
    log_path = RESEARCH_LOG_DIR / f"{trade_date.isoformat()}.md"

    if not mover or not mover.get("ticker"):
        note = (
            f"# Research log — {trade_date.isoformat()}\n\n"
            f"_No featured mover resolved ({source})._\n"
        )
        log_path.write_text(note, encoding="utf-8")
        return log_path

    ticker = str(mover["ticker"]).upper()
    pct = mover.get("change_percentage")
    catalyst = str(mover.get("catalyst") or "")

    now = datetime.now(tz=ET)
    news_start = now - timedelta(days=7)
    news_end = now
    week_news = fetch_ticker_news_window(ticker, news_start, news_end, limit=40)
    if not week_news:
        week_news = get_news_for_ticker(ticker, limit=15)

    bars = get_minute_bars_for_date(ticker, trade_date)
    move_stats = analyze_price_move(bars)

    snap = get_snapshot(ticker)
    last_price = snap.get("last") or mover.get("price") or mover.get("last")

    chain, chain_err = fetch_options_chain(ticker, limit=80)
    options_notes: list[str]
    if chain_err:
        options_notes = [chain_err]
    elif chain:
        options_notes = summarize_options_unusual(chain, _as_float(last_price))
    else:
        options_notes = ["No unusual options activity flagged."]

    jump = move_stats.get("largest_1m_jump") or {}
    from_open = move_stats.get("max_from_open") or {}
    jump_time = jump.get("time")
    open_time = from_open.get("time")

    news_lines = []
    if week_news:
        for item in week_news[:12]:
            title = item.get("headline") or item.get("title") or ""
            ts = item.get("published_utc") or item.get("datetime")
            news_lines.append(f"- {title} `{ts}`")
    else:
        news_lines.append("_No Massive headlines in the last 7 days._")

    pct_str = f"{float(pct):+.2f}%" if pct is not None else "n/a"

    section = (
        f"## Featured: ${ticker}\n\n"
        f"_Source: {source}_\n\n"
        f"- **Session move (telemetry):** {pct_str}\n"
        f"- **Pipeline catalyst:** {catalyst or '_(empty)_'}\n"
        f"- **1m bars ({trade_date}):** {move_stats.get('bar_count', 0)} bars\n"
    )
    if jump_time:
        section += (
            f"- **Largest 1m jump:** {jump.get('pct'):+.2f}% at "
            f"{jump_time.strftime('%H:%M ET')}\n"
        )
    if open_time:
        section += (
            f"- **Max from session open:** {from_open.get('pct'):+.2f}% at "
            f"{open_time.strftime('%H:%M ET')}\n"
        )

    section += (
        f"\n### 7-day narrative (Massive)\n\n"
        + "\n".join(news_lines)
        + "\n\n### Options snapshot\n\n"
        + "\n".join(f"- {line}" for line in options_notes)
        + "\n"
    )

    if not log_path.exists():
        log_path.write_text(
            f"# Research log — {trade_date.isoformat()}\n\n"
            f"Generated {now.strftime('%Y-%m-%d %H:%M ET')}\n\n",
            encoding="utf-8",
        )

    if _append_section(log_path, section, ticker):
        logger.info("Appended featured deep dive for %s to %s", ticker, log_path)
    return log_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Featured mover deep dive research log")
    parser.add_argument("--trade-date", help="YYYY-MM-DD session (default: resolved)")
    parser.add_argument("--force-weekend", action="store_true")
    args = parser.parse_args()

    trade_date = resolve_audit_trade_date()
    if args.trade_date:
        trade_date = datetime.strptime(args.trade_date, "%Y-%m-%d").date()

    if not args.force_weekend and trade_date.weekday() >= 5:
        logger.info("Skipping featured deep dive on weekend trade date %s", trade_date)
        return 0

    run_featured_deep_dive(trade_date=trade_date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
