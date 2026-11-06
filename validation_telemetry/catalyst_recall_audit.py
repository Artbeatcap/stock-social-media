#!/usr/bin/env python3
"""
Nightly catalyst recall audit: compare pipeline catalyst text to Massive top headlines.

Reads validation_telemetry/postmarket_sent/{date}.json (--date in America/New_York).
Writes validation_telemetry/reports/catalyst_recall_{date}.md

Cron (ET weekdays 9:15pm): 15 21 * * 1-5
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from massive_client import _normalize_news_item  # noqa: E402

from validation_telemetry.catalyst_audit_lib import (  # noqa: E402
    NY,
    classify_hit,
    combined_sentiment,
    ensure_dirs,
    jaccard,
    load_postmarket_snapshot,
    score_headline,
    telemetry_date_et,
    tokenize,
)


def _weekday_ok(d: datetime) -> bool:
    return d.weekday() < 5


def _collect_movers(payload: dict) -> list[dict]:
    ctx = payload.get("context") or {}
    out: list[dict] = []
    seen: set[str] = set()
    for label, side in (("gainer", "top_gainers"), ("loser", "top_losers")):
        for m in ctx.get(side) or []:
            t = (m.get("ticker") or m.get("symbol") or "").strip().upper()
            if not t or t in seen:
                continue
            seen.add(t)
            row = dict(m)
            row["_side"] = label
            row["ticker"] = t
            out.append(row)
    return out


def _raw_items_for_ticker(ticker: str, limit: int) -> list[dict]:
    from massive_client import _request

    data = _request(
        "/v2/reference/news",
        {
            "ticker": ticker.upper(),
            "limit": limit,
            "order": "desc",
            "sort": "published_utc",
        },
    )
    return list(data.get("results", []) or [])


def main() -> int:
    parser = argparse.ArgumentParser(description="Catalyst recall audit vs Massive headlines")
    parser.add_argument(
        "--date",
        help="Trading date ET YYYY-MM-DD (default: today ET)",
    )
    parser.add_argument(
        "--skip-weekend-check",
        action="store_true",
        help="Run even if the date falls on a weekend (for backfills)",
    )
    args = parser.parse_args()

    ensure_dirs()
    trading_date = args.date or telemetry_date_et()
    dt = datetime.strptime(trading_date, "%Y-%m-%d").replace(tzinfo=NY)
    if not args.skip_weekend_check and not _weekday_ok(dt):
        print(f"Skip: {trading_date} is a weekend (use --skip-weekend-check to run)")
        return 0

    snap = load_postmarket_snapshot(trading_date)
    if not snap:
        print(f"No telemetry for {trading_date} at validation_telemetry/postmarket_sent/{trading_date}.json")
        return 1

    movers = _collect_movers(snap)
    if not movers:
        print("No top_gainers/top_losers in snapshot")
        return 1

    now_ts = int(datetime.now(tz=NY).timestamp())
    window_sec = 24 * 3600

    lines: list[str] = [
        f"# Catalyst recall audit — {trading_date} (ET)",
        "",
        f"Telemetry: `validation_telemetry/postmarket_sent/{trading_date}.json`",
        f"Generated: {datetime.now(tz=NY).strftime('%Y-%m-%d %H:%M %Z')}",
        "",
        "| Ticker | Side | % move | Pipeline catalyst | Best Massive headline | Score | Top 5 Massive (24h window) | Verdict |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for m in movers:
        ticker = m["ticker"]
        pct = float(m.get("change_percentage") or 0)
        side = m.get("_side") or ("gainer" if pct >= 0 else "loser")
        expected = 1 if pct >= 0 else -1
        pipeline = (m.get("catalyst") or "").strip()
        pipeline_display = pipeline if pipeline else "(none)"
        score_pipeline = pipeline if pipeline else " "

        raw = _raw_items_for_ticker(ticker, limit=50)
        normalized: list[dict] = []
        for item in raw:
            norm = _normalize_news_item(item)
            ts = int(norm.get("datetime") or now_ts)
            if now_ts - ts > window_sec:
                continue
            normalized.append(norm)
        top5 = normalized[:5]

        best_h = ""
        best_s = -1.0
        best_j = 0.0
        sentiment_ok = False
        for item in top5:
            hl = str(item.get("headline") or "")
            sc = score_headline(score_pipeline, expected, hl, item, now_ts, window_sec)
            if not pipeline:
                sc = 0.35 * (1.0 if combined_sentiment(hl, item) == expected else 0.25) + 0.65 * (
                    1.0 - min(1.0, (now_ts - int(item.get("datetime") or now_ts)) / window_sec)
                )
            jac_v = jaccard(tokenize(pipeline), tokenize(hl)) if pipeline else 0.0
            if sc > best_s:
                best_s = sc
                best_h = hl
                best_j = jac_v
            if combined_sentiment(hl, item) == expected:
                sentiment_ok = True

        verdict = classify_hit(best_s, best_j, sentiment_ok)

        def esc(cell: str) -> str:
            return cell.replace("|", "\\|").replace("\n", " ")

        multi = "; ".join(
            f"{i + 1}) {esc(str(x.get('headline') or '')[:120])}"
            for i, x in enumerate(top5)
        ) or "(no Massive headlines in 24h)"

        lines.append(
            f"| {ticker} | {side} | {pct:+.2f}% | {esc(pipeline_display[:200])} | "
            f"{esc((best_h or '')[:200])} | {best_s:.3f} | {esc(multi[:500])} | **{verdict}** |"
        )

    out_path = _REPO / "validation_telemetry" / "reports" / f"catalyst_recall_{trading_date}.md"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
