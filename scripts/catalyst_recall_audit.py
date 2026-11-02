#!/usr/bin/env python3
"""Daily catalyst recall audit — compare pipeline catalysts to Massive headlines."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.catalyst_audit_common import (  # noqa: E402
    audit_as_of_for_trade_date,
    bootstrap_telemetry_if_missing,
    ensure_report_dirs,
    evaluate_catalyst_recall,
    ny_now,
    rank_headlines_for_mover,
    resolve_audit_trade_date,
    should_skip_weekend,
    TELEMETRY_ROOT,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def build_report(trade_date, force_weekend: bool = False) -> Path:
    if should_skip_weekend(force=force_weekend):
        logger.info("Weekend — skipping recall audit (use --force-weekend to override)")
        return TELEMETRY_ROOT / "reports" / f"catalyst_recall_{trade_date.isoformat()}.md"

    ensure_report_dirs()
    as_of = audit_as_of_for_trade_date(trade_date)
    window_start = as_of - timedelta(hours=24)

    gainers, losers = bootstrap_telemetry_if_missing(trade_date)
    rows: list[str] = []
    hits = 0
    total = 0

    for mover in gainers + losers:
        ticker = mover.get("ticker") or ""
        if not ticker:
            continue
        is_gainer = mover.get("side") == "gainer" or float(mover.get("change_percentage") or 0) >= 0
        ranked = rank_headlines_for_mover(
            ticker,
            window_start=window_start,
            window_end=as_of,
            is_gainer=is_gainer,
            limit=5,
        )
        result = evaluate_catalyst_recall(str(mover.get("catalyst") or ""), ranked)
        total += 1
        if result["hit"]:
            hits += 1
        pct = float(mover.get("change_percentage") or 0)
        verdict = "hit" if result["hit"] else "miss"
        rows.append(
            f"| {ticker} | {pct:+.2f}% | {result['pipeline_catalyst'][:80]} | "
            f"{result['massive_best'][:80]} | {result['overlap']:.0%} | {verdict} |"
        )

    report_path = TELEMETRY_ROOT / "reports" / f"catalyst_recall_{trade_date.isoformat()}.md"
    body = "\n".join(
        [
            f"# Catalyst recall audit — {trade_date.isoformat()}",
            "",
            f"- Generated: {ny_now().isoformat()}",
            f"- Trade date: {trade_date.isoformat()}",
            f"- Massive news window: `{window_start.isoformat()}` → `{as_of.isoformat()}` (24h)",
            f"- Hit threshold: token overlap ≥ 35%; headline ranking: 55% recency + 45% sentiment",
            f"- Scorecard: **{hits}/{total} hits**",
            "",
            "| Ticker | Move | Pipeline catalyst | Massive best headline | Overlap | Result |",
            "| --- | --- | --- | --- | --- | --- |",
            *rows,
            "",
            "## Top Massive headlines (per ticker)",
            "",
        ]
    )

    for mover in gainers + losers:
        ticker = mover.get("ticker") or ""
        if not ticker:
            continue
        is_gainer = mover.get("side") == "gainer" or float(mover.get("change_percentage") or 0) >= 0
        ranked = rank_headlines_for_mover(
            ticker,
            window_start=window_start,
            window_end=as_of,
            is_gainer=is_gainer,
            limit=5,
        )
        body += f"### {ticker}\n\n"
        if not ranked:
            body += "_No Massive headlines in 24h window._\n\n"
            continue
        for i, art in enumerate(ranked, 1):
            body += (
                f"{i}. ({art['published'].strftime('%Y-%m-%d %H:%M ET')}, "
                f"score={art['score']}) {art['headline']}\n"
            )
        body += "\n"

    report_path.write_text(body, encoding="utf-8")
    logger.info("Wrote %s (%s/%s hits)", report_path, hits, total)
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Catalyst recall quality audit")
    parser.add_argument("--trade-date", help="YYYY-MM-DD (default: resolve_audit_trade_date)")
    parser.add_argument("--force-weekend", action="store_true")
    args = parser.parse_args()

    if args.trade_date:
        from datetime import date

        trade_date = date.fromisoformat(args.trade_date)
    else:
        trade_date = resolve_audit_trade_date()

    build_report(trade_date, force_weekend=args.force_weekend)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
