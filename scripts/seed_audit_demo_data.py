#!/usr/bin/env python3
"""Build offline Massive cache + telemetry snapshots for audit dry-runs (no API key)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pytz

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from massive_client import _normalize_news_item  # noqa: E402
from validation_telemetry import write_run_snapshot  # noqa: E402

NY = pytz.timezone("America/New_York")


def _news_from_mcp_results(results: list[dict], ticker: str) -> list[dict]:
    return [_normalize_news_item(item) for item in results]


def build_cache(aggs_path: Path | None) -> dict:
    """Populate offline cache from embedded MCP snapshots (Aug 2026)."""
    amd_news_raw = [
        {
            "title": "Microsoft CEO Satya Nadella Just Announced Great News for AMD Stock Investors",
            "published_utc": "2026-08-04T19:30:00Z",
            "tickers": ["AMD", "MSFT", "INTC", "NVDA"],
            "insights": [{"ticker": "AMD", "sentiment": "positive"}],
        },
        {
            "title": "Cathie Wood's Ark Invest Sold $11.8 Million of AMD Stock on a Single Day Last Month. Should Other Investors Also Sell?",
            "published_utc": "2026-08-03T20:07:00Z",
            "tickers": ["AMD", "TSLA", "META", "CSCO", "NVDA"],
            "insights": [{"ticker": "AMD", "sentiment": "positive"}],
        },
    ]
    nvda_news_raw = [
        {
            "title": "SpaceX Just Guided for A $100 Billion Run Rate by December and $1 Trillion of Revenue by 2030. Here's Why the Stock Is Getting Crushed Anyway",
            "published_utc": "2026-08-05T12:10:02Z",
            "tickers": ["SPCX", "NVDA", "GOOG", "GOOGL"],
            "insights": [{"ticker": "NVDA", "sentiment": "positive"}],
        },
        {
            "title": "Andy Jassy Just Delivered Incredible News for Amazon Stock Investors",
            "published_utc": "2026-08-05T09:30:00Z",
            "tickers": ["AMZN", "NVDA"],
            "insights": [{"ticker": "NVDA", "sentiment": "neutral"}],
        },
    ]
    tsla_news_raw = [
        {
            "title": "Amazon Just Landed a Big Win in the Race Against Tesla and Waymo",
            "published_utc": "2026-08-05T09:15:00Z",
            "tickers": ["AMZN", "TSLA", "GOOG", "GOOGL"],
            "insights": [{"ticker": "TSLA", "sentiment": "negative"}],
        },
        {
            "title": "How Concerned Should Tesla Investors Be About Its Multibillion-Dollar Legal Exposure?",
            "published_utc": "2026-08-04T20:12:00Z",
            "tickers": ["TSLA"],
            "insights": [{"ticker": "TSLA", "sentiment": "negative"}],
        },
    ]
    pltr_news_raw = [
        {
            "title": "Palantir Stock Holds Steady After Strong Government Contract Momentum",
            "published_utc": "2026-08-04T14:00:00Z",
            "tickers": ["PLTR"],
            "insights": [{"ticker": "PLTR", "sentiment": "positive"}],
        },
    ]

    minute_bars: dict[str, dict[str, list]] = {"AMD": {}}
    if aggs_path and aggs_path.exists():
        payload = json.loads(aggs_path.read_text(encoding="utf-8"))
        bars = payload.get("bars") or []
        normalized = []
        for item in bars:
            ts_ms = int(item.get("t") or 0)
            normalized.append(
                {
                    "t": ts_ms,
                    "datetime": datetime.fromtimestamp(ts_ms / 1000, tz=NY).isoformat(
                        timespec="seconds"
                    ),
                    "open": item.get("o"),
                    "high": item.get("h"),
                    "low": item.get("l"),
                    "close": item.get("c"),
                    "volume": int(float(item.get("v") or 0)),
                    "vwap": item.get("vw"),
                }
            )
        minute_bars["AMD"]["2026-08-04"] = normalized

    return {
        "news": {
            "AMD": _news_from_mcp_results(amd_news_raw, "AMD"),
            "NVDA": _news_from_mcp_results(nvda_news_raw, "NVDA"),
            "TSLA": _news_from_mcp_results(tsla_news_raw, "TSLA"),
            "PLTR": _news_from_mcp_results(pltr_news_raw, "PLTR"),
        },
        "minute_bars": minute_bars,
        "options": {
            "AMD": {
                "ok": False,
                "contracts": [],
                "error": "You are not entitled to this data. Please upgrade your plan at https://massive.com/pricing",
            }
        },
    }


def seed_telemetry(trade_date: str) -> None:
    """Simulate a post-market email run with pipeline-style catalyst headlines."""
    context = {
        "top_gainers": [
            {
                "ticker": "NVDA",
                "change_percentage": 1.48,
                "price": 215.08,
                "catalyst": "Andy Jassy Just Delivered Incredible News for Amazon Stock Investors",
                "volume": 134921997,
            },
            {
                "ticker": "PLTR",
                "change_percentage": 0.11,
                "price": 162.84,
                "catalyst": "Palantir Stock Holds Steady After Strong Government Contract Momentum",
                "volume": 175034726,
            },
        ],
        "top_losers": [
            {
                "ticker": "AMD",
                "change_percentage": -7.68,
                "price": 478.73,
                "catalyst": "Microsoft CEO Satya Nadella Just Announced Great News for AMD Stock Investors",
                "volume": 48463564,
            },
            {
                "ticker": "TSLA",
                "change_percentage": -1.02,
                "price": 324.0,
                "catalyst": "Amazon Just Landed a Big Win in the Race Against Tesla and Waymo",
                "volume": 33318303,
            },
        ],
        "featured_mover": {
            "ticker": "AMD",
            "change_percentage": -7.68,
            "catalyst": "Microsoft CEO Satya Nadella Just Announced Great News for AMD Stock Investors",
        },
    }
    post_data = {
        "featured_stock": "AMD",
        "post": "$AMD -7.7% after chip sector rotation; Nadella CPU comments still in focus.",
        "style": "voice-validated",
        "market_internals": {
            "movers": {
                "gainers": [{"symbol": "NVDA", "pct": 1.48, "last": 215.08}],
                "losers": [{"symbol": "AMD", "pct": -7.68, "last": 478.73}],
            }
        },
    }
    write_run_snapshot("postmarket", context, post_data, trade_date=trade_date)


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed offline cache and telemetry for audit dry-runs")
    parser.add_argument("--date", default="2026-08-04", help="Trade date YYYY-MM-DD")
    parser.add_argument(
        "--aggs-file",
        type=Path,
        help="Path to MCP get_stock_aggregates JSON output for minute bars",
    )
    args = parser.parse_args()

    cache_path = REPO_ROOT / "validation_telemetry" / "offline_cache.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(build_cache(args.aggs_file), indent=2), encoding="utf-8")
    print(f"Wrote offline cache: {cache_path}")

    seed_telemetry(args.date)
    print(f"Seeded postmarket telemetry for {args.date}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
