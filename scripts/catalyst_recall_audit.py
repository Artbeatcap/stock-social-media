#!/usr/bin/env python3
"""Daily catalyst recall audit vs Massive headlines (24h window)."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
for p in (str(ROOT), str(SCRIPTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

from massive_client import get_news_for_ticker

from catalyst_audit_common import (
    NY,
    REPORTS_DIR,
    audit_as_of_for_trade_date,
    bootstrap_telemetry_if_missing,
    classify_recall_hit,
    pick_top_headlines,
    resolve_audit_trade_date,
    should_skip_weekend,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def run_recall_audit(trade_date, force: bool = False) -> Path:
    movers, source = bootstrap_telemetry_if_missing(trade_date)
    as_of = audit_as_of_for_trade_date(trade_date)
    window_start = as_of - timedelta(hours=24)

    lines: List[str] = [
        f"# Catalyst recall audit — {trade_date.isoformat()}",
        "",
        f"- Generated: {datetime.now(tz=NY).strftime('%Y-%m-%d %H:%M ET')}",
        f"- Movers source: `{source}`",
        f"- News window: {window_start.strftime('%Y-%m-%d %H:%M ET')} → "
        f"{as_of.strftime('%Y-%m-%d %H:%M ET')}",
        f"- Hit threshold: 35% headline token overlap",
        "",
        "| Ticker | Move | Pipeline catalyst | Best Massive headline (24h) | Overlap | Result |",
        "| --- | ---: | --- | --- | ---: | --- |",
    ]

    hits = 0
    for mover in movers:
        ticker = mover["ticker"]
        if not ticker:
            continue
        side = mover.get("side") or ("gainer" if mover["change_percentage"] >= 0 else "loser")
        pipeline = mover.get("catalyst") or ""

        news = get_news_for_ticker(ticker, limit=25)
        top5 = pick_top_headlines(news, window_start, as_of, side, limit=5)
        hit, best_headline, overlap = classify_recall_hit(pipeline, top5)
        if hit:
            hits += 1

        pct = mover["change_percentage"]
        move = f"{pct:+.2f}%"
        pipe_cell = (pipeline or "—").replace("|", "\\|")[:120]
        massive_cell = (best_headline or "—").replace("|", "\\|")[:120]
        result = "hit" if hit else "miss"
        lines.append(
            f"| {ticker} | {move} | {pipe_cell} | {massive_cell} | {overlap:.0%} | {result} |"
        )

    total = len([m for m in movers if m.get("ticker")])
    lines.extend(
        [
            "",
            f"**Summary:** {hits}/{total} hits ({(hits / total * 100) if total else 0:.0f}%)",
        ]
    )

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORTS_DIR / f"catalyst_recall_{trade_date.isoformat()}.md"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Wrote %s (%s/%s hits)", out_path, hits, total)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Catalyst recall quality audit")
    parser.add_argument("--trade-date", help="YYYY-MM-DD (default: resolved session)")
    parser.add_argument("--force-weekend", action="store_true")
    args = parser.parse_args()

    if should_skip_weekend() and not args.force_weekend:
        logger.info("Weekend — skipping recall audit (use --force-weekend or set CATALYST_QUALITY_AUDIT_TASK)")
        return 0

    if args.trade_date:
        trade_date = datetime.strptime(args.trade_date, "%Y-%m-%d").date()
    else:
        trade_date = resolve_audit_trade_date()

    run_recall_audit(trade_date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
