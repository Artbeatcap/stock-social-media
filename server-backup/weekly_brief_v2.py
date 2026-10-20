#!/usr/bin/env python3
"""
weekly_brief_v2.py — Drop-in replacement for build_weekly_brief().

code owns the numbers + the LLM owns the synthesis
(structured DATA FEED -> fixed sections -> voice pass -> anti-fabrication gates)
"""

from __future__ import annotations

import os
import re
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from market_brief_generator import (
    NY,
    BriefDataUnavailable,
    BRIEF_MODEL,
    fetch_stock_prices_strict,
    has_valid_price_data,
    calculate_expected_range,
    fetch_top_movers_av,
    fetch_economic_calendar_range,
    fetch_earnings_calendar_range,
    _tradier_history_daily,
    _pivot_levels_from_hlc,
    _last_completed_week_range,
    _rewrite_in_voice,
)

logger = logging.getLogger("weekly_brief_v2")

_HR_RE = re.compile(r"^\s*-{3,}\s*$", re.MULTILINE)
_GREETING_RE = re.compile(
    r"^\s*((hey|hi|hello)(\s+(guys|everyone|team))?[,.!]*)\s*"
    r"(here is your (morning|weekly) brief[.!]*)?\s*",
    re.IGNORECASE,
)
_SIGNOFF_RE = re.compile(
    r"\s*(have a (great|good) (day|one|week)(\s+guys)?[.!]*)\s*$",
    re.IGNORECASE,
)

JUNK_MIN_PRICE = float(os.getenv("JUNK_MIN_PRICE", "5"))
JUNK_MAX_PRICE = float(os.getenv("JUNK_MAX_PRICE", "2000"))
JUNK_MIN_ABS_PCT = float(os.getenv("JUNK_MIN_ABS_PCT", "3"))
JUNK_MAX_ABS_PCT = float(os.getenv("JUNK_MAX_ABS_PCT", "35"))
JUNK_MIN_VOL = float(os.getenv("JUNK_MIN_PM_VOL", "750000"))

_NO_CATALYST_WHYS = {
    "no headline — flow-driven",
    "no headline - flow-driven",
    "no catalyst in the last 36 hours",
}

# Large, liquid reporters. Used only as a tie-break so a quiet mega-cap
# outranks a no-estimate micro-cap when Finnhub omits revenue.
_EARNINGS_FOCUS = {
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "TSLA", "AVGO",
    "JPM", "V", "UNH", "XOM", "LLY", "MA", "JNJ", "COST", "HD", "PG", "NFLX",
    "AMD", "CRM", "BAC", "WMT", "ORCL", "KO", "PEP", "CVX", "MRK", "ABBV",
    "MCD", "CSCO", "IBM", "GE", "CAT", "BA", "DIS", "INTC", "QCOM", "TXN",
    "AMAT", "NOW", "UBER", "SHOP", "SNOW", "PLTR", "COIN", "ARM",
}
_EARNINGS_FOCUS_BOOST = 50_000_000_000
_SESSION_LABEL = {
    "bmo": "Before the open",
    "amc": "After the close",
    "dmh": "During the session",
}
_WEEK_AHEAD_LIMIT = 6


def _has_news_catalyst(row: Dict[str, Any]) -> bool:
    """A mover with no headline is noise in the email. Drop it."""
    why = str(row.get("why") or "").strip()
    if not why or why.lower() in _NO_CATALYST_WHYS:
        return False
    if row.get("hasCatalyst") is False:
        return False
    return True

SECTOR_ETFS: List[Tuple[str, str]] = [
    ("XLK", "Technology"), ("XLF", "Financials"), ("XLV", "Health Care"),
    ("XLY", "Consumer Discretionary"), ("XLP", "Consumer Staples"),
    ("XLE", "Energy"), ("XLI", "Industrials"), ("XLB", "Materials"),
    ("XLRE", "Real Estate"), ("XLU", "Utilities"), ("XLC", "Communication Svcs"),
]

_BASE_ALLOWED = {"SPY", "QQQ", "IWM", "VIX", "DIA", "TLT", "HYG", "GLD", "USO"}
_BASE_ALLOWED |= {etf for etf, _ in SECTOR_ETFS}

WEEKLY_EMAIL_SYSTEM = """You are the lead market strategist for a sharp, credible fintwit desk
(think Bilello/Unusual Whales register: plain, declarative, no hype, numbers attached to claims).
You write the WEEKLY recap email for US options/swing traders.

NON-NEGOTIABLE RULES
- Use ONLY the DATA FEED. Never invent tickers, levels, percentages, dates, or catalysts.
- Every ticker you name with a $ MUST appear in the feed. If it's not in the feed, don't name it.
- No financial advice. Analysis, context, and level-based scenarios only — never "buy/sell".
- US Eastern Time, US market focus. Round sensibly.
- If a data point is missing, write around it; do not guess or fill it.
- A "%" in the feed is a percent. Never call a percent move "points." Point moves are labeled "points" in the feed.
- Breadth must match the advancer and decliner counts. If decliners outnumber advancers, do not say most stocks held up, or that the market was broad to the upside.

VOICE
- Short sentences. Active voice. Confident, not breathless.
- "The market isn't crashing, it's rotating — know the difference." That register.
- Lead with what changed and why it matters, then the level that decides next week.

OUTPUT FORMAT — emit EXACTLY these H2 sections, in this order, nothing before or after:

## MAJOR DRIVER
One tight paragraph (2-3 sentences): the single biggest thing that defined the week and why it mattered.

## WEEK IN REVIEW
2-4 short paragraphs of connective narrative tying the index moves, the rotation, and breadth together.
Reference the real SPY/QQQ weekly % and the sector leaders/laggards from the feed. This is the heart of the email.

## KEY LEVEL
One sentence (or two): the single most important level to watch next week and what a break/hold means.
Use a level from the feed (pivots or index spot). Name the instrument.

## WEEK AHEAD
1-2 short paragraphs: how next week could unfold given the macro catalysts in the feed and the technical setup.
Name at most TWO calendar catalysts. Do NOT restate the calendar list — that is rendered separately.

## BIGGEST RISK
One paragraph (2-3 sentences): the cleanest bear/bull risk into next week, grounded in feed data
(rates, vol, positioning, the driver).

## MOVER WHY
One line per notable mover, exactly `$TICKER: one factual sentence`.
Use only that mover's headlines. No "prediction", "is it a buy", or "here's why I'm".
Do not quote a percent from a headline unless it matches that mover's percent in the feed.
If the headlines do not state a fact about that company, write `$TICKER: DROP`.
"""


def _fmt_week_label(mon, fri) -> str:
    return f"Week of {mon.strftime('%B')} {mon.day}–{fri.day}, {fri.year}"


def _pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    try:
        if a is None or not b:
            return None
        return (float(a) - float(b)) / float(b) * 100.0
    except Exception:
        return None


def _weekly_index_change(symbol: str, mon, fri) -> Optional[float]:
    hist = _tradier_history_daily(symbol, mon, fri)
    if not hist:
        return None
    try:
        first_open = float(hist[0].get("open") or hist[0].get("close"))
        last_close = float(hist[-1]["close"])
        return _pct(last_close, first_open)
    except Exception:
        return None


def _vix_change_from_yahoo(mon, fri) -> Optional[float]:
    """Mon-open to Fri-close for ^VIX. The index history feed is not on the current plan."""
    try:
        import requests

        start = datetime(mon.year, mon.month, mon.day, tzinfo=timezone.utc) - timedelta(days=1)
        end = datetime(fri.year, fri.month, fri.day, tzinfo=timezone.utc) + timedelta(days=2)
        response = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX",
            params={
                "interval": "1d",
                "period1": int(start.timestamp()),
                "period2": int(end.timestamp()),
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=12,
        )
        if response.status_code != 200:
            return None
        result = ((response.json() or {}).get("chart") or {}).get("result") or []
        if not result:
            return None
        quote = ((result[0].get("indicators") or {}).get("quote") or [{}])[0]
        bars = []
        for ts, open_px, close_px in zip(
            result[0].get("timestamp") or [],
            quote.get("open") or [],
            quote.get("close") or [],
        ):
            day = datetime.fromtimestamp(ts, timezone.utc).date()
            if mon <= day <= fri and open_px and close_px:
                bars.append((day, float(open_px), float(close_px)))
        if not bars:
            return None
        bars.sort()
        return _pct(bars[-1][2], bars[0][1])
    except Exception as exc:
        logger.warning("VIX history fallback failed: %s", exc)
        return None


def _weekly_sectors(mon, fri) -> List[Dict[str, Any]]:
    """Weekly Mon→Fri sector rotation from Tradier history."""
    out: List[Dict[str, Any]] = []
    for etf, name in SECTOR_ETFS:
        pct = _weekly_index_change(etf, mon, fri)
        if pct is None:
            continue
        out.append({"name": name, "etf": etf, "pct": round(pct, 2)})
    out.sort(key=lambda x: x["pct"], reverse=True)
    return out


def _weekly_pivots(symbol: str, mon, fri) -> Dict[str, Any]:
    hist = _tradier_history_daily(symbol, mon, fri)
    if not hist:
        return {}
    try:
        highs = [float(d["high"]) for d in hist if d.get("high") is not None]
        lows = [float(d["low"]) for d in hist if d.get("low") is not None]
        close = float(hist[-1]["close"])
        if not highs or not lows:
            return {}
        piv = _pivot_levels_from_hlc(max(highs), min(lows), close)
        return {
            "pivot": f"{piv['P']:.2f}",
            "support": f"{piv['S1']:.2f}",
            "support2": f"{piv['S2']:.2f}",
            "resistance": f"{piv['R1']:.2f}",
            "resistance2": f"{piv['R2']:.2f}",
        }
    except Exception as e:
        logger.warning("weekly pivots failed for %s: %s", symbol, e)
        return {}


def _parse_num(s: Any) -> Optional[float]:
    try:
        return float(str(s).replace("%", "").replace(",", "").replace("$", "").strip())
    except Exception:
        return None


def _sanitize_ai_field(text: str) -> str:
    """Same bleed guard as the daily brief: greetings, sign-offs, and markdown rules."""
    cleaned = _HR_RE.sub("", text or "")
    cleaned = _GREETING_RE.sub("", cleaned)
    cleaned = _SIGNOFF_RE.sub("", cleaned)
    return cleaned.strip()


def _public_mover_row(row: Dict[str, Any]) -> Dict[str, Any]:
    keep = (
        "ticker",
        "pct",
        "dir",
        "why",
        "companyName",
        "reasonUrl",
        "window",
        "windowLabel",
        "isEarnings",
        "priorSessionPct",
        "hasCatalyst",
    )
    out: Dict[str, Any] = {}
    for key in keep:
        val = row.get(key)
        if val is None or val == "":
            continue
        out[key] = val
    out["why"] = row.get("why") or ""
    headlines = row.get("_headlines") or []
    if headlines:
        out["_headlines"] = list(headlines)
    return out


def _friday_session_movers(fri) -> List[Dict[str, Any]]:
    """
    Friday close-to-close movers through the daily brief's type and liquidity gate.

    Raw gainers/losers are ranked by percentage, so 2X products always win and
    real stocks never get a look. select_notable_movers walks the session
    universe, drops leveraged and inverse names, and keeps operating companies.
    """
    try:
        from market_internals import get_session_bar_maps
        from movers_filter import select_notable_movers, session_snapshots
    except Exception as e:
        logger.warning("weekly notable movers import failed: %s", e)
        return []

    fri_str = fri.strftime("%Y-%m-%d") if hasattr(fri, "strftime") else str(fri)
    try:
        today_map, yest_map, _date = get_session_bar_maps(fri_str)
    except Exception as e:
        logger.warning("weekly session bars failed for %s: %s", fri_str, e)
        return []
    if not today_map or not yest_map:
        return []
    try:
        rows = select_notable_movers(
            session_snapshots(today_map, yest_map),
            require_news=True,
        )
    except Exception as e:
        logger.warning("weekly notable movers filter failed: %s", e)
        return []
    return [_public_mover_row(r) for r in rows if _has_news_catalyst(r)]


def _weekly_change_snaps(mon, fri) -> List[Dict[str, Any]]:
    """Mon-open to Fri-close percent, with Friday volume as the liquidity proxy."""
    if not (os.getenv("MASSIVE_API_KEY") or os.getenv("POLYGON_API_KEY")):
        return []
    from market_internals import _fetch_grouped_daily_rows

    mon_rows = _fetch_grouped_daily_rows(mon.strftime("%Y-%m-%d"))
    fri_rows = _fetch_grouped_daily_rows(fri.strftime("%Y-%m-%d"))
    mon_map = {row["T"]: row for row in mon_rows if row.get("T")}
    snaps: List[Dict[str, Any]] = []
    for row in fri_rows:
        ticker = row.get("T")
        start = mon_map.get(ticker)
        if not ticker or not start:
            continue
        try:
            open_px = float(start["o"])
            close_px = float(row["c"])
            volume = float(row.get("v") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if open_px <= 0 or close_px <= 0:
            continue
        pct = (close_px - open_px) / open_px * 100.0
        snaps.append({
            "ticker": ticker,
            "todaysChangePerc": pct,
            "day": {"c": close_px, "v": volume},
            "prevDay": {"v": volume},
            "lastTrade": {"p": close_px},
        })
    return snaps


def _weekly_movers(mon, fri) -> List[Dict[str, Any]]:
    """Rank the week Mon-open to Fri-close. A single-day fallback wears the weekday."""
    rows: List[Dict[str, Any]] = []
    try:
        from movers_filter import select_notable_movers

        snaps = _weekly_change_snaps(mon, fri)
        if snaps:
            rows = select_notable_movers(
                snaps,
                require_news=True,
                window="week",
                window_label="Week",
                news_since=datetime(mon.year, mon.month, mon.day, tzinfo=timezone.utc),
                news_until=datetime(fri.year, fri.month, fri.day, 23, 59, tzinfo=timezone.utc),
            )
    except Exception as e:
        logger.warning("weekly change movers failed: %s", e)
        rows = []
    if rows:
        return [_public_mover_row(r) for r in rows if _has_news_catalyst(r)]

    fallback = _friday_session_movers(fri)
    weekday = fri.strftime("%a") if hasattr(fri, "strftime") else "Fri"
    for row in fallback:
        row["window"] = "session"
        row["windowLabel"] = weekday
    return fallback


def _filter_av_movers(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Last-resort list. Fail closed: a name we cannot vet does not render."""
    if not rows:
        return []
    try:
        from movers_filter import is_real_operating_company
        from ticker_meta import get_ticker_meta
    except Exception as e:
        logger.warning("weekly AV mover filter unavailable: %s", e)
        return []
    try:
        meta = get_ticker_meta([r["ticker"] for r in rows])
    except Exception as e:
        logger.warning("weekly AV ticker meta failed: %s", e)
        return []
    kept: List[Dict[str, Any]] = []
    for row in rows:
        info = meta.get(row["ticker"])
        if info is None or not is_real_operating_company(info):
            continue
        if info.name and not row.get("companyName"):
            row = {**row, "companyName": info.name}
        if not _has_news_catalyst(row):
            continue
        kept.append(row)
    return kept


def _movers_from_av() -> List[Dict[str, Any]]:
    raw = fetch_top_movers_av() or []
    cleaned: List[Dict[str, Any]] = []
    for m in raw:
        ticker = (m.get("ticker") or "").upper().strip()
        price = _parse_num(m.get("price"))
        pct = _parse_num(m.get("change_percent"))
        vol = _parse_num(m.get("volume"))
        if not ticker or price is None or pct is None:
            continue
        if price < JUNK_MIN_PRICE or price > JUNK_MAX_PRICE:
            continue
        if abs(pct) < JUNK_MIN_ABS_PCT or abs(pct) > JUNK_MAX_ABS_PCT:
            continue
        if vol is not None and vol < JUNK_MIN_VOL:
            continue
        cleaned.append({
            "ticker": ticker,
            "pct": f"{pct:+.2f}%",
            "dir": "down" if pct < 0 else "up",
            "why": (m.get("why") or m.get("headline") or "").strip(),
        })
    cleaned.sort(key=lambda x: abs(_parse_num(x["pct"]) or 0), reverse=True)
    return cleaned[:6]


def _fetch_friday_internals(fri) -> Optional[Dict[str, Any]]:
    if not (os.getenv("MASSIVE_API_KEY") or os.getenv("POLYGON_API_KEY")):
        return None
    try:
        from market_internals import get_market_internals

        fri_str = fri.strftime("%Y-%m-%d")
        return get_market_internals(session_date=fri_str)
    except Exception as e:
        logger.warning("market_internals failed for %s: %s", fri, e)
        return None


def _trim_week_events(events: List[Dict[str, str]], limit: int = _WEEK_AHEAD_LIMIT) -> List[Dict[str, str]]:
    """Keep the week scannable. High-impact prints win a slot when the list is long."""
    def rank(event: Dict[str, str]) -> int:
        impact = str(event.get("impact") or "").lower()
        return {"high": 0, "medium": 1}.get(impact, 2)

    ranked = sorted(events, key=lambda e: (rank(e), e.get("date") or "", e.get("event") or ""))
    chosen = ranked[:limit]
    chosen.sort(key=lambda e: (e.get("date") or "", e.get("event") or ""))
    return chosen


def _weekday_badge(date_str: str) -> str:
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%a")
    except Exception:
        return date_str or "—"


def _week_ahead_bullets(start_mon) -> List[Dict[str, str]]:
    start_dt = datetime.combine(start_mon, datetime.min.time(), tzinfo=NY)
    # Monday through Friday. days_ahead=5 with an inclusive end lands on Friday.
    cats = fetch_economic_calendar_range(days_ahead=5, start=start_dt) or []
    out: List[Dict[str, str]] = []
    for c in _trim_week_events(cats):
        event = (c.get("event") or "").strip()
        if not event:
            continue
        out.append({
            "date": _weekday_badge((c.get("date") or "").strip()),
            "event": event,
            "why": (c.get("why") or "").strip(),
        })
    return out


def _earnings_score(row: Dict[str, Any]) -> float:
    revenue = _parse_num(row.get("revenue_estimate")) or 0.0
    score = revenue
    symbol = str(row.get("symbol") or "").upper()
    if symbol in _EARNINGS_FOCUS:
        score += _EARNINGS_FOCUS_BOOST
    if _parse_num(row.get("eps_estimate")) is not None:
        score += 1.0
    return score


def _earnings_bullets_from_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Most anticipated reporters, grouped by day and session. At most four names a line."""
    ranked = sorted(
        (r for r in rows if re.fullmatch(r"[A-Z]{1,5}", str(r.get("symbol") or ""))),
        key=_earnings_score,
        reverse=True,
    )
    groups: Dict[Tuple[str, str], List[str]] = {}
    order: List[Tuple[str, str]] = []
    kept = 0
    for row in ranked:
        if _earnings_score(row) <= 0:
            continue
        key = (str(row.get("date") or ""), str(row.get("hour") or ""))
        bucket = groups.setdefault(key, [])
        if key not in order:
            order.append(key)
        if len(bucket) >= 4:
            continue
        bucket.append(str(row["symbol"]))
        kept += 1
        if kept >= 12:
            break
    bullets: List[Dict[str, str]] = []
    for day, hour in sorted(order, key=lambda k: (k[0], {"bmo": 0, "dmh": 1, "amc": 2}.get(k[1], 3))):
        names = groups.get((day, hour)) or []
        if not names:
            continue
        bullets.append({
            "date": _weekday_badge(day),
            "event": ", ".join(names),
            "why": _SESSION_LABEL.get(hour) or "Time TBA",
        })
    return bullets


def _earnings_symbols(bullets: List[Dict[str, str]]) -> set:
    symbols = set()
    for bullet in bullets:
        for part in (bullet.get("event") or "").split(","):
            symbol = part.strip().upper()
            if symbol:
                symbols.add(symbol)
    return symbols


def _earnings_bullets(start_mon) -> List[Dict[str, str]]:
    start_dt = datetime.combine(start_mon, datetime.min.time(), tzinfo=NY)
    try:
        rows = fetch_earnings_calendar_range(days_ahead=5, start=start_dt) or []
    except Exception as e:
        logger.warning("earnings calendar failed: %s", e)
        return []
    return _earnings_bullets_from_rows(rows)


def _internals_feed_lines(internals: Optional[Dict[str, Any]]) -> List[str]:
    if not internals:
        return ["  (no Friday session internals)"]
    lines: List[str] = []
    breadth = internals.get("breadth") or {}
    if breadth.get("advancers") is not None and breadth.get("decliners") is not None:
        bl = f"  A/D: {breadth['advancers']} advancers / {breadth['decliners']} decliners"
        if breadth.get("ad_ratio") is not None:
            bl += f" (ratio {breadth['ad_ratio']})"
        lines.append(bl)
    trend = internals.get("trend") or {}
    if trend.get("pct_above") is not None:
        direction = "above" if trend.get("above_50dma") else "below"
        lines.append(
            f"  SPY {direction} 50DMA by {abs(trend['pct_above']):.2f}%"
        )
    fri_sectors = internals.get("sectors") or []
    if fri_sectors:
        top = fri_sectors[:3]
        bottom = list(reversed(fri_sectors[-3:]))
        lines.append(
            "  Friday session leaders: "
            + ", ".join(f"{s['name']} ({s['etf']}) {s['pct']:+.2f}%" for s in top)
        )
        lines.append(
            "  Friday session laggards: "
            + ", ".join(f"{s['name']} ({s['etf']}) {s['pct']:+.2f}%" for s in bottom)
        )
    return lines or ["  (no Friday session internals)"]


def _build_feed(
    week_label: str,
    tape: List[Dict[str, Any]],
    sectors: List[Dict[str, Any]],
    movers: List[Dict[str, Any]],
    levels: Dict[str, Any],
    week_ahead: List[Dict[str, str]],
    internals: Optional[Dict[str, Any]] = None,
    earnings: Optional[List[Dict[str, str]]] = None,
) -> str:
    def fmt_tape(t: Dict[str, Any]) -> str:
        if t.get("pct") is None:
            return f"  {t['symbol']}: {t['price']} (weekly change unavailable)"
        pct = float(t["pct"])
        price = _parse_num(t.get("price"))
        points = ""
        if price:
            points = f", {price * pct / 100:+.1f} points"
        return f"  {t['symbol']}: {t['price']} ({pct:+.2f}% on the week{points})"

    sec_lines = [f"  {s['name']} ({s['etf']}): {s['pct']:+.2f}%" for s in sectors] or ["  (no sector data)"]
    mov_lines = []
    for m in movers:
        window = f" [{m['windowLabel']}]" if m.get("windowLabel") else ""
        headlines = m.get("_headlines") or ([m["why"]] if m.get("why") else [])
        line = f"  ${m['ticker']}: {m['pct']}{window}"
        if headlines:
            line += "\n    headlines: " + " | ".join(headlines)
        mov_lines.append(line)
    if not mov_lines:
        mov_lines = ["  (no qualifying movers)"]
    lvl_lines = []
    for sym, d in levels.items():
        lvl_lines.append(
            f"  {sym.upper()}: pivot {d.get('pivot', 'n/a')}, "
            f"S {d.get('support', 'n/a')}/{d.get('support2', 'n/a')}, "
            f"R {d.get('resistance', 'n/a')}/{d.get('resistance2', 'n/a')}"
        )
    wa_lines = [
        f"  {b['date']} {b['event']}" + (f" — {b['why']}" if b.get("why") else "")
        for b in week_ahead
    ] or ["  (no catalysts on calendar)"]
    earn_lines = [
        f"  {b['date']} {b['event']}" + (f" — {b['why']}" if b.get("why") else "")
        for b in (earnings or [])
    ] or ["  (no major earnings on the calendar)"]

    return (
        f"WEEK: {week_label}\n\n"
        f"INDEX TAPE (weekly close & % change):\n"
        + "\n".join(fmt_tape(t) for t in tape)
        + "\n\n"
        f"SECTOR ROTATION (weekly %, sorted):\n"
        + "\n".join(sec_lines)
        + "\n\n"
        f"FRIDAY SESSION (breadth / trend / daily sector context):\n"
        + "\n".join(_internals_feed_lines(internals))
        + "\n\n"
        f"NOTABLE MOVERS (quality-filtered, Friday session when available):\n"
        + "\n".join(mov_lines)
        + "\n\n"
        f"WEEKLY LEVELS (real pivots from last week's H/L/C):\n"
        + "\n".join(lvl_lines)
        + "\n\n"
        f"WEEK-AHEAD CATALYSTS:\n"
        + "\n".join(wa_lines)
        + "\n\n"
        f"EARNINGS ON DECK:\n"
        + "\n".join(earn_lines)
        + "\n"
    )


def _allowed_tickers(
    tape: List[Dict[str, Any]],
    sectors: List[Dict[str, Any]],
    movers: List[Dict[str, Any]],
) -> set:
    allowed = set(_BASE_ALLOWED)
    allowed |= {t["symbol"].upper() for t in tape}
    allowed |= {s["etf"].upper() for s in sectors}
    allowed |= {m["ticker"].upper() for m in movers}
    return allowed


def _find_fabricated(text: str, allowed: set) -> List[str]:
    cashtags = set(re.findall(r"\$([A-Z]{1,6})\b", text or ""))
    return sorted(t for t in cashtags if t not in allowed)


_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "hundred": 100,
}
_NUMBER_WORD = "|".join(_WORD_NUMBERS)
_POINT_CLAIM = re.compile(
    rf"\b(?P<num>{_NUMBER_WORD}|\d+(?:\.\d+)?)\b(?:\s+\w+){{0,5}}\s+points?\b",
    re.I,
)
_PERCENT_CLAIM = re.compile(r"\b(?P<num>\d+(?:\.\d+)?)\s*(?:%|percent)\b", re.I)
_DOLLAR_CLAIM = re.compile(r"\$(?P<num>\d+(?:,\d{3})*(?:\.\d+)?)")
_BARE_NUMBER = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)(?![\d.])")
_BULL_BREADTH = re.compile(
    r"most stocks[^.]{0,48}(held|hold up|higher|gained|advanced|rose)|"
    r"broad(?:ly)?[^.]{0,40}(higher|upside|held up|advance)|"
    r"majority of (?:stocks|names)[^.]{0,32}(rose|gained|held|advanced)",
    re.I,
)
_BEAR_BREADTH = re.compile(
    r"most stocks[^.]{0,48}(fell|lower|declin|sold off|dropped)|"
    r"majority of (?:stocks|names)[^.]{0,32}(fell|declin|dropped)",
    re.I,
)


def _claim_value(raw: str) -> Optional[float]:
    key = (raw or "").strip().lower().replace(",", "")
    if key in _WORD_NUMBERS:
        return float(_WORD_NUMBERS[key])
    return _parse_num(key)


def _near(value: float, pool: List[float], tol: float) -> bool:
    return any(abs(abs(value) - abs(item)) <= tol for item in pool)


def _number_pools(ctx: Dict[str, Any]) -> Dict[str, Any]:
    percents: List[float] = []
    prices: List[float] = []
    points: List[float] = []
    counts: List[float] = []
    for row in ctx.get("tape") or []:
        pct = row.get("pct")
        price = _parse_num(row.get("price"))
        if pct is not None:
            percents.append(float(pct))
        if price is not None:
            prices.append(price)
            if pct is not None:
                points.append(abs(price * float(pct) / 100.0))
    for row in ctx.get("sectors") or []:
        if row.get("pct") is not None:
            percents.append(float(row["pct"]))
    for row in ctx.get("movers") or []:
        pct = _parse_num(row.get("pct"))
        if pct is not None:
            percents.append(pct)
    for levels in (ctx.get("levels") or {}).values():
        for key in ("pivot", "support", "support2", "resistance", "resistance2"):
            value = _parse_num((levels or {}).get(key))
            if value is not None:
                prices.append(value)
    breadth = ((ctx.get("internals") or {}).get("breadth") or {})
    adv = breadth.get("advancers")
    dec = breadth.get("decliners")
    if adv is not None:
        counts.append(float(adv))
    if dec is not None:
        counts.append(float(dec))
    return {
        "percents": percents,
        "prices": prices,
        "points": points,
        "counts": counts,
        "advancers": None if adv is None else float(adv),
        "decliners": None if dec is None else float(dec),
    }


def _narrative_without_mover_why(md: str) -> str:
    return re.sub(r"^##\s+MOVER WHY\b[\s\S]*", "", md or "", flags=re.MULTILINE).strip()


def _span_inside(spans: List[Tuple[int, int]], start: int, end: int) -> bool:
    return any(start >= a and end <= b for a, b in spans)


def check_weekly_prose(md: str, ctx: Dict[str, Any]) -> List[str]:
    """Every claimed number has to come from the feed, in the right unit."""
    text = _narrative_without_mover_why(md)
    pools = _number_pools(ctx)
    errors: List[str] = []
    consumed: List[Tuple[int, int]] = []

    for match in _PERCENT_CLAIM.finditer(text):
        consumed.append(match.span())
        value = _claim_value(match.group("num"))
        if value is None or not _near(value, pools["percents"], 0.15):
            errors.append(f"{match.group(0).strip()} does not match a percent in the feed")
    for match in _DOLLAR_CLAIM.finditer(text):
        consumed.append(match.span())
        value = _claim_value(match.group("num"))
        if value is None or not _near(value, pools["prices"], 0.5):
            errors.append(f"{match.group(0).strip()} does not match a price in the feed")
    for match in _POINT_CLAIM.finditer(text):
        consumed.append(match.span())
        value = _claim_value(match.group("num"))
        if value is None or not _near(value, pools["points"], 1.0):
            errors.append(
                f"'{match.group(0).strip()}' does not match a point move in the feed"
            )
    allowed_bare = pools["percents"] + pools["prices"] + pools["points"] + pools["counts"]
    for match in _BARE_NUMBER.finditer(text):
        if _span_inside(consumed, match.start(), match.end()):
            continue
        if re.match(r"(?:%|percent|points?)", text[match.end():], re.I):
            continue
        raw = match.group(1)
        if re.fullmatch(r"\d{4}", raw) and 1990 <= int(raw) <= 2039:
            continue
        if re.match(r"\s*-?\s*(day|dma|week)", text[match.end():], re.I):
            continue
        value = _claim_value(raw)
        if value is None or not _near(value, allowed_bare, 0.5):
            errors.append(f"{raw} does not match a value in the feed")

    adv = pools["advancers"]
    dec = pools["decliners"]
    if adv is not None and dec is not None:
        if dec > adv and _BULL_BREADTH.search(text):
            errors.append(
                f"breadth says most stocks held up, but decliners ({int(dec)}) "
                f"outnumber advancers ({int(adv)})"
            )
        if adv > dec and _BEAR_BREADTH.search(text):
            errors.append(
                f"breadth says most stocks fell, but advancers ({int(adv)}) "
                f"outnumber decliners ({int(dec)})"
            )

    ahead = _split_sections(text).get("week_ahead", "")
    named = []
    for bullet in ctx.get("week_ahead_bullets") or []:
        event = (bullet.get("event") or "").strip()
        if event and event.lower() in ahead.lower():
            named.append(event)
    if len(named) > 2:
        errors.append(
            "week ahead prose names more than two calendar items: " + ", ".join(named)
        )
    return errors


def _alert_weekly_failure(message: str) -> None:
    logger.error("weekly brief aborted: %s", message)
    url = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    if not url:
        return
    try:
        import requests

        requests.post(url, json={"text": f"Weekly brief aborted: {message[:500]}"}, timeout=8)
    except Exception as exc:
        logger.warning("weekly alert failed: %s", exc)


def _llm(user: str, max_tokens: int = 1800, temp: float = 0.7) -> str:
    from llm_client import call_claude, text_block

    return call_claude(
        [text_block(WEEKLY_EMAIL_SYSTEM, cache=True)],
        user,
        model=BRIEF_MODEL,
        max_tokens=max_tokens,
        temperature=temp,
        log_info=True,
    )


def _finish_draft(md: str, allowed: set) -> str:
    bad = _find_fabricated(md, allowed)
    if bad:
        logger.error("weekly: still fabricating %s — scrubbing offending cashtags", bad)
        for ticker in bad:
            md = re.sub(rf"\${re.escape(ticker)}\b", "", md)
    try:
        md = _rewrite_in_voice(md)
    except Exception as e:
        logger.warning("voice pass skipped: %s", e)
    return md


def _synthesize(feed: str, allowed: set, ctx: Optional[Dict[str, Any]] = None) -> str:
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise BriefDataUnavailable("ANTHROPIC_API_KEY missing; cannot synthesize weekly brief")

    user = f"DATA FEED\n--------\n{feed}\n\nWrite the weekly email now using ONLY the feed."
    md = _llm(user)
    bad = _find_fabricated(md, allowed)
    if bad:
        logger.warning("weekly: fabricated tickers %s — retrying with feedback", bad)
        md = _llm(
            user
            + f"\n\nVIOLATION: you named tickers not in the feed: {', '.join('$' + b for b in bad)}. "
            "Rewrite using ONLY tickers present in the DATA FEED."
        )
    md = _finish_draft(md, allowed)
    if ctx is None:
        return md

    errors = check_weekly_prose(md, ctx)
    if not errors:
        logger.info("numeric-check ok")
        return md
    logger.warning("numeric-check failed: %s", "; ".join(errors))
    md = _llm(user + "\n\nVIOLATION: " + "; ".join(errors) + " Rewrite so every number and breadth claim matches the feed.")
    md = _finish_draft(md, allowed)
    errors = check_weekly_prose(md, ctx)
    if errors:
        message = "numeric-check failed: " + "; ".join(errors)
        logger.warning(message)
        _alert_weekly_failure(message)
        raise BriefDataUnavailable(message)
    logger.info("numeric-check ok")
    return md


def _apply_mover_whys(md: str, movers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    match = re.search(r"^##\s+MOVER WHY\s*$([\s\S]*?)(?=^##\s|\Z)", md or "", flags=re.MULTILINE)
    if not match:
        return movers
    decisions: Dict[str, str] = {}
    for line in match.group(1).splitlines():
        parsed = re.match(r"\$?([A-Z]{1,5})\s*:\s*(.+)", line.strip())
        if parsed:
            decisions[parsed.group(1)] = parsed.group(2).strip()
    if not decisions:
        return movers
    kept: List[Dict[str, Any]] = []
    for row in movers:
        decision = decisions.get(str(row.get("ticker") or "").upper())
        if decision is None:
            kept.append(row)
            continue
        if decision.upper() == "DROP":
            continue
        kept.append({**row, "why": decision})
    return kept


def _clip_preheader(text: str, limit: int = 90) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    cut = cleaned[:limit].rsplit(" ", 1)[0].rstrip(".,;:")
    return cut or cleaned[:limit].rstrip()


def _week_ahead_subject(next_mon, tape: List[Dict[str, Any]], sectors: List[Dict[str, Any]]) -> str:
    next_fri = next_mon + timedelta(days=4)
    span = f"{next_mon.strftime('%b')} {next_mon.day}–{next_fri.day}"
    bits: List[str] = []
    qqq = next((row.get("pct") for row in tape if row.get("symbol") == "QQQ"), None)
    tech = next((row.get("pct") for row in sectors if row.get("etf") == "XLK"), None)
    if qqq is not None:
        bits.append(f"QQQ {qqq:+.1f}%")
    if tech is not None:
        bits.append(f"tech {tech:+.1f}%")
    if not bits:
        return f"Week ahead {span}"
    return f"Week ahead {span}: {', '.join(bits)}"


_SECTION_KEYS = {
    "MAJOR DRIVER": "major_driver",
    "WEEK IN REVIEW": "week_in_review",
    "KEY LEVEL": "key_level_note",
    "WEEK AHEAD": "week_ahead",
    "BIGGEST RISK": "biggest_risk",
}


def _split_sections(md: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    parts = re.split(r"^##\s+(.+?)\s*$", md, flags=re.MULTILINE)
    for i in range(1, len(parts) - 1, 2):
        header = parts[i].strip().upper()
        body = parts[i + 1].strip()
        key = _SECTION_KEYS.get(header)
        if key:
            out[key] = body
    return out


def _md_paragraphs_to_html(text: str) -> str:
    if not text:
        return ""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    html_blocks = []
    for b in blocks:
        b = _sanitize_ai_field(b)
        if not b:
            continue
        b = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", b)
        b = b.replace("\n", "<br>")
        html_blocks.append(f"<p>{b}</p>")
    return "\n".join(html_blocks)


def build_weekly_brief() -> Dict[str, Any]:
    """Assemble the full context for weekly_brief.html.jinja (v2)."""
    now = datetime.now(tz=NY)
    mon, fri = _last_completed_week_range(now)
    week_label = _fmt_week_label(mon, fri)
    next_mon = fri + timedelta(days=3)

    prices = fetch_stock_prices_strict()
    if not prices or not has_valid_price_data(prices):
        raise BriefDataUnavailable("Missing live price data for weekly brief")

    data_stale = False

    def _spot(sym: str) -> str:
        v = prices.get(sym, {}).get("current_price")
        return f"${v:,.2f}" if isinstance(v, (int, float)) and v else "—"

    tape: List[Dict[str, Any]] = []
    for sym, label in (("spy", "SPY"), ("qqq", "QQQ"), ("iwm", "IWM")):
        wk = _weekly_index_change(label, mon, fri)
        if wk is None:
            data_stale = True
        tape.append({
            "symbol": label,
            "price": _spot(sym),
            "pct": round(wk, 2) if wk is not None else None,
        })
    spy_pct = next((t.get("pct") for t in tape if t["symbol"] == "SPY"), None)
    qqq_pct = next((t.get("pct") for t in tape if t["symbol"] == "QQQ"), None)
    if spy_pct is None and qqq_pct is None:
        raise BriefDataUnavailable("Missing weekly index returns for SPY/QQQ")

    vix_v = prices.get("vix", {}).get("current_price")
    vix_pct = _weekly_index_change("VIX", mon, fri)
    if vix_pct is None:
        vix_pct = _vix_change_from_yahoo(mon, fri)
    if vix_pct is None:
        _alert_weekly_failure("Missing VIX weekly change")
        raise BriefDataUnavailable("Missing VIX weekly change")
    tape.append({
        "symbol": "VIX",
        "price": f"{vix_v:.2f}" if isinstance(vix_v, (int, float)) and vix_v else "—",
        "pct": round(vix_pct, 2),
    })

    sectors = _weekly_sectors(mon, fri)
    if not sectors:
        data_stale = True

    internals = _fetch_friday_internals(fri)
    if not internals:
        data_stale = True

    # Mon-open to Fri-close. A Friday-only fallback is badged with the weekday.
    movers = _weekly_movers(mon, fri)
    if not movers:
        movers = _filter_av_movers(_movers_from_av())
        weekday = fri.strftime("%a")
        for row in movers:
            row["window"] = "session"
            row["windowLabel"] = weekday

    levels: Dict[str, Any] = {}
    for sym in ("SPY", "QQQ"):
        piv = _weekly_pivots(sym, mon, fri)
        if piv:
            levels[sym.lower()] = piv
    if not levels:
        er = calculate_expected_range(prices) or {}
        for sym in ("spy", "qqq"):
            d = er.get(sym) or {}
            if d:
                levels[sym] = {
                    "pivot": None,
                    "support": f"{d.get('support')}",
                    "support2": f"{d.get('support2')}",
                    "resistance": f"{d.get('resistance')}",
                    "resistance2": f"{d.get('resistance2')}",
                }
        data_stale = True

    from movers_filter import company_base_name

    movers = [m for m in movers if _has_news_catalyst(m)]
    for row in movers:
        base = company_base_name(row.get("companyName") or "")
        if base:
            row["companyName"] = base

    week_ahead = _week_ahead_bullets(next_mon)
    earnings = _earnings_bullets(next_mon)

    feed = _build_feed(
        week_label, tape, sectors, movers, levels, week_ahead, internals, earnings
    )
    allowed = _allowed_tickers(tape, sectors, movers) | _earnings_symbols(earnings)
    check_ctx = {
        "tape": tape,
        "sectors": sectors,
        "movers": movers,
        "levels": levels,
        "internals": internals,
        "week_ahead_bullets": week_ahead,
    }
    md = _synthesize(feed, allowed, check_ctx)
    movers = _apply_mover_whys(md, movers)
    for row in movers:
        row.pop("_headlines", None)
    secs = _split_sections(md)
    for key, val in list(secs.items()):
        secs[key] = _sanitize_ai_field(val)

    sections = {
        "major_driver": secs.get("major_driver", ""),
        "week_in_review_html": _md_paragraphs_to_html(secs.get("week_in_review", "")),
        "key_level_note": secs.get("key_level_note", ""),
        "week_ahead_html": _md_paragraphs_to_html(secs.get("week_ahead", "")),
        "biggest_risk": secs.get("biggest_risk", ""),
    }

    if not sections["week_in_review_html"] and not sections["major_driver"]:
        raise BriefDataUnavailable("Weekly synthesis returned no usable narrative")

    preheader = _clip_preheader(sections["major_driver"]) or "Your weekly market recap and the week ahead"

    return {
        "brand": {"name": "Options Plunge", "app_url": "https://optionsplunge.com/dashboard"},
        "week_label": week_label,
        "preheader": preheader,
        "tape": tape,
        "sections": sections,
        "sectors": sectors,
        "movers": movers,
        "levels": levels,
        "week_ahead_bullets": week_ahead,
        "earnings_bullets": earnings,
        "data_stale": data_stale,
        "unsubscribe_url": "https://optionsplunge.com/market_brief",
        "preferences_url": "https://optionsplunge.com/settings",
        "generated_at": now.strftime("%Y-%m-%d %H:%M ET"),
        "date_range": {
            "monday": mon.strftime("%Y-%m-%d"),
            "friday": fri.strftime("%Y-%m-%d"),
            "label": week_label,
        },
        "subject": _week_ahead_subject(next_mon, tape, sectors),
    }


if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO)
    ctx = build_weekly_brief()
    print(
        json.dumps(
            {
                k: (v[:200] + "…" if isinstance(v, str) and len(v) > 200 else v)
                for k, v in ctx.items()
                if k != "sections"
            },
            indent=2,
            default=str,
        )
    )
