#!/usr/bin/env python3
"""
Featured-mover deep dive — news narrative, intraday move timing, options flags.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from catalyst_audit_common import (  # noqa: E402
    RESEARCH_LOG_DIR,
    bootstrap_telemetry_if_missing,
    effective_audit_trade_date,
    load_postmarket_featured,
    parse_published_utc,
)
from massive_client import (  # noqa: E402
    analyze_options_chain,
    find_largest_intraday_move,
    get_news_for_ticker_raw,
)

import pytz

NY = pytz.timezone("America/New_York")
logger = logging.getLogger(__name__)


def _news_section(ticker: str, days: int = 7) -> str:
    items = get_news_for_ticker_raw(ticker, limit=50)
    cutoff = datetime.now(NY) - timedelta(days=days)
    filtered: List[dict] = []
    for item in items:
        pub = parse_published_utc(item.get("published_utc"))
        if pub and pub >= cutoff:
            filtered.append((pub, item))
    filtered.sort(key=lambda x: x[0], reverse=True)
    lines = [f"### News ({days}d, {len(filtered)} articles)"]
    for pub, item in filtered[:12]:
        title = item.get("title") or item.get("headline") or ""
        publisher = ""
        pub_info = item.get("publisher")
        if isinstance(pub_info, dict):
            publisher = pub_info.get("name") or ""
        lines.append(f"- **{pub.strftime('%Y-%m-%d %H:%M ET')}** ({publisher}): {title}")
    if not filtered:
        lines.append("- No Massive headlines in window.")
    return "\n".join(lines)


def _intraday_section(ticker: str, trade_date: date) -> str:
    move = find_largest_intraday_move(ticker, trade_date)
    if not move:
        return "### Intraday (1m bars)\n- No minute bars available for session."
    lines = [
        "### Intraday (1m bars)",
        f"- Largest 1m move: **{move['pct_change']:+.2f}%** at {move['bar_time_et']}",
        f"- Bar OHLC: O {move['open']:.2f} H {move['high']:.2f} L {move['low']:.2f} C {move['close']:.2f}",
        f"- Volume: {move.get('volume', 0):,}",
    ]
    if move.get("session_pct") is not None:
        lines.append(f"- Session close vs open: {move['session_pct']:+.2f}%")
    return "\n".join(lines)


def _options_section(ticker: str) -> str:
    analysis = analyze_options_chain(ticker)
    if analysis.get("error"):
        return f"### Options\n- {analysis['error']}"
    lines = ["### Options"]
    if analysis.get("note"):
        lines.append(f"- {analysis['note']}")
    for flag in analysis.get("flags") or []:
        lines.append(f"- {flag}")
    if not analysis.get("flags"):
        lines.append("- No unusual IV/OI flags vs chain median (heuristic).")
    return "\n".join(lines)


def build_section(ticker: str, trade_date: date) -> str:
    parts = [
        f"## Featured: ${ticker}",
        f"_Trade date {trade_date.isoformat()} — generated {datetime.now(NY).strftime('%Y-%m-%d %H:%M ET')}_",
        "",
        _news_section(ticker),
        "",
        _intraday_section(ticker, trade_date),
        "",
        _options_section(ticker),
        "",
    ]
    return "\n".join(parts)


def run_deep_dive(trade_date: Optional[date] = None) -> Optional[Path]:
    trade_date = trade_date or effective_audit_trade_date()
    bootstrap_telemetry_if_missing(trade_date)
    ticker = load_postmarket_featured(trade_date)
    if not ticker:
        logger.warning("No featured_stock in postmarket_run.json for %s", trade_date)
        return None

    RESEARCH_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = RESEARCH_LOG_DIR / f"{trade_date.isoformat()}.md"
    section = build_section(ticker, trade_date)

    marker = f"## Featured: ${ticker}"
    if log_path.is_file():
        existing = log_path.read_text(encoding="utf-8")
        if marker in existing:
            start = existing.index(marker)
            end = existing.find("\n## Featured:", start + len(marker))
            if end == -1:
                existing = existing[:start].rstrip() + "\n\n" + section
            else:
                existing = existing[:start].rstrip() + "\n\n" + section + existing[end:]
            log_path.write_text(existing, encoding="utf-8")
        else:
            log_path.write_text(existing.rstrip() + "\n\n" + section, encoding="utf-8")
    else:
        header = f"# Research log — {trade_date.isoformat()}\n\n"
        log_path.write_text(header + section, encoding="utf-8")

    logger.info("Appended featured deep dive for %s to %s", ticker, log_path)
    return log_path


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Featured mover deep dive")
    parser.add_argument("--date", help="Trade date YYYY-MM-DD")
    parser.add_argument("--ticker", help="Override featured ticker")
    args = parser.parse_args()
    trade_date = None
    if args.date:
        trade_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    if args.ticker:
        RESEARCH_LOG_DIR.mkdir(parents=True, exist_ok=True)
        td = trade_date or effective_audit_trade_date()
        log_path = RESEARCH_LOG_DIR / f"{td.isoformat()}.md"
        section = build_section(args.ticker.upper(), td)
        if log_path.is_file():
            log_path.write_text(log_path.read_text(encoding="utf-8").rstrip() + "\n\n" + section, encoding="utf-8")
        else:
            log_path.write_text(f"# Research log — {td.isoformat()}\n\n" + section, encoding="utf-8")
        print(log_path)
        return 0
    path = run_deep_dive(trade_date=trade_date)
    if path:
        print(path)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
