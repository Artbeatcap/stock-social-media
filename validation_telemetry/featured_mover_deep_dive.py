#!/usr/bin/env python3
"""
Featured-mover deep dive: 7d news narrative, intraday move timing vs news, options flags.

Reads validation_telemetry/postmarket_sent/{date}.json for featured ticker resolution.
Appends ## Featured: $TICKER to validation_telemetry/reports/daily_research_log.md

Cron (ET weekdays 5:30pm): 30 17 * * 1-5
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from massive_client import (  # noqa: E402
    _as_float,
    _as_int,
    _normalize_news_item,
    get_minute_aggregates,
    get_options_snapshot,
)

from validation_telemetry.catalyst_audit_lib import (  # noqa: E402
    NY,
    ensure_dirs,
    featured_ticker_from_postmarket_payload,
    load_postmarket_snapshot,
    telemetry_date_et,
)


def _weekday_ok(d: datetime) -> bool:
    return d.weekday() < 5


def _format_et_from_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=NY).strftime("%Y-%m-%d %H:%M ET")


def _norm_epoch_ms(item: Dict[str, Any]) -> int:
    ts = int(item.get("datetime") or 0)
    return ts * 1000 if ts < 1_000_000_000_000 else ts


def _news_last_7_days(ticker: str, limit: int = 30) -> List[Dict[str, Any]]:
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
    out: List[Dict[str, Any]] = []
    for item in data.get("results", []) or []:
        out.append(_normalize_news_item(item))
    return out


def _largest_bar_moves(
    bars: List[Dict[str, Any]],
    top_n: int = 3,
) -> List[Tuple[int, float, str]]:
    """Largest absolute 1m open-to-close % move."""
    rows: List[Tuple[int, float, str]] = []
    for b in bars:
        o, c = b["o"], b["c"]
        if not o:
            continue
        pct_from_open = (c - o) / o * 100.0
        rows.append((b["t_ms"], pct_from_open, "1m_open_to_close"))
    rows.sort(key=lambda x: abs(x[1]), reverse=True)
    return rows[:top_n]


def _iv_from_contract(c: Dict[str, Any]) -> Optional[float]:
    for key in ("implied_volatility", "impliedVolatility", "iv"):
        v = c.get(key)
        if v is None and isinstance(c.get("day"), dict):
            v = c["day"].get(key)
        if v is not None:
            try:
                x = float(v)
                if math.isnan(x):
                    continue
                return x
            except (TypeError, ValueError):
                continue
    g = c.get("greeks") if isinstance(c.get("greeks"), dict) else {}
    iv = g.get("iv") or g.get("implied_volatility") or g.get("impliedVolatility")
    if iv is not None:
        try:
            return float(iv)
        except (TypeError, ValueError):
            pass
    return None


def _oi_from_contract(c: Dict[str, Any]) -> int:
    for key in ("open_interest", "openInterest"):
        v = c.get(key)
        if v is None and isinstance(c.get("day"), dict):
            v = c["day"].get(key)
        if v is not None:
            return _as_int(v)
    return 0


def _strike_from_contract(c: Dict[str, Any]) -> Optional[float]:
    for key in ("strike_price", "strikePrice", "strike"):
        v = c.get(key)
        if v is None and isinstance(c.get("details"), dict):
            v = c["details"].get(key)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


def _contract_type(c: Dict[str, Any]) -> str:
    for key in ("contract_type", "contractType", "type"):
        v = c.get(key) or (c.get("details") or {}).get(key)
        if isinstance(v, str) and v.lower() in ("call", "put"):
            return v.lower()
    t = str(c.get("ticker") or "")
    if "C" in t[-15:] or "Call" in t:
        return "call"
    if "P" in t[-15:] or "Put" in t:
        return "put"
    return ""


def _analyze_options(contracts: List[Dict[str, Any]]) -> List[str]:
    if not contracts:
        return ["No options snapshot data (tier or symbol)."]
    iv_rows: List[Tuple[float, float, str]] = []
    for c in contracts:
        iv = _iv_from_contract(c)
        if iv is None:
            continue
        strike = _strike_from_contract(c)
        ctype = _contract_type(c) or "?"
        iv_rows.append((iv, strike or 0.0, ctype))
    ivs_f = [r[0] for r in iv_rows]
    lines: List[str] = []
    if ivs_f:
        med = statistics.median(ivs_f)
        iv_rows.sort(key=lambda x: x[0], reverse=True)
        top_iv = iv_rows[:5]
        lines.append(
            f"IV snapshot: median ~{med:.4f} across {len(ivs_f)} contracts with IV."
        )
        lines.append(
            "Highest IV strikes (single snapshot — compare day-over-day in dashboard): "
            + "; ".join(
                f"{ct} ${stk:g} IV={iv:.4f}" if stk else f"{ct} IV={iv:.4f}"
                for iv, stk, ct in top_iv
            )
        )
    oi_pairs: List[Tuple[int, str, float, str]] = []
    for c in contracts:
        oi = _oi_from_contract(c)
        if oi < 100:
            continue
        k = _strike_from_contract(c) or 0.0
        ct = _contract_type(c) or "?"
        oi_pairs.append((oi, str(c.get("ticker") or ""), k, ct))
    oi_pairs.sort(reverse=True)
    if oi_pairs:
        top = oi_pairs[:5]
        lines.append(
            "Highest open interest (possible buildup): "
            + "; ".join(f"{ct} ${stk:g} OI={oi}" for oi, _, stk, ct in top)
        )
    return lines if lines else ["Snapshot present but could not parse IV/OI fields (check API shape)."]


def main() -> int:
    parser = argparse.ArgumentParser(description="Append featured mover deep dive to daily research log")
    parser.add_argument("--date", help="Trading date ET YYYY-MM-DD (default: today ET)")
    parser.add_argument("--skip-weekend-check", action="store_true")
    args = parser.parse_args()

    ensure_dirs()
    trading_date = args.date or telemetry_date_et()
    dt = datetime.strptime(trading_date, "%Y-%m-%d").replace(tzinfo=NY)
    if not args.skip_weekend_check and not _weekday_ok(dt):
        print(f"Skip: {trading_date} is a weekend")
        return 0

    snap = load_postmarket_snapshot(trading_date)
    if not snap:
        print(f"No telemetry at validation_telemetry/postmarket_sent/{trading_date}.json")
        return 1

    ticker = featured_ticker_from_postmarket_payload(snap)
    if not ticker:
        print("Could not resolve featured ticker from snapshot")
        return 1

    news = _news_last_7_days(ticker, limit=40)
    now_ts = int(datetime.now(tz=NY).timestamp())
    week_ago = now_ts - 7 * 24 * 3600
    news_week = []
    for n in news:
        ts = int(n.get("datetime") or 0)
        ts_s = ts if ts < 1_000_000_000_000 else ts // 1000
        if ts_s >= week_ago:
            news_week.append(n)

    bars = get_minute_aggregates(ticker, trading_date, trading_date, limit=5000)
    move_rows = _largest_bar_moves(bars, top_n=5)
    contracts = get_options_snapshot(ticker, limit=250)

    lines: List[str] = [
        "",
        f"## Featured: ${ticker} — {trading_date} (ET)",
        "",
        f"_From postmarket snapshot `postmarket_sent/{trading_date}.json`._",
        "",
        "### Multi-day narrative (Massive news, last 7 days)",
        "",
    ]
    if not news_week:
        lines.append("_No headlines returned for this window._")
    else:
        for i, n in enumerate(news_week[:12], 1):
            lines.append(
                f"{i}. **{n.get('headline', '')}** — {_format_et_from_ms(_norm_epoch_ms(n))} ({n.get('source', '')})"
            )

    lines.extend(
        [
            "",
            "### Intraday price move (1m bars, session day)",
            "",
        ]
    )
    if not bars:
        lines.append("_No minute bars returned (check API key or symbol)._")
    else:
        lines.append(f"Bars: {len(bars)}")
        for tms, pct, kind in move_rows:
            lines.append(f"- {_format_et_from_ms(tms)} — {kind}: **{pct:+.3f}%**")
        if news_week and bars:
            lines.append("")
            lines.append("**News within ±15 minutes of largest |move| bar:**")
            top_ts = move_rows[0][0]
            if top_ts:
                nearby = []
                for n in news_week:
                    n_ms = _norm_epoch_ms(n)
                    if abs(n_ms - top_ts) <= 15 * 60 * 1000:
                        nearby.append(n)
                if nearby:
                    for n in nearby[:5]:
                        lines.append(
                            f"- {_format_et_from_ms(_norm_epoch_ms(n))} — {n.get('headline', '')}"
                        )
                else:
                    lines.append("_No headline timestamps within ±15m of that bar._")

    lines.extend(["", "### Options chain snapshot (unusual IV / OI)", ""])
    lines.extend([f"- {x}" for x in _analyze_options(contracts)])

    lines.append("")
    log_path = _REPO / "validation_telemetry" / "reports" / "daily_research_log.md"
    sep = f"\n\n<!-- featured-dive:{trading_date}:{ticker} -->\n"
    block = sep.strip("\n") + "\n" + "\n".join(lines) + "\n"

    if log_path.is_file():
        existing = log_path.read_text(encoding="utf-8")
        marker = f"<!-- featured-dive:{trading_date}:{ticker} -->"
        if marker in existing:
            print(f"Section already exists for {ticker} on {trading_date}")
            return 0
        log_path.write_text(existing.rstrip() + "\n\n" + block.lstrip(), encoding="utf-8")
    else:
        log_path.write_text("# Daily research log\n\n" + block.lstrip(), encoding="utf-8")

    print(f"Appended featured section to {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
