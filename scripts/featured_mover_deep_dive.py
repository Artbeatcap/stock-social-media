#!/usr/bin/env python3
"""
Featured-mover deep dive — multi-day news, intraday move timing, options flags.

Appends ## Featured: $TICKER to validation_telemetry/research_log/YYYY-MM-DD.md
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.audit_common import (  # noqa: E402
    NY,
    RESEARCH_LOG_DIR,
    bootstrap_telemetry_if_missing,
    effective_audit_trade_date,
    ensure_telemetry_dirs,
    load_postmarket_run,
    now_ny,
)
from massive_client import (  # noqa: E402
    analyze_options_chain,
    find_largest_intraday_move,
    get_minute_bars_for_date,
    get_news_for_ticker,
)


def _resolve_featured_ticker(trade_date) -> Tuple[Optional[str], Dict[str, Any]]:
    run = load_postmarket_run(trade_date)
    ticker = (run.get("featured_stock") or "").strip().upper() or None
    meta: Dict[str, Any] = {"featured_source": "postmarket_run" if ticker else None}
    if ticker:
        return ticker, meta

    movers, boot = bootstrap_telemetry_if_missing(trade_date)
    meta["featured_source"] = "bootstrap_top_mover"
    meta.update(boot)
    if not movers:
        return None, meta
    movers_sorted = sorted(movers, key=lambda m: abs(float(m.get("change_percentage") or 0.0)), reverse=True)
    return str(movers_sorted[0].get("ticker") or "").upper() or None, meta


def _format_news_section(items: List[Dict[str, Any]], days: int) -> str:
    if not items:
        return f"_No Massive headlines in the last {days} days._\n"
    lines = []
    for item in items[:12]:
        ts = int(item.get("datetime") or 0)
        when = datetime.fromtimestamp(ts, tz=NY).strftime("%Y-%m-%d %H:%M ET") if ts else "?"
        headline = str(item.get("headline") or "")
        sent = item.get("sentiment") or ""
        lines.append(f"- **{when}** ({sent or 'n/a'}): {headline}")
    return "\n".join(lines) + "\n"


def append_featured_section(trade_date) -> Path:
    ensure_telemetry_dirs()
    ticker, meta = _resolve_featured_ticker(trade_date)
    log_path = RESEARCH_LOG_DIR / f"{trade_date.isoformat()}.md"
    if not log_path.exists():
        log_path.write_text(
            f"# Research log — {trade_date.isoformat()}\n\n",
            encoding="utf-8",
        )

    if not ticker:
        section = (
            f"\n## Featured: (none)\n\n"
            f"_No featured_stock in postmarket_run and bootstrap returned no movers "
            f"({meta.get('featured_source')})._\n"
        )
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(section)
        return log_path

    news = get_news_for_ticker(ticker, limit=40)
    cutoff = now_ny().timestamp() - 7 * 86400
    news_7d = []
    for item in news:
        ts = int(item.get("datetime") or 0)
        if ts == 0 or ts >= cutoff:
            news_7d.append(item)

    bars = get_minute_bars_for_date(ticker, trade_date)
    move_info = find_largest_intraday_move(bars, news_items=news_7d)
    options_note = analyze_options_chain(ticker)

    section_lines = [
        f"\n## Featured: ${ticker}",
        "",
        f"_Generated {now_ny().strftime('%Y-%m-%d %H:%M ET')}; featured source: {meta.get('featured_source')}._",
        "",
        "### Narrative (Massive news, 7d)",
        _format_news_section(news_7d, 7).rstrip(),
        "",
        "### Intraday move (1m bars, trade date)",
    ]
    if move_info:
        section_lines.append(
            f"- Largest 1m bar: **{move_info['time_et']}** "
            f"({move_info['pct_change']:+.2f}% close-to-close on bar, "
            f"vol {move_info.get('volume', 0):,})"
        )
        if move_info.get("nearest_headline"):
            section_lines.append(f"- Nearest headline (±2h): {move_info['nearest_headline']}")
    else:
        section_lines.append(f"- _No 1m bars returned for {trade_date.isoformat()}._")

    section_lines.extend(["", "### Options snapshot", options_note, ""])
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(section_lines) + "\n")
    return log_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Featured mover deep dive")
    parser.add_argument("--date", help="Trade date YYYY-MM-DD")
    args = parser.parse_args()
    trade_date = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else effective_audit_trade_date()
    path = append_featured_section(trade_date)
    print(f"Updated {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
