#!/usr/bin/env python3
"""
Catalyst recall audit — compare pipeline catalyst strings to Massive headlines (24h).
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from catalyst_audit_common import (  # noqa: E402
    RECALL_HIT_OVERLAP,
    REPORTS_DIR,
    audit_as_of_for_trade_date,
    bootstrap_telemetry_if_missing,
    effective_audit_trade_date,
    load_movers_for_trade_date,
    news_window_start,
    parse_published_utc,
    score_headline,
    sentiment_for_ticker,
    token_overlap,
)
from massive_client import get_news_for_ticker_raw  # noqa: E402

import pytz

NY = pytz.timezone("America/New_York")
logger = logging.getLogger(__name__)


def _filter_news_24h(items: List[dict], ticker: str, window_start, as_of) -> List[dict]:
    out: List[dict] = []
    for item in items:
        pub = parse_published_utc(item.get("published_utc") or item.get("datetime"))
        if pub is None:
            continue
        if window_start <= pub <= as_of:
            out.append(item)
    out.sort(key=lambda x: parse_published_utc(x.get("published_utc")) or datetime.min.replace(tzinfo=NY), reverse=True)
    return out[:5]


def audit_mover(
    mover: Dict[str, Any],
    is_gainer: bool,
    as_of: datetime,
) -> Dict[str, Any]:
    ticker = str(mover.get("ticker") or "").upper()
    pct = float(mover.get("change_percentage") or 0)
    pipeline_catalyst = str(mover.get("catalyst") or "").strip()

    window_start = news_window_start(as_of, hours=24)
    raw_items = get_news_for_ticker_raw(ticker, limit=25)
    top5 = _filter_news_24h(raw_items, ticker, window_start, as_of)

    best_title = ""
    best_score = -1.0
    for item in top5:
        title = str(item.get("title") or item.get("headline") or "")
        pub = parse_published_utc(item.get("published_utc"))
        if pub is None:
            continue
        sent = sentiment_for_ticker(item, ticker)
        sc = score_headline(
            title,
            pub,
            sent,
            is_gainer=is_gainer,
            window_start=window_start,
            as_of=as_of,
        )
        if sc > best_score:
            best_score = sc
            best_title = title

    overlap = token_overlap(pipeline_catalyst, best_title) if pipeline_catalyst and best_title else 0.0
    hit = overlap >= RECALL_HIT_OVERLAP

    return {
        "ticker": ticker,
        "pct_move": pct,
        "pipeline_catalyst": pipeline_catalyst or "(none)",
        "massive_headline": best_title or "(no headlines in 24h window)",
        "overlap": round(overlap, 3),
        "hit": hit,
    }


def render_report(
    trade_date,
    rows: List[Dict[str, Any]],
    as_of: datetime,
    bootstrapped: bool,
) -> str:
    hits = sum(1 for r in rows if r["hit"])
    lines = [
        f"# Catalyst recall audit — {trade_date.isoformat()}",
        "",
        f"- As of: {as_of.strftime('%Y-%m-%d %H:%M %Z')}",
        f"- Movers audited: {len(rows)}",
        f"- Hits (≥{int(RECALL_HIT_OVERLAP * 100)}% token overlap): **{hits}/{len(rows)}**",
    ]
    if bootstrapped:
        lines.append("- Note: telemetry bootstrapped from `market_internals` (no prod send snapshot)")
    lines.extend(["", "| Ticker | % move | Pipeline catalyst | Massive (best 24h) | Hit |", "| --- | ---: | --- | --- | --- |"])
    for r in rows:
        hit = "hit" if r["hit"] else "miss"
        catalyst = (r["pipeline_catalyst"] or "").replace("|", "/")[:120]
        massive = (r["massive_headline"] or "").replace("|", "/")[:120]
        lines.append(
            f"| {r['ticker']} | {r['pct_move']:+.2f}% | {catalyst} | {massive} | {hit} |"
        )
    lines.append("")
    return "\n".join(lines)


def run_audit(trade_date=None, now=None) -> Path:
    now = now or datetime.now(NY)
    trade_date = trade_date or effective_audit_trade_date(now)
    as_of = audit_as_of_for_trade_date(trade_date, now)

    bootstrapped = bootstrap_telemetry_if_missing(trade_date)
    gainers, losers = load_movers_for_trade_date(trade_date)

    rows: List[Dict[str, Any]] = []
    for m in gainers:
        rows.append(audit_mover(m, is_gainer=True, as_of=as_of))
    for m in losers:
        rows.append(audit_mover(m, is_gainer=False, as_of=as_of))

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORTS_DIR / f"catalyst_recall_{trade_date.isoformat()}.md"
    out_path.write_text(render_report(trade_date, rows, as_of, bootstrapped), encoding="utf-8")
    logger.info("Wrote %s (%d movers, %d hits)", out_path, len(rows), sum(1 for r in rows if r["hit"]))
    return out_path


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Catalyst recall audit vs Massive news")
    parser.add_argument("--date", help="Trade date YYYY-MM-DD (default: effective session)")
    args = parser.parse_args()
    trade_date = None
    if args.date:
        trade_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    path = run_audit(trade_date=trade_date)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
