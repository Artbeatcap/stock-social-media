#!/usr/bin/env python3
"""Compare pipeline catalyst strings against Massive headlines (24h window)."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.catalyst_audit_common import (  # noqa: E402
    HIT_OVERLAP_THRESHOLD,
    REPORTS_DIR,
    audit_as_of_for_trade_date,
    best_match,
    bootstrap_telemetry_if_missing,
    ensure_telemetry_dirs,
    news_in_window,
    now_ny,
    rank_top_headlines,
    resolve_audit_trade_date,
)


def build_report(trade_date, force: bool = False) -> Path:
    ensure_telemetry_dirs()
    gainers, losers, post = bootstrap_telemetry_if_missing(trade_date)
    window_end = audit_as_of_for_trade_date(trade_date)
    window_start = window_end - timedelta(hours=24)

    lines = [
        f"# Catalyst recall audit — {trade_date.isoformat()}",
        "",
        f"- Generated: {now_ny().strftime('%Y-%m-%d %H:%M %Z')}",
        f"- News window: {window_start.strftime('%Y-%m-%d %H:%M %Z')} → "
        f"{window_end.strftime('%Y-%m-%d %H:%M %Z')}",
        f"- Hit threshold: {int(HIT_OVERLAP_THRESHOLD * 100)}% headline token overlap",
        "",
    ]
    if post.get("bootstrapped"):
        lines.append("- Note: telemetry bootstrapped from live market internals (no production snapshot).")
        lines.append("")

    rows: list[tuple[str, float, str, str, str, float]] = []
    hits = 0
    total = 0

    for mover in gainers + losers:
        ticker = mover["ticker"]
        if not ticker:
            continue
        is_gainer = mover["side"] == "gainer"
        pipeline = mover.get("catalyst") or "(empty)"
        items = news_in_window(ticker, window_start, window_end)
        top5 = rank_top_headlines(
            items,
            is_gainer=is_gainer,
            window_start=window_start,
            window_end=window_end,
        )
        match, overlap = best_match(pipeline, top5)
        verdict = "hit" if overlap >= HIT_OVERLAP_THRESHOLD else "miss"
        if verdict == "hit":
            hits += 1
        total += 1
        massive_line = str((match or {}).get("headline") or "(no scored headline)")
        rows.append(
            (
                ticker,
                mover["change_percentage"],
                pipeline,
                massive_line,
                verdict,
                overlap,
            )
        )

    lines.append(f"**Summary:** {hits}/{total} hits")
    lines.append("")
    lines.append("| Ticker | Move | Pipeline catalyst | Best Massive headline (top-5) | Result | Overlap |")
    lines.append("| --- | ---: | --- | --- | --- | ---: |")
    for ticker, pct, pipeline, massive, verdict, overlap in rows:
        pipe_cell = pipeline.replace("|", "\\|")[:120]
        mass_cell = massive.replace("|", "\\|")[:120]
        lines.append(
            f"| {ticker} | {pct:+.2f}% | {pipe_cell} | {mass_cell} | **{verdict}** | {overlap:.0%} |"
        )
    lines.append("")

    out_path = REPORTS_DIR / f"catalyst_recall_{trade_date.isoformat()}.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Catalyst recall quality audit")
    parser.add_argument("--trade-date", type=str, help="YYYY-MM-DD session to audit")
    parser.add_argument("--force", action="store_true", help="Overwrite existing report")
    args = parser.parse_args()

    if args.trade_date:
        trade_date = datetime.strptime(args.trade_date, "%Y-%m-%d").date()
    else:
        trade_date = resolve_audit_trade_date()

    out_path = REPORTS_DIR / f"catalyst_recall_{trade_date.isoformat()}.md"
    if out_path.exists() and not args.force:
        print(f"Report exists: {out_path}")
        return 0

    path = build_report(trade_date, force=args.force)
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
