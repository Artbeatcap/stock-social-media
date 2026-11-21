#!/usr/bin/env python3
"""Compare pipeline catalyst strings against Massive headlines (24h recall audit)."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.catalyst_audit_common import (  # noqa: E402
    NY,
    TELEMETRY_ROOT,
    audit_as_of_for_trade_date,
    bootstrap_telemetry_if_missing,
    catalyst_recall_verdict,
    fetch_ticker_news,
    rank_top_headlines,
    recall_news_window,
    resolve_audit_trade_date,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _fmt_pct(value: float) -> str:
    return f"{value:+.2f}%"


def run_recall_audit(trade_date=None) -> Path:
    trade_date = trade_date or resolve_audit_trade_date()
    window_start, window_end = recall_news_window(trade_date)
    gainers, losers, source = bootstrap_telemetry_if_missing(trade_date)

    rows: list[str] = []
    hits = 0
    total = 0

    for side, movers in (("gainer", gainers), ("loser", losers)):
        for mover in movers:
            ticker = mover.get("ticker") or ""
            if not ticker:
                continue
            total += 1
            pct = float(mover.get("change_percentage") or 0)
            pipeline = mover.get("catalyst") or ""

            raw_news = fetch_ticker_news(
                ticker,
                limit=20,
                published_gte=window_start,
                published_lte=window_end,
            )
            top5 = rank_top_headlines(
                raw_news,
                mover_pct=pct,
                window_start=window_start,
                window_end=window_end,
                top_n=5,
            )
            verdict, best_headline, overlap = catalyst_recall_verdict(pipeline, top5)
            if verdict == "hit":
                hits += 1

            massive_summary = (
                top5[0].get("headline") if top5 else "(no Massive headlines in 24h window)"
            )
            rows.append(
                f"| {ticker} | {_fmt_pct(pct)} | {side} | "
                f"{pipeline or '*(empty)*'} | {massive_summary or '*(empty)*'} | "
                f"{verdict} ({overlap:.0%} overlap) |"
            )

    report_dir = TELEMETRY_ROOT / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"catalyst_recall_{trade_date.isoformat()}.md"

    as_of = audit_as_of_for_trade_date(trade_date)
    body = f"""# Catalyst recall audit — {trade_date.isoformat()}

Generated: {datetime.now(NY).strftime("%Y-%m-%d %H:%M %Z")}
Mover source: `{source}`
News window: `{window_start.isoformat()}` → `{window_end.isoformat()}` (audit as-of {as_of.strftime("%H:%M %Z")})

Summary: **{hits}/{total} hits** (token overlap ≥ 35% or substring match vs top-5 scored headlines)

| Ticker | Move | Side | Pipeline catalyst | Top Massive headline | Result |
| --- | --- | --- | --- | --- | --- |
"""
    body += "\n".join(rows) + "\n"

    if top5_detail := _headline_appendix(gainers + losers, trade_date, window_start, window_end):
        body += "\n## Top Massive headlines (scored)\n\n" + top5_detail

    report_path.write_text(body, encoding="utf-8")
    logger.info("Wrote %s (%s/%s hits)", report_path, hits, total)
    return report_path


def _headline_appendix(movers, trade_date, window_start, window_end) -> str:
    chunks: list[str] = []
    seen: set[str] = set()
    for mover in movers:
        ticker = mover.get("ticker") or ""
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        pct = float(mover.get("change_percentage") or 0)
        raw_news = fetch_ticker_news(
            ticker,
            limit=20,
            published_gte=window_start,
            published_lte=window_end,
        )
        top5 = rank_top_headlines(
            raw_news,
            mover_pct=pct,
            window_start=window_start,
            window_end=window_end,
            top_n=5,
        )
        if not top5:
            chunks.append(f"### {ticker}\n\n_No headlines in window._\n")
            continue
        lines = [f"### {ticker} ({pct:+.2f}%)", ""]
        for idx, item in enumerate(top5, start=1):
            lines.append(
                f"{idx}. ({item.get('_score', 0):.2f}) {item.get('headline', '')}"
            )
        chunks.append("\n".join(lines) + "\n")
    return "\n".join(chunks)


def main() -> int:
    parser = argparse.ArgumentParser(description="Catalyst recall audit vs Massive news")
    parser.add_argument("--trade-date", help="YYYY-MM-DD session date (default: resolved ET)")
    args = parser.parse_args()
    trade_date = None
    if args.trade_date:
        trade_date = datetime.strptime(args.trade_date, "%Y-%m-%d").date()
    run_recall_audit(trade_date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
