#!/usr/bin/env python3
"""Compare pipeline catalyst strings to Massive headlines for today's movers."""

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
    ET,
    REPORTS_DIR,
    audit_as_of_for_trade_date,
    bootstrap_telemetry_if_missing,
    ensure_telemetry_dirs,
    evaluate_catalyst_recall,
    fetch_ticker_news_window,
    load_movers_for_trade_date,
    rank_top_headlines,
    resolve_audit_trade_date,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _format_time(ts) -> str:
    if ts is None:
        return "—"
    return ts.strftime("%H:%M ET")


def run_recall_audit(trade_date=None, force: bool = False) -> Path:
    ensure_telemetry_dirs()
    trade_date = trade_date or resolve_audit_trade_date()
    window_end = audit_as_of_for_trade_date(trade_date)
    window_start = window_end - timedelta(hours=24)

    bootstrapped = bootstrap_telemetry_if_missing(trade_date)
    gainers, losers = load_movers_for_trade_date(trade_date)

    rows: list[str] = []
    hits = 0
    total = 0

    for mover in gainers + losers:
        ticker = str(mover.get("ticker") or mover.get("symbol") or "").upper()
        if not ticker:
            continue
        pct = mover.get("change_percentage", mover.get("pct", 0))
        is_gainer = float(pct or 0) >= 0
        pipeline_catalyst = str(mover.get("catalyst") or mover.get("why") or "")

        news = fetch_ticker_news_window(ticker, window_start, window_end, limit=25)
        top5 = rank_top_headlines(news, is_gainer=is_gainer, window_end=window_end, n=5)
        best_title, hit, overlap, verdict = evaluate_catalyst_recall(pipeline_catalyst, top5)
        if hit:
            hits += 1
        total += 1

        massive_lines = []
        if not top5:
            massive_lines.append("_No Massive headlines in 24h window._")
        else:
            for headline, score in top5:
                title = headline.get("headline") or headline.get("title") or ""
                ts = headline.get("published_utc") or headline.get("datetime")
                massive_lines.append(f"- ({score:.2f}) {title} `{ts}`")

        rows.append(
            f"### {ticker} ({float(pct):+.2f}%)\n\n"
            f"- **Pipeline catalyst:** {pipeline_catalyst or '_(empty)_'}\n"
            f"- **Best Massive match:** {best_title or '_(none)_'} "
            f"(overlap {overlap:.0%})\n"
            f"- **Verdict:** **{verdict}**\n"
            f"- **Top Massive headlines (24h to {window_end.strftime('%Y-%m-%d %H:%M ET')}):**\n"
            + "\n".join(massive_lines)
            + "\n"
        )

    report_path = REPORTS_DIR / f"catalyst_recall_{trade_date.isoformat()}.md"
    header = (
        f"# Catalyst recall audit — {trade_date.isoformat()}\n\n"
        f"Generated {datetime.now(tz=ET).strftime('%Y-%m-%d %H:%M ET')}  \n"
        f"News window: `{window_start.isoformat()}` → `{window_end.isoformat()}`  \n"
        f"Telemetry bootstrapped: **{'yes' if bootstrapped else 'no'}**  \n"
        f"Hit threshold: token overlap ≥ 35% on best of top-5 scored headlines "
        f"(55% recency + 45% sentiment).\n\n"
        f"## Summary\n\n"
        f"**{hits}/{total} hits**\n\n"
        f"---\n\n"
    )
    report_path.write_text(header + "\n".join(rows), encoding="utf-8")
    logger.info("Wrote %s (%s/%s hits)", report_path, hits, total)
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Catalyst recall audit vs Massive headlines")
    parser.add_argument("--trade-date", help="YYYY-MM-DD session to audit (default: resolved)")
    parser.add_argument("--force-weekend", action="store_true", help="Run even on Sat/Sun")
    args = parser.parse_args()

    trade_date = resolve_audit_trade_date()
    if args.trade_date:
        trade_date = datetime.strptime(args.trade_date, "%Y-%m-%d").date()

    if not args.force_weekend and trade_date.weekday() >= 5:
        logger.info("Skipping recall audit on weekend trade date %s", trade_date)
        return 0

    run_recall_audit(trade_date=trade_date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
