#!/usr/bin/env python3
"""Featured mover research log — news narrative, intraday timing, options flags."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.catalyst_audit_common import (  # noqa: E402
    analyze_intraday_moves,
    bootstrap_telemetry_if_missing,
    ensure_report_dirs,
    fetch_minute_bars,
    fetch_options_snapshot,
    fetch_ticker_news,
    load_featured_stock,
    ny_now,
    resolve_audit_trade_date,
    should_skip_weekend,
    TELEMETRY_ROOT,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _fmt_move(label: str, move: dict | None) -> str:
    if not move:
        return f"- {label}: n/a"
    ts = move.get("ts")
    ts_s = ts.strftime("%H:%M ET") if isinstance(ts, datetime) else str(ts)
    return f"- {label}: {move.get('pct'):+.2f}% at {ts_s}"


def append_featured_section(trade_date, force_weekend: bool = False) -> Path:
    if should_skip_weekend(force=force_weekend):
        logger.info("Weekend — skipping featured deep dive")
        return TELEMETRY_ROOT / "research_log" / f"{trade_date.isoformat()}.md"

    ensure_report_dirs()
    bootstrap_telemetry_if_missing(trade_date)
    ticker, meta = load_featured_stock(trade_date)
    log_path = TELEMETRY_ROOT / "research_log" / f"{trade_date.isoformat()}.md"

    if not ticker:
        section = f"\n## Featured: (none)\n\n_No featured ticker for {trade_date.isoformat()}._\n"
        log_path.write_text((log_path.read_text(encoding="utf-8") if log_path.is_file() else "") + section, encoding="utf-8")
        return log_path

    news_start = ny_now() - timedelta(days=7)
    news_end = ny_now()
    news = fetch_ticker_news(ticker, limit=15, published_gte=news_start, published_lte=news_end)
    bars = fetch_minute_bars(ticker, trade_date)
    intraday = analyze_intraday_moves(bars)
    options = fetch_options_snapshot(ticker)

    gainers, losers = bootstrap_telemetry_if_missing(trade_date)
    mover = next(
        (m for m in gainers + losers if (m.get("ticker") or "").upper() == ticker),
        {},
    )
    pct = float(mover.get("change_percentage") or 0)
    catalyst = mover.get("catalyst") or meta.get("featured_mover", {}).get("catalyst") or ""

    lines = [
        f"\n## Featured: ${ticker}\n",
        f"- Trade date: {trade_date.isoformat()}",
        f"- Session move (telemetry): {pct:+.2f}%",
        f"- Pipeline catalyst: {catalyst or '(empty)'}",
        f"- Featured source: {meta.get('fallback') or 'postmarket_run.json'}",
        "",
        "### 7-day Massive narrative",
        "",
    ]
    if news:
        for art in news[:12]:
            lines.append(
                f"- {art['published'].strftime('%Y-%m-%d %H:%M ET')}: {art['headline']}"
            )
    else:
        lines.append("_No Massive headlines in the last 7 days._")

    lines.extend(["", "### Intraday (1m bars)", ""])
    if intraday:
        lines.append(f"- Bars: {intraday.get('bar_count', 0)}")
        lines.append(_fmt_move("Largest 1m jump", intraday.get("largest_1m_move")))
        lines.append(_fmt_move("Max from RTH open", intraday.get("max_from_rth_open")))
        if news and intraday.get("largest_1m_move"):
            jump_ts = intraday["largest_1m_move"].get("ts")
            if isinstance(jump_ts, datetime):
                nearby = [
                    a
                    for a in news
                    if abs((a["published"] - jump_ts).total_seconds()) <= 3600
                ]
                if nearby:
                    lines.append(
                        f"- Headlines within ±1h of largest 1m move: "
                        + "; ".join(n["headline"][:60] for n in nearby[:3])
                    )
    else:
        lines.append("_No minute bars returned for session._")

    lines.extend(["", "### Options snapshot", ""])
    if not options.get("ok"):
        lines.append(
            f"_Options chain unavailable ({options.get('error', 'unknown')}) — "
            "Massive snapshot tier may not include `/v3/snapshot/options`._"
        )
    else:
        unusual = options.get("unusual") or []
        if unusual:
            lines.append("Unusual volume (≥500 contracts today):")
            for u in unusual:
                lines.append(
                    f"- {u.get('type')} {u.get('strike')} exp {u.get('exp')}: "
                    f"vol={u.get('volume')} OI={u.get('open_interest')} IV={u.get('iv')}"
                )
        else:
            lines.append("_No contracts flagged for elevated volume._")

    lines.append("")
    section = "\n".join(lines) + "\n"

    existing = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
    if f"## Featured: ${ticker}" in existing:
        logger.info("Featured section for %s already present — skipping duplicate", ticker)
        return log_path
    if not existing:
        existing = f"# Research log — {trade_date.isoformat()}\n"
    log_path.write_text(existing + section, encoding="utf-8")
    logger.info("Appended featured deep dive for %s to %s", ticker, log_path)
    return log_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Featured mover deep dive")
    parser.add_argument("--trade-date", help="YYYY-MM-DD (default: resolve_audit_trade_date)")
    parser.add_argument("--force-weekend", action="store_true")
    args = parser.parse_args()

    if args.trade_date:
        from datetime import date

        trade_date = date.fromisoformat(args.trade_date)
    else:
        trade_date = resolve_audit_trade_date()

    append_featured_section(trade_date, force_weekend=args.force_weekend)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
