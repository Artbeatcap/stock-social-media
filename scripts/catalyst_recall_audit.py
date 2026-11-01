#!/usr/bin/env python3
"""Daily catalyst recall audit — pipeline catalyst vs Massive headlines."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.catalyst_audit_common import (  # noqa: E402
    NY,
    TELEMETRY_ROOT,
    audit_as_of_for_trade_date,
    bootstrap_telemetry_if_missing,
    evaluate_catalyst_recall,
    fetch_news_in_window,
    resolve_audit_trade_date,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("catalyst_recall_audit")


def _format_headline_list(headlines: list) -> str:
    if not headlines:
        return "_(no Massive headlines in 24h window)_"
    lines = []
    for idx, item in enumerate(headlines, start=1):
        lines.append(f"{idx}. {item.get('headline', '').strip()}")
    return "\n".join(lines)


def run_recall_audit(trade_date=None, *, now: datetime | None = None) -> Path:
    now = now or datetime.now(NY)
    trade_date = trade_date or resolve_audit_trade_date(now)
    as_of = audit_as_of_for_trade_date(trade_date, now)
    window_start = as_of - timedelta(hours=24)

    gainers, losers, _post = bootstrap_telemetry_if_missing(trade_date)
    movers = gainers + losers

    report_dir = TELEMETRY_ROOT / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"catalyst_recall_{trade_date.isoformat()}.md"

    hits = 0
    rows: list[str] = []
    for mover in movers:
        ticker = mover.get("ticker") or ""
        if not ticker:
            continue
        pct = float(mover.get("change_percentage") or 0.0)
        pipeline = str(mover.get("catalyst") or "").strip()
        headlines = fetch_news_in_window(ticker, window_start, as_of, top_n=5)
        verdict, massive_headline, overlap, score = evaluate_catalyst_recall(
            pipeline,
            headlines,
            change_pct=pct,
            as_of=as_of,
        )
        if verdict == "hit":
            hits += 1
        rows.append(
            "\n".join(
                [
                    f"### {ticker} ({pct:+.2f}%) — **{verdict}**",
                    f"- **Pipeline catalyst:** {pipeline or '_(empty)_'}",
                    f"- **Best Massive match:** {massive_headline or '_(none)_'} "
                    f"(token overlap {overlap:.0%}, headline score {score:.2f})",
                    "- **Top Massive headlines (24h):**",
                    _format_headline_list(headlines),
                ]
            )
        )

    total = len(rows)
    summary = f"**Recall summary:** {hits}/{total} hits (≥35% token overlap vs top-5 Massive headlines)"
    body = "\n\n".join(
        [
            f"# Catalyst recall audit — {trade_date.isoformat()}",
            "",
            f"_Generated {now.strftime('%Y-%m-%d %H:%M:%S %Z')}_",
            f"_News window: {window_start.strftime('%Y-%m-%d %H:%M %Z')} → {as_of.strftime('%Y-%m-%d %H:%M %Z')}_",
            "",
            summary,
            "",
            "---",
            "",
            *rows,
            "",
        ]
    )
    report_path.write_text(body, encoding="utf-8")
    logger.info("Wrote %s (%s)", report_path, summary)
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Catalyst recall quality audit")
    parser.add_argument("--trade-date", help="YYYY-MM-DD session date (default: resolved from ET clock)")
    args = parser.parse_args()
    trade_date = None
    if args.trade_date:
        trade_date = datetime.strptime(args.trade_date, "%Y-%m-%d").date()
    run_recall_audit(trade_date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
