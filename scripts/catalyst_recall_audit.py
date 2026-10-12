#!/usr/bin/env python3
"""
Catalyst recall audit — compare pipeline catalyst strings against Massive headlines.

Writes validation_telemetry/reports/catalyst_recall_YYYY-MM-DD.md
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.audit_common import (  # noqa: E402
    NY,
    REPORTS_DIR,
    audit_as_of_for_trade_date,
    bootstrap_telemetry_if_missing,
    catalyst_hit,
    effective_audit_trade_date,
    ensure_telemetry_dirs,
    filter_news_to_window,
    now_ny,
    score_headline,
)
from massive_client import get_news_for_ticker  # noqa: E402


def run_recall_audit(trade_date) -> Path:
    ensure_telemetry_dirs()
    as_of = audit_as_of_for_trade_date(trade_date)
    window_end = as_of
    window_start = window_end - timedelta(hours=24)

    movers, meta = bootstrap_telemetry_if_missing(trade_date)
    lines: List[str] = [
        f"# Catalyst recall audit — {trade_date.isoformat()}",
        "",
        f"- Generated: {now_ny().strftime('%Y-%m-%d %H:%M ET')}",
        f"- News window: {window_start.strftime('%Y-%m-%d %H:%M ET')} → {window_end.strftime('%Y-%m-%d %H:%M ET')}",
        f"- Mover source: {meta.get('source', 'unknown')}",
        "",
        "| Ticker | Move | Side | Pipeline catalyst | Best Massive headline (24h) | Hit/Miss |",
        "| --- | ---: | --- | --- | --- | --- |",
    ]

    hits = 0
    evaluated = 0
    for mover in movers:
        ticker = mover.get("ticker", "")
        if not ticker:
            continue
        pct = float(mover.get("change_percentage") or 0.0)
        side = mover.get("side") or ("gainer" if pct >= 0 else "loser")
        pipeline = str(mover.get("catalyst") or "").strip()

        news = get_news_for_ticker(ticker, limit=25)
        in_window = filter_news_to_window(news, window_start, window_end)
        in_window.sort(
            key=lambda item: score_headline(item, pipeline, side, window_start, window_end),
            reverse=True,
        )
        top5 = in_window[:5]
        if not top5 and news:
            top5 = news[:5]

        hit, _ = catalyst_hit(pipeline, top5)
        best = top5[0] if top5 else {}
        best_headline = str(best.get("headline") or "(no Massive headlines in window)")
        if pipeline:
            evaluated += 1
            if hit:
                hits += 1
        verdict = "hit" if hit else ("miss" if pipeline else "n/a")

        def esc(s: str) -> str:
            return s.replace("|", "/").replace("\n", " ")[:200]

        lines.append(
            f"| {ticker} | {pct:+.2f}% | {side} | {esc(pipeline or '—')} | {esc(best_headline)} | {verdict} |"
        )

    lines.extend(
        [
            "",
            f"**Summary:** {hits}/{evaluated} hits (≥35% token overlap vs top-5 scored headlines).",
        ]
    )
    if meta.get("bootstrap_error"):
        lines.append(f"\n_Bootstrap note: {meta['bootstrap_error']}_")

    out_path = REPORTS_DIR / f"catalyst_recall_{trade_date.isoformat()}.md"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Catalyst recall quality audit")
    parser.add_argument("--date", help="Trade date YYYY-MM-DD (default: effective trade date)")
    args = parser.parse_args()
    trade_date = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else effective_audit_trade_date()
    path = run_recall_audit(trade_date)
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
