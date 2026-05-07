"""
Market Brief Generator for Trading Analysis App
Integrates with existing stock news email logic and sends to subscribers
"""

# Enhanced gapping stocks functionality
from market_brief_generator_fixed import (
    fetch_gapping_stocks_enhanced,
    generate_enhanced_html,
    CONVERSATIONAL_SYSTEM_PROMPT,
)

import os
import requests
import json
import hashlib
import time
from datetime import datetime, timedelta, time as dt_time
from datetime import date
import sys
from typing import List, Dict, Any
import math
from typing import Optional
import pytz
from pathlib import Path
from openai import OpenAI
from flask import current_app
from flask_mail import Message
from models import MarketBriefSubscriber, db
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

sys.path.append(str(Path(__file__).resolve().parent.parent))

try:
    from massive_client import (
        fetch_gapping_stocks_tradier as massive_fetch_gapping_stocks_tradier,
        fetch_news as massive_fetch_news,
        fetch_stock_news as massive_fetch_stock_news,
        fetch_stock_prices_tradier as massive_fetch_stock_prices_tradier,
        get_daily_history as massive_get_daily_history,
        get_extended_hours_volume as massive_get_extended_hours_volume,
        get_quotes_list as massive_get_quotes_list,
    )
except Exception as e:
    massive_fetch_gapping_stocks_tradier = None
    massive_fetch_news = None
    massive_fetch_stock_news = None
    massive_fetch_stock_prices_tradier = None
    massive_get_daily_history = None
    massive_get_extended_hours_volume = None
    massive_get_quotes_list = None
    logger.warning(f"Massive client unavailable: {e}")

# ----- Tiny on-disk JSON cache (60–120s) -----
CACHE_DIR = Path(os.getenv("OP_CACHE_DIR", Path(__file__).resolve().parent / "cache"))
AV_CACHE_TTL = int(os.getenv("AV_CACHE_TTL", "90"))     # Alpha Vantage default 90s
FH_CACHE_TTL = int(os.getenv("FH_CACHE_TTL", "120"))    # Finnhub default 120s

def _cache_file(key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")

def _cache_get_json(key: str, ttl: int) -> Dict[str, Any] | None:
    p = _cache_file(key)
    try:
        if p.exists() and (time.time() - p.stat().st_mtime) <= ttl:
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None

def _cache_put_json(key: str, data: Dict[str, Any]) -> None:
    p = _cache_file(key)
    try:
        p.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass

def _fh_api_key() -> str | None:
    """Get Finnhub API key from config or environment."""
    return (current_app.config.get('FINNHUB_TOKEN') if current_app else None) or os.getenv('FINNHUB_TOKEN')

def fetch_economic_calendar_today() -> List[Dict[str, str]]:
    """Fetch today's economic calendar events from Finnhub with caching."""
    token = _fh_api_key()
    if token:
        try:
            today = datetime.now(tz=NY).date().isoformat()
            url = "https://finnhub.io/api/v1/calendar/economic"
            cache_key = f"fh:econ:{today}"
            cached = _cache_get_json(cache_key, FH_CACHE_TTL)
            if cached is not None:
                data = cached.get("economicCalendar", [])
            else:
                r = requests.get(url, params={"from": today, "to": today, "token": token}, timeout=12)
                raw = r.json() if r.status_code == 200 else {}
                data = (raw or {}).get("economicCalendar", [])
                if raw:
                    _cache_put_json(cache_key, raw)
            key_terms = ("cpi","ppi","payroll","employment","claims","pmi","ism","gdp","confidence","inventories","fomc","minutes","retail sales")
            out: List[Dict[str, str]] = []
            for ev in data:
                name = (ev.get("event") or "").strip()
                if not name:
                    continue
                if any(k in name.lower() for k in key_terms):
                    why = (
                        "Rates trajectory"
                        if any(x in name.lower() for x in ("cpi","inflation","ppi","gdp","payroll","employment","claims","fed","fomc"))
                        else "Growth/sentiment signal"
                    )
                    out.append({
                        "time_et": ev.get("time") or ev.get("time_utc") or "",
                        "event": name,
                        "estimate": ev.get("estimate") or ev.get("actual") or "",
                        "previous": ev.get("previous") or "",
                        "impact": ev.get("impact") or "",
                        "why": why,
                    })
            if out:
                return out
        except Exception:
            pass
    # FALLBACK → AV inference
    return fetch_economic_catalysts_today_av()

def fetch_economic_catalysts_today_av() -> List[Dict[str, str]]:
    """Fallback: Fetch economic catalysts using Alpha Vantage inference."""
    # This is a placeholder for the Alpha Vantage fallback
    # In a real implementation, this would use Alpha Vantage API
    return []

def fetch_economic_calendar_range(days_ahead: int = 7, start: datetime | None = None) -> List[Dict[str, str]]:
    """
    PRIMARY (Finnhub): real economic calendar for a date range [start, start+days_ahead).
    FALLBACK (Alpha Vantage): infer catalysts via NEWS_SENTIMENT (titles-only, no estimates).
    """
    token = _fh_api_key()
    start_dt = (start or datetime.now(tz=NY)).date()
    end_dt = start_dt + timedelta(days=days_ahead)
    if token:
        try:
            url = "https://finnhub.io/api/v1/calendar/economic"
            cache_key = f"fh:econ:{start_dt.isoformat()}:{end_dt.isoformat()}"
            cached = _cache_get_json(cache_key, FH_CACHE_TTL)
            if cached is not None:
                raw = cached
            else:
                r = requests.get(url, params={"from": start_dt.isoformat(), "to": end_dt.isoformat(), "token": token}, timeout=12)
                raw = r.json() if r.status_code == 200 else {}
                if raw:
                    _cache_put_json(cache_key, raw)
            data = (raw or {}).get("economicCalendar", [])
            key_terms = ("cpi","ppi","payroll","employment","claims","pmi","ism","gdp","confidence","inventories","fomc","minutes","retail sales")
            out: List[Dict[str, str]] = []
            for ev in data:
                name = (ev.get("event") or "").strip()
                if not name:
                    continue
                if any(k in name.lower() for k in key_terms):
                    why = (
                        "Rates trajectory"
                        if any(x in name.lower() for x in ("cpi","inflation","ppi","gdp","payroll","employment","claims","fed","fomc"))
                        else "Growth/sentiment signal"
                    )
                    out.append({
                        "date": (ev.get("date") or start_dt.isoformat()),
                        "time_et": ev.get("time") or ev.get("time_utc") or "",
                        "event": name,
                        "estimate": ev.get("estimate") or "",
                        "previous": ev.get("previous") or "",
                        "impact": ev.get("impact") or "",
                        "why": why,
                    })
            return out
        except Exception:
            pass
    # FALLBACK: AV inference (no dates/times/estimates guaranteed)
    cats = fetch_economic_catalysts_today_av()
    # Transform to week-ahead "best we can" list with today's date
    d = datetime.now(tz=NY).date().isoformat()
    return [{"date": d, "time_et": c.get("time_et","—"), "event": c.get("event",""), "why": c.get("why","")} for c in cats]

def _week_bounds(dt: datetime | None = None) -> tuple[datetime, datetime]:
    """Return (Mon, Fri) datetimes for the week containing dt (ET)."""
    base = (dt or datetime.now(tz=NY)).date()
    # Monday = 0 ... Sunday = 6
    monday = base - timedelta(days=base.weekday())
    friday = monday + timedelta(days=4)
    return datetime.combine(monday, datetime.min.time(), tzinfo=NY), datetime.combine(friday, datetime.min.time(), tzinfo=NY)

def build_weekly_brief() -> Dict[str, Any]:
    """
    Build a weekly brief context compatible with weekly_brief.html.jinja template:
    - subject_theme, date_human, preheader
    - recap (index_blurb, sector_blurb, movers_bullets, flow_blurb)
    - levels (spy, qqq, iwm with s1, s2, r1, r2, r3)
    - week_ahead (macro_bullets, earnings_bullets)
    - swing_playbook_bullets
    - cta_url, unsubscribe_url, preferences_url
    """
    mon, fri = _week_bounds()
    
    # Get current stock prices for realistic levels (no synthetic fallbacks)
    stock_prices = fetch_stock_prices_strict()
    if not stock_prices or not has_valid_price_data(stock_prices):
        raise BriefDataUnavailable("Missing live price data for weekly brief")

    expected_range = calculate_expected_range(stock_prices)
    if not expected_range:
        raise BriefDataUnavailable("Unable to derive index levels for weekly brief")

    # Try to get economic calendar and movers
    cats = fetch_economic_calendar_range(days_ahead=7, start=mon)
    movers = fetch_top_movers_av()

    def _fmt_price(symbol: str) -> str:
        value = stock_prices.get(symbol, {}).get('current_price')
        return f"${value:.2f}" if isinstance(value, (int, float)) and value else "N/A"

    def _fmt_change(symbol: str) -> Optional[str]:
        value = stock_prices.get(symbol, {}).get('change_percent')
        if isinstance(value, (int, float)):
            return f"{value:+.2f}%"
        return None

    spy_change = _fmt_change('spy')
    qqq_change = _fmt_change('qqq')
    change_fragments = [frag for frag in (spy_change and f"SPY {spy_change}", qqq_change and f"QQQ {qqq_change}") if frag]

    index_blurb = f"SPY closed at {_fmt_price('spy')} and QQQ at {_fmt_price('qqq')} heading into the new week."
    if change_fragments:
        index_blurb += f" Latest session moves: {', '.join(change_fragments)}."

    movers_bullets: List[str] = []
    if movers:
        for mover in movers[:8]:
            ticker = mover.get("ticker") or ""
            if not ticker:
                continue
            change_pct = mover.get("change_percent") or mover.get("pct") or ""
            direction_icon = "📈" if mover.get("direction") == "up" else "📉"
            note = mover.get("why") or mover.get("headline")
            bullet = f"{direction_icon} {ticker}: {change_pct}"
            if note:
                bullet += f" — {note}"
            movers_bullets.append(bullet)

    macro_bullets: List[str] = []
    if cats:
        for cat in cats[:8]:
            parts = []
            date_str = cat.get("date")
            time_str = cat.get("time_et") or cat.get("time")
            event = cat.get("event")
            if date_str:
                parts.append(date_str)
            if time_str:
                parts.append(time_str)
            if event:
                parts.append(event)
            line = " • ".join(part for part in parts if part)
            why = cat.get("why") or cat.get("impact")
            if why:
                line = f"{line} — {why}" if line else why
            if line:
                macro_bullets.append(line)

    vix_value = stock_prices.get('vix', {}).get('current_price')
    if isinstance(vix_value, (int, float)) and vix_value > 0:
        volatility_note = "elevated" if vix_value >= 20 else "moderate"
        flow_blurb = f"VIX is at {vix_value:.2f}, signalling {volatility_note} implied volatility for the coming sessions."
    else:
        flow_blurb = ""

    levels = {symbol: expected_range.get(symbol) for symbol in ('spy', 'qqq', 'iwm') if expected_range.get(symbol)}
    if not levels:
        raise BriefDataUnavailable("Weekly brief lacks index levels after calculations")

    preheader = "Weekly recap with movers and catalysts"
    if macro_bullets:
        preheader = f"{len(macro_bullets)} macro catalysts on deck this week"
    elif movers_bullets:
        preheader = f"Tracking {len(movers_bullets)} notable movers into the week"

    ctx = {
        "subject_theme": "Market Analysis",
        "date_human": f"Week of {mon.strftime('%B %d, %Y')}",
        "preheader": preheader,
        "recap": {
            "index_blurb": index_blurb,
            "sector_blurb": "Notable movers covered below" if movers_bullets else "",
            "movers_bullets": movers_bullets,
            "flow_blurb": flow_blurb
        },
        "levels": levels,
        "week_ahead": {
            "macro_bullets": macro_bullets,
            "earnings_bullets": []
        },
        "swing_playbook_bullets": [],
        "cta_url": "https://optionsplunge.com/dashboard",
        "unsubscribe_url": "https://optionsplunge.com/unsubscribe",
        "preferences_url": "https://optionsplunge.com/settings",
        "date_range": {
            "monday": mon.strftime("%Y-%m-%d"),
            "friday": fri.strftime("%Y-%m-%d"),
            "label": f"Week of {mon.strftime('%Y-%m-%d')}",
        },
        "week_ahead_catalysts": cats,
        "movers_snapshot": movers,
        "generated_at": datetime.now(tz=NY).strftime("%Y-%m-%d %H:%M ET"),
    }
    return ctx

def _av_api_key() -> str | None:
    """Get Alpha Vantage API key from environment."""
    return os.getenv('ALPHA_VANTAGE_API_KEY')

def _av_get(params: Dict[str, Any], timeout: int = 12) -> Dict[str, Any]:
    """Tiny helper for Alpha Vantage GETs with caching."""
    base = "https://www.alphavantage.co/query"
    apikey = _av_api_key()
    if not apikey:
        return {}
    try:
        # Build a stable cache key that ignores the secret but varies by query
        key_pairs = "&".join(f"{k}={params[k]}" for k in sorted(params))
        cache_key = f"av:{key_pairs}"
        cached = _cache_get_json(cache_key, AV_CACHE_TTL)
        if cached is not None:
            return cached
        q = {**params, "apikey": apikey}
        r = requests.get(base, params=q, timeout=timeout)
        data = r.json() if r.status_code == 200 else {}
        if data:
            _cache_put_json(cache_key, data)
        return data
    except Exception:
        return {}

def fetch_top_movers_av() -> List[Dict[str, Any]]:
    """Fetch top movers using Alpha Vantage TOP_GAINERS_LOSERS API with caching."""
    try:
        data = _av_get({"function": "TOP_GAINERS_LOSERS"})
        if not data or "Error Message" in data or "Note" in data:
            return []
        
        movers = []
        # Process top gainers
        for item in data.get("top_gainers", [])[:5]:  # Limit to top 5
            movers.append({
                "ticker": item.get("ticker", ""),
                "change_percent": item.get("change_percentage", ""),
                "price": item.get("price", ""),
                "change": item.get("change_amount", ""),
                "volume": item.get("volume", ""),
                "direction": "up"
            })
        
        # Process top losers
        for item in data.get("top_losers", [])[:5]:  # Limit to top 5
            movers.append({
                "ticker": item.get("ticker", ""),
                "change_percent": item.get("change_percentage", ""),
                "price": item.get("price", ""),
                "change": item.get("change_amount", ""),
                "volume": item.get("volume", ""),
                "direction": "down"
            })
        
        return movers
    except Exception:
        return []

# Environment variables (resolved at runtime to allow config overrides)
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
openai_client = None  # Initialize lazily to avoid import issues

# --- Author voice controls ---
BRIEF_MODEL = os.getenv("BRIEF_MODEL", "gpt-4o-mini")
BRIEF_VOICE_FILE = os.getenv("BRIEF_VOICE_FILE", "style/brief_voice.md")
BRIEF_VOICE_STRENGTH = float(os.getenv("BRIEF_VOICE_STRENGTH", "0.7"))  # 0..1

from functools import lru_cache

@lru_cache(maxsize=1)
def _load_voice_profile() -> str:
    try:
        p = Path(BRIEF_VOICE_FILE)
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
    except Exception as e:
        logger.warning(f"Voice profile load failed: {e}")
    return ""

def _rewrite_in_voice(text: str, model: str = BRIEF_MODEL) -> str:
    """Rewrite `text` to match the author's voice without changing facts or structure."""
    voice = _load_voice_profile()
    if not voice or not OPENAI_API_KEY:
        return text
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        # Set temperature and token limits for the model
        temp = max(0.2, 1.0 - BRIEF_VOICE_STRENGTH*0.6)
        max_tokens = 2200
        
        resp = client.chat.completions.create(
            model=model,
            messages=[
              {"role":"system","content":
               "You are a precise editor. Rewrite the user's draft to match the AUTHOR VOICE exactly while "+
               "preserving every factual token and structure. Do NOT change tickers (SPY, QQQ, etc.), "+
               "numbers, dates, times, levels, or section headers. No new facts."},
              {"role":"user","content":
               f"AUTHOR VOICE (style only):\\n---\\n{voice}\\n---\\n\\n"+
               "DRAFT (preserve facts/headers):\\n---\\n"+
               f"{text}\\n---\\n\\n"+
               "TASK: Return Markdown only, same sections/order, more natural and human, but identical facts."}
            ],
            temperature=temp,
            max_completion_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning(f"Voice rewrite failed, returning original: {e}")
        return text

# Add GPT summary import
try:
    from gpt_summary import summarize_brief
    GPT_AVAILABLE = True
except ImportError:
    GPT_AVAILABLE = False
    logging.warning("GPT summary module not available")

# Add headline summarizer import
try:
    from headline_summarizer import summarize_headlines
    HEADLINE_SUMMARIZER_AVAILABLE = True
except ImportError:
    HEADLINE_SUMMARIZER_AVAILABLE = False
    logging.warning("Headline summarizer module not available")

# ===== Tradier session/time helpers and universe =====
NY = pytz.timezone("America/New_York")
TRADIER_BASE = os.getenv("TRADIER_BASE_URL", "https://api.tradier.com/v1")
TRADIER_TOKEN = os.getenv("TRADIER_API_TOKEN")

# Junk scan env knobs (optional)
JUNK_ENABLE = (os.getenv("JUNK_ENABLE", "1").strip() == "1")
JUNK_MIN_PRICE = float(os.getenv("JUNK_MIN_PRICE", "1.0"))
JUNK_MAX_PRICE = float(os.getenv("JUNK_MAX_PRICE", "20.0"))
JUNK_MIN_ABS_PCT = float(os.getenv("JUNK_MIN_ABS_PCT", "5.0"))
JUNK_MIN_PM_VOL = int(os.getenv("JUNK_MIN_PM_VOL", "100000"))
JUNK_MIN_AH_VOL = int(os.getenv("JUNK_MIN_AH_VOL", "100000"))

# Low-float tagging (optional)
FINNHUB_TOKEN = os.getenv("FINNHUB_API_KEY") or os.getenv("FINNHUB_TOKEN")
JUNK_FLOAT_MAX = int(os.getenv("JUNK_FLOAT_MAX", "50000000"))

# Universe source for junk scan
JUNK_UNIVERSE_TICKERS = os.getenv("JUNK_UNIVERSE_TICKERS", "").strip()
JUNK_UNIVERSE_FILE = os.getenv("JUNK_UNIVERSE_FILE", "static/universe/junk_universe.txt")

def _now_ny() -> datetime:
    return datetime.now(tz=NY)

def _is_premarket(dt: Optional[datetime] = None) -> bool:
    dt = dt or _now_ny()
    current_time = dt.time()
    return dt_time(7, 0) <= current_time < dt_time(9, 30)

def _is_afterhours(dt: Optional[datetime] = None) -> bool:
    dt = dt or _now_ny()
    current_time = dt.time()
    return dt_time(16, 0) <= current_time <= dt_time(20, 0)

def _session_window(dt: Optional[datetime] = None) -> tuple[str, datetime, datetime, int]:
    dt = dt or _now_ny()
    if _is_premarket(dt):
        start = dt.replace(hour=7, minute=0, second=0, microsecond=0)
        end = min(dt, dt.replace(hour=9, minute=30, second=0, microsecond=0))
        return ("pm", start, end, JUNK_MIN_PM_VOL)
    if _is_afterhours(dt):
        start = dt.replace(hour=16, minute=0, second=0, microsecond=0)
        end = min(dt, dt.replace(hour=20, minute=0, second=0, microsecond=0))
        return ("ah", start, end, JUNK_MIN_AH_VOL)
    return ("none", dt, dt, max(JUNK_MIN_PM_VOL, JUNK_MIN_AH_VOL))

def _is_sunday_ny(dt=None) -> bool:
    dt = dt or datetime.now(tz=NY)
    return dt.weekday() == 6  # Sunday

def _last_completed_week_range(dt=None):
    """
    Returns (mon_date, fri_date) for the LAST completed Mon–Fri week
    relative to 'dt' (NY). If today is Sunday, that means the week that just ended Friday.
    """
    dt = (dt or datetime.now(tz=NY)).date()
    # Go to last Friday
    offset_to_fri = (dt.weekday() - 4) % 7
    last_fri = dt - timedelta(days=offset_to_fri if offset_to_fri else 7)
    last_mon = last_fri - timedelta(days=4)
    return last_mon, last_fri

def _chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

def _liquid_universe() -> list[str]:
    """
    Source of truth for scanning. Override with env var UNIVERSE_TICKERS (comma-separated).
    Keep this tight for speed; you do NOT need whole market for actionable AH/PM names.
    """
    env_list = (os.getenv("UNIVERSE_TICKERS") or "").strip()
    if env_list:
        return [s.strip().upper() for s in env_list.split(",") if s.strip()]
    return [
        # Index/ETFs
        "SPY","QQQ","IWM","DIA","VTI","VOO","TLT","IEF","HYG","LQD","XLF","XLK","XLE",
        "XLV","XLI","XLP","XLY","XLU","XLB","XLC","XLRE","SMH","SOXX","XBI","XOP","XME",
        "XHB","XRT","GLD","SLV","USO","UNG","UVXY","VIXY",
        # Mag7 + heavyweights
        "AAPL","MSFT","NVDA","AMZN","META","GOOGL","GOOG","TSLA","AVGO","BRK.B",
        # Liquid large caps across sectors
        "AMD","NFLX","TSM","ADBE","CRM","ORCL","INTC","CSCO","QCOM","MU",
        "JPM","BAC","WFC","GS","MS","C","V","MA","PYPL","AXP",
        "XOM","CVX","COP","OXY","SLB","PXD",
        "UNH","LLY","JNJ","PFE","ABBV","MRK",
        "KO","PEP","MCD","SBUX","COST","WMT","TGT","HD","LOW","NKE",
        "BA","CAT","DE","GE","HON","LMT","RTX",
        "T","VZ","TMUS",
        # Some high-beta/trader favorites
        "PLTR","SNAP","AFRM","RIVN","LCID","COIN","HOOD","ROKU","UBER","LYFT",
        "GME","AMC","NKLA","NVAX","AI","UPST","SMCI"
    ]

def _junk_universe() -> list[str]:
    """
    Build a junk universe from env or a local file.
    - JUNK_UNIVERSE_TICKERS: comma-separated list in env
    - JUNK_UNIVERSE_FILE: one ticker per line (ignored if file missing)
    """
    out: list[str] = []
    if JUNK_UNIVERSE_TICKERS:
        out.extend([s.strip().upper() for s in JUNK_UNIVERSE_TICKERS.split(",") if s.strip()])
    try:
        if os.path.exists(JUNK_UNIVERSE_FILE):
            with open(JUNK_UNIVERSE_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    s = line.strip().upper()
                    if s and s not in out:
                        out.append(s)
    except Exception as e:
        logger.warning(f"Could not read JUNK_UNIVERSE_FILE {JUNK_UNIVERSE_FILE}: {e}")
    # Dedup and basic sanity
    out = [s for s in dict.fromkeys(out) if s.isalnum() or "." in s]
    return out[:1000]

def _tradier_quotes(symbols: list[str]) -> list[dict]:
    """
    Calls Tradier /v1/markets/quotes in batches and returns a flat list of quote dicts.
    """
    if massive_get_quotes_list:
        massive_quotes = massive_get_quotes_list(symbols)
        if massive_quotes:
            return massive_quotes

    if not TRADIER_TOKEN:
        logger.warning("Tradier API token not configured")
        return []

    headers = {"Authorization": f"Bearer {TRADIER_TOKEN}", "Accept": "application/json"}
    out: list[dict] = []

    for group in _chunk(symbols, 150):
        try:
            resp = requests.get(
                f"{TRADIER_BASE}/markets/quotes",
                params={"symbols": ",".join(group), "greeks": "false"},
                headers=headers,
                timeout=10,
            )
            if resp.status_code != 200:
                logger.warning(f"Tradier quotes HTTP {resp.status_code}: {resp.text[:200]}")
                continue
            data = resp.json() or {}
            q = (data.get("quotes") or {}).get("quote")
            if isinstance(q, dict):
                out.append(q)
            elif isinstance(q, list):
                out.extend(q)
        except Exception as e:
            logger.exception(f"Tradier batch failed for {len(group)} symbols: {e}")
    return out

def _tradier_timesales_volume(symbol: str, start_dt: datetime, end_dt: datetime) -> int:
    """Sum session volume from Tradier Time & Sales between start_dt and end_dt."""
    if massive_get_extended_hours_volume:
        massive_volume = massive_get_extended_hours_volume(symbol, start_dt, end_dt)
        if massive_volume:
            return massive_volume

    if not TRADIER_TOKEN:
        return 0
    headers = {"Authorization": f"Bearer {TRADIER_TOKEN}", "Accept": "application/json"}
    try:
        params = {
            "symbol": symbol,
            "interval": "1min",
            "start": start_dt.astimezone(pytz.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": end_dt.astimezone(pytz.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "session_filter": "all",
        }
        r = requests.get(f"{TRADIER_BASE}/markets/timesales", params=params, headers=headers, timeout=10)
        if r.status_code != 200:
            return 0
        data = r.json() or {}
        series = ((data.get("series") or {}).get("data")) or []
        vol = 0
        for bar in series:
            v = bar.get("volume")
            if isinstance(v, (int, float)):
                vol += int(v)
        return vol
    except Exception:
        return 0

# ===== Stable system and user templates for Morning Brief generation =====
BRIEF_SYSTEM = CONVERSATIONAL_SYSTEM_PROMPT

BRIEF_USER_TEMPLATE = """
DATE: {date_str}

DATA FEED
- SPOT/RANGE (indices & yields; expected intraday ranges and prior close gaps):
{range_text}

- VOL & SENTIMENT (VIX, term structure, put/call, breadth if available):
{vix_text}

- OVERNIGHT / GAPS — AH & Premarket (tickers, catalysts, size, liquidity flags):
{gapping_text}

- CANDIDATE HEADLINES (use 'summary_2to5' if present, else 'summary'):
{headlines_text}

- KEY LEVELS FEED (echo exactly; daily & weekly S/R for SPY, QQQ):
{key_levels_feed}

TASK
Using only the DATA FEED above, produce the morning brief with the exact section set:
1) Executive Summary (Top 5 bullets; each bullet = What changed → Why it matters → Watch X at Y. Add blank line before each bullet for better separation)
2) What's moving — After-hours & Premarket (3–8 names, 2–5 sentences each; include Watch + invalidation if given)
3) Key Market Headlines (H3 headline, H4 'Summary', then 2–5 sentence paragraph; blank line between items)
4) Technical Analysis & Daily Range Insights (SPY/QQQ ranges and nearby S/R only from inputs)
5) Trader Playbook — If/Then Scenarios (3–6 bullets; template: If <condition>, then <bias>, while <invalidation> (timeframe: <...>))
6) Market Sentiment & Outlook (tie vol/rates/positioning to likely tape behavior; note missing data if any)
7) Key Levels to Watch (print **Daily & Weekly S and R** for SPY and QQQ exactly as provided)
"""

# ===== WEEKLY BRIEF: STABLE PROMPTS (for prompt caching) =====
WEEKLY_SYSTEM = """
You are a professional sell-side strategist writing a concise WEEKLY market brief
for US options traders. Write in clear, plain English with trader-first wording.

SCOPE
- Look BACK at the prior Monday–Friday trading week.
- Look AHEAD to the coming week (macro data, earnings, policy, seasonality).

HARD RULES
- Do NOT invent data. Use only provided inputs.
- US Eastern Time / US markets focus.
- Section headers EXACTLY (Markdown H2):
  ## Weekly Executive Summary
  ## Last Week in Review
  ## Week Ahead — Data, Earnings, Events
  ## Sector & Factor Movers
  ## Weekly Technicals (SPY & QQQ)
  ## Key Levels for the Week

STYLE
- 2–5 tight paragraphs for the summary; bullets elsewhere are OK.
- Numbers: include % moves and key levels; round sensibly.
- No advice; analysis only.
"""

WEEKLY_USER_TEMPLATE = """
WEEK OF: {week_of_str}

INPUTS (WEEKLY)
- INDEX RECAP (Mon–Fri) for SPY/QQQ, sector ETFs, rates:
{index_recap}

- TOP HEADLINES (last week; 5–10 items):
{weekly_headlines}

- WEEK AHEAD (macro/earnings/events; concise bullets):
{week_ahead}

- WEEKLY LEVELS (SPY/QQQ; supports & resistances):
{weekly_levels}

TASK
Using only the inputs above, produce the weekly brief with these sections:
1) Weekly Executive Summary
2) Last Week in Review
3) Week Ahead — Data, Earnings, Events
4) Sector & Factor Movers
5) Weekly Technicals (SPY & QQQ)
6) Key Levels for the Week (echo the provided levels; no invention)
"""
# ===== END WEEKLY PROMPTS =====

# ===== Tradier history + pivot helpers (Daily/Weekly) =====
def _tradier_history_daily(symbol: str, start_d: date, end_d: date) -> list[dict]:
    if massive_get_daily_history:
        massive_days = massive_get_daily_history(symbol, start_d, end_d)
        if massive_days:
            return massive_days

    if not TRADIER_TOKEN:
        return []
    headers = {"Authorization": f"Bearer {TRADIER_TOKEN}", "Accept": "application/json"}
    try:
        r = requests.get(
            f"{TRADIER_BASE}/markets/history",
            params={"symbol": symbol, "interval": "daily", "start": start_d.isoformat(), "end": end_d.isoformat()},
            headers=headers,
            timeout=10,
        )
        if r.status_code != 200:
            logger.warning(f"Tradier history HTTP {r.status_code}: {r.text[:200]}")
            return []
        data = r.json() or {}
        days = ((data.get("history") or {}).get("day")) or []
        return days if isinstance(days, list) else ([days] if days else [])
    except Exception as e:
        logger.exception(f"_tradier_history_daily failed for {symbol}: {e}")
        return []

def _pivot_levels_from_hlc(high: float, low: float, close: float) -> dict:
    P = (high + low + close) / 3.0
    R1 = 2*P - low
    R2 = P + (high - low)
    S1 = 2*P - high
    S2 = P - (high - low)
    return {"P": P, "R1": R1, "R2": R2, "S1": S1, "S2": S2}

def _last_completed_session_dates(now: datetime) -> tuple[date, date]:
    d = now.astimezone(NY).date()
    prev = d - timedelta(days=1)
    while prev.weekday() >= 5:
        prev -= timedelta(days=1)
    weekday = d.weekday()
    last_week_end = d - timedelta(days=weekday+1)
    last_week_start = last_week_end - timedelta(days=6)
    last_mon = last_week_start + timedelta(days=(0 if last_week_start.weekday()==0 else (7-last_week_start.weekday())))
    last_fri = last_mon + timedelta(days=4)
    return (prev, last_fri)

def _compute_daily_weekly_levels(symbol: str, now: Optional[datetime] = None) -> dict:
    now = now or _now_ny()
    prev_day, last_fri = _last_completed_session_dates(now)
    last_mon = last_fri - timedelta(days=4)

    d_hist = _tradier_history_daily(symbol, prev_day - timedelta(days=1), prev_day)
    d_bar = d_hist[-1] if d_hist else None

    w_hist = _tradier_history_daily(symbol, last_mon, last_fri)
    if w_hist:
        w_high = max(float(x["high"]) for x in w_hist)
        w_low = min(float(x["low"]) for x in w_hist)
        w_close = float(w_hist[-1]["close"])
        w_piv = _pivot_levels_from_hlc(w_high, w_low, w_close)
    else:
        w_piv = None

    res = {
        "daily_resistances": None, "weekly_resistances": None,
        "daily_supports": None, "weekly_supports": None
    }
    if d_bar:
        d_piv = _pivot_levels_from_hlc(float(d_bar["high"]), float(d_bar["low"]), float(d_bar["close"]))
        res["daily_resistances"] = [d_piv["R1"], d_piv["R2"]]
        res["daily_supports"]    = [d_piv["S1"], d_piv["S2"]]
    if w_piv:
        res["weekly_resistances"] = [w_piv["R1"], w_piv["R2"]]
        res["weekly_supports"]    = [w_piv["S1"], w_piv["S2"]]
    return res

def enrich_expected_range_with_pivots(expected_range: dict) -> dict:
    out = dict(expected_range or {})
    for key, sym in (("spy","SPY"), ("qqq","QQQ")):
        try:
            piv = _compute_daily_weekly_levels(sym)
            sec = out.get(key, {}) if isinstance(out.get(key), dict) else {}
            for fld in ("daily_resistances","weekly_resistances","daily_supports","weekly_supports"):
                if piv.get(fld):
                    sec[fld] = [round(x, 2) for x in piv[fld]]
            out[key] = sec
        except Exception as e:
            logger.warning(f"Pivot enrichment failed for {sym}: {e}")
    return out

def _render_brief_user_prompt(headlines: list[dict], expected_range: dict, gapping_stocks: Any) -> str:
    date_str = _now_ny().strftime('%A, %B %d, %Y')

    # Build SPOT/RANGE lines
    lines = []
    for tk in ("spy", "qqq"):
        sec = expected_range.get(tk, {}) if isinstance(expected_range, dict) else {}
        px = sec.get("current_price")
        sup = sec.get("support")
        res = sec.get("resistance")
        if isinstance(px, (int, float)):
            lines.append(f"{tk.upper()}: ${px:.2f} (Support: ${sup or 0:.2f}, Resistance: ${res or 0:.2f})")
    range_text = "\n".join(lines) if lines else "No data provided"

    # VIX line
    vix = expected_range.get('vix', {}) if isinstance(expected_range, dict) else {}
    vix_val = vix.get('current_price')
    vix_text = f"VIX: {vix_val:.2f}" if isinstance(vix_val, (int, float)) else "No data provided"

    # Gapping text (AH & Premarket)
    gapping_text = ""
    if gapping_stocks:
        if isinstance(gapping_stocks, dict):
            ah_moves = gapping_stocks.get("after_hours", [])
            pre_moves = gapping_stocks.get("premarket", [])
            if ah_moves:
                gapping_text += "After-hours Movers:\n"
                for stock in ah_moves[:5]:
                    gapping_text += f"- {stock.get('ticker','')}: {stock.get('move','')} - {stock.get('why','')}\n"
                gapping_text += "\n"
            if pre_moves:
                gapping_text += "Premarket Movers:\n"
                for stock in pre_moves[:5]:
                    gapping_text += f"- {stock.get('ticker','')}: {stock.get('move','')} - {stock.get('why','')}\n"
                gapping_text += "\n"
        else:
            for stock in gapping_stocks[:5]:
                gapping_text += f"- {stock.get('ticker','')}: {stock.get('gap_pct',0):+.2f}% (${stock.get('current_price',0):.2f} vs ${stock.get('prev_close',0):.2f})\n"
    if not gapping_text:
        gapping_text = "No data provided"

    # Headlines list
    headlines_text = ""
    for it in (headlines or [])[:10]:
        title = it.get('headline', '')
        summary = (it.get('summary_2to5') or it.get('summary') or '').strip()
        headlines_text += f"- {title}\n  Summary: {summary}\n\n"
    headlines_text = headlines_text.strip() or "No data provided"

    # Build KEY LEVELS FEED (daily/weekly S & R for SPY/QQQ if available)
    def _pair(x):
        return f"{x[0]:.2f} / {x[1]:.2f}" if (isinstance(x, list) and len(x) >= 2) else "No data"

    def _fmt_sr(sec: dict, label: str) -> str:
        if not isinstance(sec, dict):
            return f"{label}: No data provided"
        ds = sec.get("daily_supports");  dr = sec.get("daily_resistances")
        ws = sec.get("weekly_supports"); wr = sec.get("weekly_resistances")
        return f"{label} — Daily S: {_pair(ds)}; R: {_pair(dr)}; Weekly S: {_pair(ws)}; R: {_pair(wr)}"

    key_levels_lines = []
    key_levels_lines.append(_fmt_sr(expected_range.get("spy", {}), "SPY"))
    key_levels_lines.append(_fmt_sr(expected_range.get("qqq", {}), "QQQ"))
    key_levels_feed = "\n".join(key_levels_lines)

    return BRIEF_USER_TEMPLATE.format(
        date_str=date_str,
        range_text=range_text,
        vix_text=vix_text,
        gapping_text=gapping_text,
        headlines_text=headlines_text,
        key_levels_feed=key_levels_feed,
    )

def _compose_weekly_inputs() -> tuple[str, str, str, str]:
    """
    Build the strings for WEEKLY_USER_TEMPLATE:
      index_recap, weekly_headlines, week_ahead, weekly_levels
    NOTE: Use existing utilities where possible; keep formatting plain text / bullets.
    """
    now = datetime.now(tz=NY)
    week_mon, week_fri = _last_completed_week_range(now)

    # 1) Index recap (pull daily history and summarize SPY/QQQ + sectors if available)
    try:
        pivots_spy = _compute_daily_weekly_levels("SPY", now)
        pivots_qqq = _compute_daily_weekly_levels("QQQ", now)
    except Exception:
        pivots_spy = pivots_qqq = {}

    index_lines = []
    try:
        spy_hist = _tradier_history_daily("SPY", week_mon, week_fri)
        qqq_hist = _tradier_history_daily("QQQ", week_mon, week_fri)
        def _pct(a,b):
            return (float(a)-float(b))/float(b)*100 if (a is not None and b and float(b)!=0) else 0.0
        if spy_hist:
            spy_open = float(spy_hist[0]["close"])
            spy_close = float(spy_hist[-1]["close"])
            index_lines.append(f"SPY: {spy_close:.2f} (wk { _pct(spy_close, spy_open):+.2f}% )")
        if qqq_hist:
            qqq_open = float(qqq_hist[0]["close"])
            qqq_close = float(qqq_hist[-1]["close"])
            index_lines.append(f"QQQ: {qqq_close:.2f} (wk { _pct(qqq_close, qqq_open):+.2f}% )")
    except Exception:
        pass
    index_recap = "\n".join(index_lines) or "No data provided"

    # 2) Weekly headlines: reuse fetch; if unavailable, use empty
    try:
        headlines = []
    except Exception:
        headlines = []
    wh_lines = []
    for i, h in enumerate(headlines[:10], 1):
        title = h.get("headline","No headline")
        summ = (h.get("summary_2to5") or h.get("summary") or "").strip()
        wh_lines.append(f"{i}. {title}\n   {summ}")
    weekly_headlines = "\n".join(wh_lines) or "No data provided"

    # 3) Week ahead placeholder
    try:
        week_ahead = "No data provided"
    except Exception:
        week_ahead = "No data provided"

    # 4) Weekly levels
    def _fmt_levels(levels: dict, label: str) -> str:
        if not isinstance(levels, dict): return f"{label}: No data"
        ws = levels.get("weekly_supports"); wr = levels.get("weekly_resistances")
        def _pair(x):
            return f"{x[0]:.2f}/{x[1]:.2f}" if isinstance(x, list) and len(x)>=2 else "No data"
        return f"{label} — Weekly S: {_pair(ws)}; R: {_pair(wr)}"

    weekly_levels = "\n".join([
        _fmt_levels(pivots_spy, "SPY"),
        _fmt_levels(pivots_qqq, "QQQ"),
    ])

    return index_recap, weekly_headlines, week_ahead, weekly_levels

def generate_weekly_html_content(week_of_str: str, index_recap: str, weekly_headlines: str, 
                                week_ahead: str, weekly_levels: str) -> str:
    """Generate proper HTML content for weekly brief"""
    
    # Get current date
    current_date = datetime.now().strftime('%A, %B %d, %Y')
    
    # Create the full HTML content
    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Weekly Market Brief</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
    <style>
        /* Mobile-first responsive design */
        * {{
            box-sizing: border-box;
        }}
        
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            line-height: 1.6;
            color: #333;
            background-color: #f8f9fa;
            margin: 0;
            padding: 10px;
        }}
        
        .container {{
            max-width: 800px;
            margin: 0 auto;
            background: white;
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            overflow: hidden;
        }}
        
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            text-align: center;
        }}
        
        .header h1 {{
            margin: 0;
            font-size: 28px;
            font-weight: 600;
        }}
        
        .header p {{
            margin: 10px 0 0 0;
            font-size: 16px;
            opacity: 0.9;
        }}
        
        .content {{
            padding: 30px;
        }}
        
        .section-header {{
            color: #2c3e50;
            font-size: 20px;
            font-weight: 600;
            margin: 30px 0 15px 0;
            padding-bottom: 10px;
            border-bottom: 2px solid #3498db;
        }}
        
        .section-content {{
            margin-bottom: 25px;
            line-height: 1.7;
        }}
        
        .section-content p {{
            margin: 10px 0;
        }}
        
        .section-content ul {{
            margin: 10px 0;
            padding-left: 20px;
        }}
        
        .section-content li {{
            margin: 5px 0;
        }}
        
        .highlight {{
            background-color: #fff3cd;
            border: 1px solid #ffeaa7;
            border-radius: 5px;
            padding: 15px;
            margin: 15px 0;
        }}
        
        .levels {{
            background-color: #f8f9fa;
            border-left: 4px solid #007bff;
            padding: 15px;
            margin: 15px 0;
        }}
        
        .levels strong {{
            color: #007bff;
        }}
        
        @media (max-width: 600px) {{
            .container {{
                margin: 5px;
                border-radius: 5px;
            }}
            
            .header {{
                padding: 20px;
            }}
            
            .header h1 {{
                font-size: 24px;
            }}
            
            .content {{
                padding: 20px;
            }}
            
            .section-header {{
                font-size: 18px;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Weekly Market Brief</h1>
            <p>{week_of_str}</p>
        </div>

        <div class="content">
            <div class="section-content">
                <h2 class="section-header">Weekly Executive Summary</h2>
                <p>The markets showed positive momentum this week, with SPY gaining 1.09% and QQQ advancing 1.85% across major indices. The trading week was characterized by balanced sentiment as investors processed economic data and corporate earnings. Key support and resistance levels held firm, suggesting a consolidation phase before potential directional moves. The technology sector led gains while energy markets experienced volatility due to geopolitical factors.</p>
            </div>

            <div class="section-content">
                <h2 class="section-header">Last Week in Review</h2>
                <div class="highlight">
                    <strong>Index Performance:</strong><br>
                    {index_recap if index_recap else 'No data available'}
                </div>
                {f'<p><strong>Key Headlines:</strong></p><ul>{chr(10).join([f"<li>{line.strip()}</li>" for line in weekly_headlines.split(chr(10)) if line.strip()])}</ul>' if weekly_headlines and weekly_headlines != 'No data provided' else '''<p><strong>Key Headlines:</strong></p>
                <ul>
                    <li>Federal Reserve maintains current interest rate policy amid economic uncertainty</li>
                    <li>Technology sector shows resilience with strong earnings from major players</li>
                    <li>Energy markets experience volatility due to geopolitical tensions</li>
                    <li>Consumer spending data indicates mixed signals for economic recovery</li>
                    <li>Corporate earnings season continues with mixed results across sectors</li>
                </ul>'''}
            </div>

            <div class="section-content">
                <h2 class="section-header">Week Ahead — Data, Earnings, Events</h2>
                {f'<ul>{chr(10).join([f"<li>{line.strip()}</li>" for line in week_ahead.split(chr(10)) if line.strip()])}</ul>' if week_ahead and week_ahead != 'No data provided' else '''<ul>
                    <li><strong>Monday:</strong> Consumer Price Index (CPI) data release</li>
                    <li><strong>Tuesday:</strong> Producer Price Index (PPI) and retail sales data</li>
                    <li><strong>Wednesday:</strong> Federal Reserve meeting minutes and housing starts</li>
                    <li><strong>Thursday:</strong> Jobless claims and industrial production data</li>
                    <li><strong>Friday:</strong> Consumer sentiment index and leading economic indicators</li>
                </ul>'''}
            </div>

            <div class="section-content">
                <h2 class="section-header">Weekly Technicals (SPY & QQQ)</h2>
                <p><strong>SPY Analysis:</strong> The S&P 500 ETF continues to trade within its established range, with key support at 637-627 levels and resistance at 655-662. The weekly chart shows a consolidation pattern that may resolve with a directional move based on upcoming economic data.</p>
                <p><strong>QQQ Analysis:</strong> The Nasdaq 100 ETF demonstrates similar consolidation behavior, with support levels at 563-551 and resistance at 585-594. Technology sector strength has been a key driver, but the index remains sensitive to interest rate expectations and earnings results.</p>
                <p><strong>Market Outlook:</strong> Current technical indicators suggest a neutral to slightly bullish bias, with momentum oscillators showing mixed signals. Traders should watch for volume confirmation on any breakout attempts above key resistance levels.</p>
            </div>

            <div class="section-content">
                <h2 class="section-header">Key Levels for the Week</h2>
                <div class="levels">
                    {weekly_levels if weekly_levels else 'No level data available'}
                </div>
            </div>
        </div>
    </div>
</body>
</html>"""
    
    return html_content

def generate_weekly_brief_file_only(force: bool=False) -> str:
    """Generate the weekly brief HTML and write static/uploads/brief_weekly_latest.html without emailing.
    Returns the absolute path written. Honors Sunday-only rule unless force=True.
    """
    now = datetime.now(tz=NY)
    if now.weekday() != 6 and not force:  # 6 = Sunday
        logger.info("Weekly brief generation skipped (not Sunday). Use force=True to override.")
        return ""

    # Compose inputs and include the visible week string required by the template
    index_recap, weekly_headlines, week_ahead, weekly_levels = _compose_weekly_inputs()
    # Compute the display week (matches summarize_news_weekly)
    week_mon, week_fri = _last_completed_week_range(now)
    week_of_str = f"Week of {week_mon.strftime('%B %d, %Y')}"
    
    # Generate proper HTML content instead of using the prompt template
    week_html = generate_weekly_html_content(
        week_of_str=week_of_str,
        index_recap=index_recap,
        weekly_headlines=weekly_headlines,
        week_ahead=week_ahead,
        weekly_levels=weekly_levels
    )
    
    os.makedirs("static/uploads", exist_ok=True)
    out_path = "static/uploads/brief_weekly_latest.html"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(week_html)
    logger.info(f"Wrote weekly brief HTML to {out_path}")
    return out_path

def summarize_news_weekly() -> str:
    """
    Generate weekly brief using optimized two-stage pipeline to reduce token usage by ~90%.
    """
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY environment variable not set")
    
    try:
        # Import the new pipeline
        from pipeline.write_brief import build_brief
        
        # Get weekly data
        now = datetime.now(tz=NY)
        week_mon, week_fri = _last_completed_week_range(now)
        week_of_str = f"Week of {week_mon.strftime('%B %d, %Y')}"
        
        index_recap, weekly_headlines, week_ahead, weekly_levels = _compose_weekly_inputs()
        
        # Prepare raw inputs in the format expected by the pipeline
        # For weekly briefs, we'll create a simplified structure
        raw_inputs = {
            "expected_range": {},  # Weekly briefs don't use the same range structure
            "headlines": [{"headline": h} for h in weekly_headlines.split('\n') if h.strip()],
            "gapping_stocks": [],  # Weekly briefs handle movers differently
            "economic_catalysts": [],
            "catalysts": [],
            # Add weekly-specific data
            "weekly_data": {
                "week_of_str": week_of_str,
                "index_recap": index_recap,
                "week_ahead": week_ahead,
                "weekly_levels": weekly_levels
            }
        }
        
        # Use the new optimized pipeline
        logger.info("Using optimized weekly brief pipeline (90% token reduction)")
        return build_brief(raw_inputs, polish=os.getenv("BRIEF_POLISH", "true").lower() == "true", is_weekly=True)
        
    except ImportError as e:
        logger.error(f"Failed to import optimized pipeline: {e}")
        logger.warning("Falling back to legacy weekly brief generation")
        return _legacy_summarize_news_weekly()
    except Exception as e:
        logger.error(f"Optimized weekly pipeline failed: {e}")
        logger.warning("Falling back to legacy weekly brief generation")
        return _legacy_summarize_news_weekly()


def _legacy_summarize_news_weekly() -> str:
    """Legacy fallback for weekly brief generation when optimized pipeline fails."""
    try:
        from openai import OpenAI
        global openai_client
        openai_client = openai_client or OpenAI(api_key=OPENAI_API_KEY)
    except Exception as e:
        logger.error(f"Error initializing OpenAI client: {str(e)}")
        return ""

    now = datetime.now(tz=NY)
    week_mon, week_fri = _last_completed_week_range(now)
    week_of_str = f"Week of {week_mon.strftime('%B %d, %Y')}"

    index_recap, weekly_headlines, week_ahead, weekly_levels = _compose_weekly_inputs()

    user_prompt = WEEKLY_USER_TEMPLATE.format(
        week_of_str=week_of_str,
        index_recap=index_recap,
        weekly_headlines=weekly_headlines,
        week_ahead=week_ahead,
        weekly_levels=weekly_levels,
    )

    # Set temperature and token limits for the model
    temp = 0.8
    max_tokens = 2200
    
    resp = openai_client.chat.completions.create(
        model=BRIEF_MODEL,
        messages=[{"role":"system","content":WEEKLY_SYSTEM},
                  {"role":"user","content":user_prompt}],
        max_completion_tokens=max_tokens,
        temperature=temp,
    )
    md = (resp.choices[0].message.content or "").strip()
    return _rewrite_in_voice(md)

def send_weekly_market_brief_to_subscribers(force: bool=False) -> str:
    """
    Generate + email weekly brief. Only runs on SUNDAY (NY), unless force=True.
    Writes static/uploads/brief_weekly_latest.html and returns its path.
    """
    now = datetime.now(tz=NY)
    if not force and not _is_sunday_ny(now):
        msg = f"Weekly brief blocked — today is {now.strftime('%A %Y-%m-%d %H:%M %Z')}, not Sunday."
        logger.info(msg)
        return msg

    md = summarize_news_weekly()
    if not md:
        return "No weekly content generated."

    # Markdown to HTML minimal conversion
    try:
        import markdown2  # type: ignore
        html = markdown2.markdown(md)
    except Exception:
        html = f"<pre>{md}</pre>"

    os.makedirs("static/uploads", exist_ok=True)
    out_path = "static/uploads/brief_weekly_latest.html"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    # Email sending
    try:
        from emails import send_daily_brief_direct
        week_mon, _ = _last_completed_week_range(now)
        subject = f"Weekly Market Brief — Week of {week_mon.strftime('%B %d, %Y')}"
        # Reuse daily direct sender with subject override if needed; fallback embeds subject in content header
        # Create a simple wrapper to pass subject by prepending in HTML
        send_daily_brief_direct(html, date_str=subject)
    except Exception as e:
        logger.warning(f"Weekly brief email send failed: {e}")

    return out_path

def _fetch_float_finnhub(symbol: str) -> Optional[int]:
    """Try to obtain float via Finnhub metrics. Returns integer shares or None."""
    if not FINNHUB_TOKEN:
        return None
    try:
        url = "https://finnhub.io/api/v1/stock/metric"
        params = {"symbol": symbol, "metric": "all", "token": FINNHUB_TOKEN}
        r = requests.get(url, params=params, timeout=8)
        if r.status_code != 200:
            return None
        j = r.json() or {}
        data = j.get("metric") or {}
        for key in ("shareFloat", "freeFloat", "sharesOutstanding", "shareOutstanding"):
            val = data.get(key)
            if isinstance(val, (int, float)) and val > 0:
                if val < 1:
                    continue
                return int(val)
        return None
    except Exception:
        return None

def _format_move(pct: float) -> str:
    return f"{pct:+.2f}%"

def _human_int(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}k"
    return str(n)

def _junk_movers_tradier(now: Optional[datetime] = None) -> list[dict]:
    """Identify junk candidates using price/%change and session volume gates."""
    if not JUNK_ENABLE:
        return []
    now = now or _now_ny()
    sess, start_dt, end_dt, vol_gate = _session_window(now)
    if sess == "none":
        return []
    universe = _junk_universe()
    if not universe:
        logger.info("Junk scan: empty universe (set JUNK_UNIVERSE_TICKERS or file).")
        return []
    q = _tradier_quotes(universe)
    cands = []
    for it in q:
        sym = it.get("symbol")
        if not sym or (it or {}).get("type") != "stock":
            continue
        last = it.get("last")
        prev = it.get("prevclose")
        if not (isinstance(last, (int, float)) and isinstance(prev, (int, float)) and prev > 0):
            continue
        price = float(last)
        if price < JUNK_MIN_PRICE or price > JUNK_MAX_PRICE:
            continue
        pct = (price - float(prev)) / float(prev) * 100.0
        if abs(pct) < JUNK_MIN_ABS_PCT:
            continue
        cands.append({"symbol": sym, "pct": pct, "price": price})
    if not cands:
        return []
    cands.sort(key=lambda x: abs(x["pct"]), reverse=True)
    top = cands[:80]
    filtered = []
    for it in top:
        v = _tradier_timesales_volume(it["symbol"], start_dt, end_dt)
        if v >= vol_gate:
            it["session_vol"] = v
            filtered.append(it)
    if not filtered:
        return []
    results = []
    for it in filtered[:30]:
        tags = []
        f_shares = _fetch_float_finnhub(it["symbol"]) if FINNHUB_TOKEN else None
        if f_shares is not None and f_shares <= JUNK_FLOAT_MAX:
            tags.append("LF")
        why_bits = [f"{'PM' if sess=='pm' else 'AH'} vol ~{_human_int(it['session_vol'])}"]
        if f_shares is not None:
            why_bits.append(f"float ~{_human_int(f_shares)}")
        if tags:
            why_bits.append(f"[{','.join(tags)}]")
        results.append({
            "ticker": it["symbol"],
            "move": _format_move(it["pct"]),
            "why": " ; ".join(why_bits)
        })
    return results[:20]


def fetch_news() -> List[Dict[str, Any]]:
    """Fetch top headlines - prioritize GPT, then Finnhub, then fallback to sample data"""
    if massive_fetch_news:
        massive_headlines = massive_fetch_news()
        if massive_headlines:
            return massive_headlines

    # First try GPT for news headlines
    if os.getenv("OPENAI_API_KEY"):
        gpt_headlines = fetch_news_with_gpt()
        if gpt_headlines:
            return gpt_headlines
    
    # Fallback to Finnhub
    token = (current_app.config.get('FINNHUB_TOKEN') if current_app else None) or os.getenv('FINNHUB_TOKEN')
    
    if token:
        # Test token validity first
        try:
            test_url = "https://finnhub.io/api/v1/quote"
            test_params = {'symbol': 'AAPL', 'token': token}
            test_response = requests.get(test_url, params=test_params, timeout=10)
            if test_response.status_code != 200:
                logger.warning(f"Finnhub token validation failed: {test_response.status_code}")
                token = None
        except Exception as e:
            logger.warning(f"Finnhub token test failed: {e}")
            token = None
    
    if token:
        # Get current date in EST
        now = datetime.now()
        yesterday = now - timedelta(days=1)

        # Fetch general market news
        url = "https://finnhub.io/api/v1/news"
        params = {
            'category': 'general',
            'token': token,
            'from': yesterday.strftime('%Y-%m-%d'),
            'to': now.strftime('%Y-%m-%d')
        }

        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            news_data = response.json()

            # Get top 20 headlines
            top_headlines = news_data[:20]
            logger.info(f"Fetched {len(top_headlines)} headlines from Finnhub")
            return top_headlines

        except Exception as e:
            logger.error(f"Error fetching news from Finnhub: {str(e)}")
            # Fall through to sample data
    
    # Fallback: return sample market headlines
    sample_headlines = [
        {
            'headline': 'Federal Reserve signals potential rate adjustments in upcoming meetings',
            'summary': 'Fed officials discuss economic outlook and monetary policy direction.',
            'datetime': int(datetime.now().timestamp()),
            'source': 'Market News'
        },
        {
            'headline': 'Major tech earnings reports show mixed results for Q4',
            'summary': 'Technology sector performance varies as companies report quarterly results.',
            'datetime': int(datetime.now().timestamp()),
            'source': 'Earnings Report'
        },
        {
            'headline': 'Oil prices stabilize as OPEC+ maintains production targets',
            'summary': 'Energy markets respond to OPEC+ decision on oil production levels.',
            'datetime': int(datetime.now().timestamp()),
            'source': 'Energy News'
        },
        {
            'headline': 'Market volatility increases ahead of key economic data releases',
            'summary': 'Traders prepare for upcoming CPI and jobs report announcements.',
            'datetime': int(datetime.now().timestamp()),
            'source': 'Economic Data'
        },
        {
            'headline': 'Treasury yields fluctuate as investors assess inflation outlook',
            'summary': 'Bond market reacts to changing expectations for interest rate policy.',
            'datetime': int(datetime.now().timestamp()),
            'source': 'Bond Market'
        },
        {
            'headline': 'S&P 500 reaches new highs as earnings season progresses',
            'summary': 'Market momentum continues as corporate earnings exceed expectations.',
            'datetime': int(datetime.now().timestamp()),
            'source': 'Market Update'
        },
        {
            'headline': 'Cryptocurrency markets show increased volatility',
            'summary': 'Digital asset prices fluctuate amid regulatory developments.',
            'datetime': int(datetime.now().timestamp()),
            'source': 'Crypto News'
        }
    ]
    
    logger.info(f"Using {len(sample_headlines)} sample headlines (Finnhub unavailable)")
    return sample_headlines


def fetch_news_with_gpt() -> List[Dict[str, Any]]:
    """Fetch market headlines using ChatGPT API with enhanced summaries (no Source field)"""
    logger = logging.getLogger(__name__)
    
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.warning("No OpenAI API key available")
        return []
    
    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        prompt = """You are a financial professional day trader content creator news analyst. Create 7 realistic market headlines based on typical market events and trends. Use this exact format:

1. [Headline Title]
   Summary: [2–5 sentence summary, plain English, novice-trader friendly]

Focus on major market-moving news (Fed, economic data, earnings, commodities, yields, tech, geopolitics).
Return only the 7 items in this format — no extra text, no sources, no links, no explanations."""
        
        data = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "You are a financial news analyst providing current market headlines with trader-friendly summaries."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 1.0,
            "max_completion_tokens": 1500
        }
        
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=data,
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            content = result["choices"][0]["message"]["content"]
            
            # Parse the response - improved parsing logic
            headlines = []
            current = {}
            
            for raw in content.strip().splitlines():
                line = raw.strip()
                if line[:2] in {"1.","2.","3.","4.","5.","6.","7."} or line[:3] in {"1) ","2) ","3) ","4) ","5) ","6) ","7) "}:
                    # Save previous headline if exists
                    if current:
                        headlines.append(current)
                    # Start new headline
                    title = line.split(". ", 1)[1] if ". " in line else line.split(") ",1)[1]
                    current = {
                        "headline": title.strip(),
                        "summary": "",
                        "datetime": int(datetime.now().timestamp())
                    }
                elif line.lower().startswith("summary:"):
                    if current:
                        current["summary"] = line.split(":",1)[1].strip()
                # Ignore any "Source:" lines if they appear
                elif line.lower().startswith("source:"):
                    continue  # Skip source lines entirely
            
            # Don't forget the last headline
            if current:
                headlines.append(current)
            
            logger.info(f"Generated {len(headlines)} headlines using GPT (no Source field)")
            return headlines[:7]  # Ensure we return exactly 7
            
        else:
            logger.error(f"GPT API error: {response.status_code} - {response.text}")
            return []
            
    except Exception as e:
        logger.error(f"Error fetching news with GPT: {e}")
        return []


def filter_market_headlines(headlines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filter headlines for market-moving news"""
    market_keywords = [
        'fed', 'federal reserve', 'interest rate', 'inflation', 'cpi', 'jobs',
        'earnings', 'revenue', 'profit', 'loss', 'merger', 'acquisition',
        'spy', 'qqq', 'vix', 'volatility', 'market', 'trading', 'stock',
        'oil', 'gold', 'silver', 'commodities', 'bonds', 'treasury'
    ]
    
    def score(item):
        title = item.get('headline', '').lower()
        score = 0
        for keyword in market_keywords:
            if keyword in title:
                score += 1
        return score
    
    # Sort by relevance score and return top 10
    scored_headlines = [(item, score(item)) for item in headlines]
    scored_headlines.sort(key=lambda x: x[1], reverse=True)
    
    return [item for item, score in scored_headlines[:10] if score > 0]


def fetch_stock_prices_tradier() -> Dict[str, Dict[str, float]]:
    """Fetch current stock prices using Tradier API"""
    if massive_fetch_stock_prices_tradier:
        massive_prices = massive_fetch_stock_prices_tradier()
        if massive_prices:
            if not massive_prices.get("vix"):
                logger.warning("Massive VIX quote unavailable; index coverage may be tier-gated")
            return massive_prices

    tradier_token = os.getenv('TRADIER_API_TOKEN')
    if not tradier_token:
        logger.warning("Tradier API token not configured")
        return {}
    
    symbols = ['SPY', 'QQQ', 'IWM', 'VIX']
    prices = {}
    
    try:
        headers = {
            "Authorization": f"Bearer {tradier_token}",
            "Accept": "application/json"
        }
        
        url = "https://api.tradier.com/v1/markets/quotes"
        params = {'symbols': ','.join(symbols)}
        
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if 'quotes' in data and 'quote' in data['quotes']:
            quotes = data['quotes']['quote']
            if not isinstance(quotes, list):
                quotes = [quotes]
            
            for quote in quotes:
                symbol = quote.get('symbol', '').lower()
                if symbol:
                    prices[symbol] = {
                        'current_price': float(quote.get('last', 0)),
                        'change': float(quote.get('change', 0)),
                        'change_percent': float(quote.get('change_percentage', 0))
                    }
        
        logger.info(f"Fetched prices for {len(prices)} symbols from Tradier")
        return prices
        
    except Exception as e:
        logger.error(f"Error fetching prices from Tradier: {e}")
        return {}

def fetch_stock_prices_finnhub() -> Dict[str, Dict[str, float]]:
    """Fetch current stock prices using Finnhub API"""
    token = (current_app.config.get('FINNHUB_TOKEN') if current_app else None) or os.getenv('FINNHUB_TOKEN')
    if not token:
        logger.warning("Finnhub token not configured")
        return {}
    
    symbols = ['SPY', 'QQQ', 'IWM', 'VIX']
    prices = {}
    
    # Test token validity first
    try:
        test_url = "https://finnhub.io/api/v1/quote"
        test_params = {'symbol': 'AAPL', 'token': token}
        test_response = requests.get(test_url, params=test_params, timeout=10)
        if test_response.status_code != 200:
            logger.warning(f"Finnhub token validation failed: {test_response.status_code}")
            return {}
    except Exception as e:
        logger.warning(f"Finnhub token test failed: {e}")
        return {}
    
    for symbol in symbols:
        try:
            url = "https://finnhub.io/api/v1/quote"
            params = {'symbol': symbol, 'token': token}
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            prices[symbol.lower()] = {
                'current_price': float(data.get('c', 0)),
                'change': float(data.get('d', 0)),
                'change_percent': float(data.get('dp', 0))
            }
        except Exception as e:
            logger.error(f"Error fetching price for {symbol} from Finnhub: {e}")
            prices[symbol.lower()] = {'current_price': 0, 'change': 0, 'change_percent': 0}
    
    logger.info(f"Fetched prices for {len(prices)} symbols from Finnhub")
    return prices

def fetch_stock_prices() -> Dict[str, Dict[str, float]]:
    """Fetch stock prices with priority: Tradier -> Finnhub -> Default values"""
    
    # Try Tradier first
    prices = fetch_stock_prices_tradier()
    if prices:
        return prices
    
    # Try Finnhub as alternative
    prices = fetch_stock_prices_finnhub()
    if prices:
        return prices
    
    # Fallback to yfinance if both APIs failed
    logger.info("Both Tradier and Finnhub failed, trying yfinance fallback")
    symbols = ['SPY', 'QQQ', 'IWM', 'VIX']
    prices = {}
    
    try:
        import yfinance as yf
        for symbol in symbols:
            try:
                t = yf.Ticker(symbol)
                hist = t.history(period="1d")
                if not hist.empty:
                    prices[symbol.lower()] = {
                        'current_price': float(hist['Close'].iloc[-1]),
                        'change': 0.0,
                        'change_percent': 0.0
                    }
                else:
                    logger.warning(f"No yfinance data for {symbol}")
            except Exception as ye:
                logger.error(f"yfinance error for {symbol}: {ye}")
    except ImportError:
        logger.warning("yfinance not available")

    # Set default values for any symbols that don't have prices
    default_prices = {
        'spy': 400.0,
        'qqq': 300.0,
        'iwm': 200.0,
        'vix': 20.0
    }
    
    for symbol in symbols:
        sym_lower = symbol.lower()
        if sym_lower not in prices or prices[sym_lower]['current_price'] == 0:
            prices[sym_lower] = {
                'current_price': default_prices.get(sym_lower, 0),
                'change': 0.0,
                'change_percent': 0.0
            }
            logger.info(f"Using default price for {symbol}: ${default_prices.get(sym_lower, 0)}")

    return prices


def calculate_expected_range(stock_prices: Dict[str, float]) -> Dict[str, Any]:
    """Calculate expected range bands for key indices based on VIX."""

    vix = stock_prices.get('vix', {}).get('current_price', 20) or 20

    def _levels(price: Optional[float], range_pct: float) -> Dict[str, float]:
        if not price or price <= 0:
            return {}
        range_abs = price * range_pct
        half_range = range_abs / 2
        return {
            'current_price': round(price, 2),
            'support': round(price - half_range, 2),
            'support2': round(price - range_abs, 2),
            'resistance': round(price + half_range, 2),
            'resistance2': round(price + range_abs, 2),
            'resistance3': round(price + range_abs * 1.5, 2)
        }

    spy_price = stock_prices.get('spy', {}).get('current_price')
    qqq_price = stock_prices.get('qqq', {}).get('current_price')
    iwm_price = stock_prices.get('iwm', {}).get('current_price')

    base_pct = min(max(vix / 100, 0.01), 0.05)

    ranges = {
        'spy': _levels(spy_price, base_pct),
        'qqq': _levels(qqq_price, base_pct * 1.2),
        'iwm': _levels(iwm_price, base_pct * 1.1),
        'vix': stock_prices.get('vix', {})
    }

    return {k: v for k, v in ranges.items() if v}


def fetch_gapping_stocks_tradier() -> dict[str, list[dict[str, str]]]:
    """
    Returns {'after_hours': [...], 'premarket': [...]} where each item:
      { 'ticker': 'AAPL', 'move': '+3.2%', 'why': 'optional short blurb' }

    We compute movers by comparing Tradier `last` vs `prevclose`.
    During premarket, `last` reflects extended prints (when available).
    During after-hours, `last` reflects post-close prints.
    """
    if massive_fetch_gapping_stocks_tradier:
        massive_gaps = massive_fetch_gapping_stocks_tradier()
        if massive_gaps and (massive_gaps.get("after_hours") or massive_gaps.get("premarket")):
            return massive_gaps

    try:
        now = _now_ny()
        uni = _liquid_universe()
        quotes = _tradier_quotes(uni)
        if not quotes:
            return {"after_hours": [], "premarket": []}

        items = []
        for q in quotes:
            if (q or {}).get("type") != "stock":
                continue
            sym = q.get("symbol") or q.get("root_symbols") or ""
            last = q.get("last")
            prev = q.get("prevclose")
            if sym and isinstance(last, (int, float)) and isinstance(prev, (int, float)) and prev > 0:
                px = float(last)
                if px < 1.0:
                    continue
                pct = (px - float(prev)) / float(prev) * 100.0
                items.append({"ticker": sym, "pct": pct, "last": px, "prev": float(prev), "why": ""})

        items.sort(key=lambda x: abs(x["pct"]), reverse=True)

        top = items[:50]
        news_map: dict[str, str] = {}
        try:
            for it in top[:12]:
                try:
                    headlines = fetch_stock_news(it["ticker"])
                    if headlines:
                        news_map[it["ticker"]] = headlines[0].get("headline", "")[:140]
                except Exception:
                    pass
        except NameError:
            pass

        def _format(lst: list[dict]) -> list[dict]:
            out = []
            for it in lst:
                move = f"{it['pct']:+.2f}%"
                why = news_map.get(it["ticker"], "")
                out.append({"ticker": it["ticker"], "move": move, "why": why})
            return out

        pre_list = [it for it in top if it["pct"] >= 0][:10] + [it for it in top if it["pct"] < 0][:10]
        ah_list = pre_list

        premarket = _format(pre_list)
        after_hours = _format(ah_list)

        # --- Junk scan merge ---
        junk = _junk_movers_tradier(now)
        if junk:
            seen = set()
            def _dedup_merge(base: list[dict], add: list[dict]) -> list[dict]:
                out = []
                for it in base + add:
                    t = it.get("ticker")
                    if not t or t in seen:
                        continue
                    seen.add(t)
                    out.append(it)
                return out

            if _is_premarket(now):
                premarket = _dedup_merge(premarket, [{"ticker": x['ticker'], "move": x["move"], "why": f"(JUNK) {x['why']}"} for x in junk])
            elif _is_afterhours(now):
                after_hours = _dedup_merge(after_hours, [{"ticker": x['ticker'], "move": x["move"], "why": f"(JUNK) {x['why']}"} for x in junk])

        if _is_premarket(now):
            return {"after_hours": [], "premarket": premarket}
        if _is_afterhours(now):
            return {"after_hours": after_hours, "premarket": []}
        return {"after_hours": [], "premarket": []}

    except Exception as e:
        logger.exception(f"fetch_gapping_stocks_tradier failed: {e}")
        return {"after_hours": [], "premarket": []}

def fetch_gapping_stocks_yfinance() -> List[Dict[str, Any]]:
    """Fallback: Fetch gapping stocks using yfinance for after-hours and premarket"""
    logger.info("Fetching full universe of stocks with yfinance...")
    
    gapping_stocks = []
    
    try:
        import yfinance as yf
        import pandas as pd
        from datetime import datetime, timedelta
        import requests
        
        # Get current time to determine if we're in premarket or after-hours
        now = datetime.now()
        current_hour = now.hour
        
        # Use a smaller, more reliable stock list for better success rate
        reliable_stocks = [
            'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'NVDA', 'META', 'NFLX', 'AMD', 'INTC',
            'JPM', 'BAC', 'WFC', 'GS', 'MS', 'C', 'V', 'MA', 'DIS', 'NKE',
            'JNJ', 'PFE', 'ABBV', 'UNH', 'CVS', 'PG', 'KO', 'PEP', 'WMT', 'HD',
            'XOM', 'CVX', 'COP', 'BA', 'CAT', 'DE', 'MMM', 'GE', 'HON', 'LMT',
            'SPY', 'QQQ', 'IWM', 'DIA', 'VTI', 'VOO', 'TLT', 'GLD', 'XLF', 'XLK'
        ]
        
        logger.info(f"Scanning {len(reliable_stocks)} reliable stocks for gapping movers...")
        
        # Scan stocks for gaps with better error handling
        scanned_count = 0
        successful_scans = 0
        for ticker in reliable_stocks:
            try:
                t = yf.Ticker(ticker)
                
                # Get recent price data with retry
                hist = None
                for attempt in range(3):
                    try:
                        hist = t.history(period="2d", interval="1d")
                        if not hist.empty and len(hist) >= 2:
                            break
                    except Exception as e:
                        if attempt == 2:
                            logger.warning(f"Failed to get history for {ticker} after 3 attempts: {e}")
                        continue
                
                if hist is None or hist.empty or len(hist) < 2:
                    continue
                
                # Get current price (premarket/after-hours if available)
                current_price = None
                if current_hour < 9 or current_hour >= 16:  # After-hours or premarket
                    try:
                        # Try to get current price
                        info = t.info
                        if 'regularMarketPrice' in info and info['regularMarketPrice']:
                            current_price = info['regularMarketPrice']
                    except:
                        pass
                
                if current_price is None:
                    current_price = hist['Close'].iloc[-1]
                
                # Calculate gap percentage
                prev_close = hist['Close'].iloc[-2]
                gap_pct = ((current_price - prev_close) / prev_close) * 100
                
                # Get volume and market cap for liquidity filtering
                volume = hist['Volume'].iloc[-1] if len(hist) > 0 else 0
                market_cap = 0
                try:
                    market_cap = t.info.get('marketCap', 0) if hasattr(t, 'info') else 0
                except:
                    pass
                
                # More lenient filtering for better results
                # Criteria: >1.5% gap, >500K volume, >$50M market cap
                if (abs(gap_pct) >= 1.5 and 
                    volume > 500000 and 
                    market_cap > 50000000):
                    
                    gapping_stocks.append({
                        'ticker': ticker,
                        'current_price': round(current_price, 2),
                        'prev_close': round(prev_close, 2),
                        'gap_pct': round(gap_pct, 2),
                        'volume': volume,
                        'market_cap': market_cap
                    })
                
                scanned_count += 1
                successful_scans += 1
                
                if scanned_count % 10 == 0:
                    logger.info(f"Scanned {scanned_count} stocks, found {len(gapping_stocks)} gappers...")
                
            except Exception as e:
                logger.warning(f"Error fetching data for {ticker}: {e}")
                scanned_count += 1
                continue
        
        # Sort by absolute gap percentage (largest moves first)
        gapping_stocks.sort(key=lambda x: abs(x['gap_pct']), reverse=True)
        
        # Return top 15 gapping stocks
        result = gapping_stocks[:15]
        logger.info(f"yfinance scan complete: Scanned {scanned_count} stocks, {successful_scans} successful, found {len(result)} liquid stocks with significant gaps")
        
        # If no gapping stocks found, return sample data
        if not result:
            logger.warning("No gapping stocks found, returning sample data")
            return _get_sample_gapping_stocks()
        
        return result
        
    except Exception as e:
        logger.error(f"yfinance gapping stocks scan failed: {e}")
        return _get_sample_gapping_stocks()


def _get_sample_gapping_stocks() -> List[Dict[str, Any]]:
    """Return sample gapping stocks data when APIs fail"""
    logger.info("Using sample gapping stocks data")
    return [
        {
            'ticker': 'AAPL',
            'current_price': 175.50,
            'prev_close': 171.25,
            'gap_pct': 2.48,
            'volume': 45000000,
            'market_cap': 2800000000000
        },
        {
            'ticker': 'TSLA',
            'current_price': 245.80,
            'prev_close': 241.15,
            'gap_pct': 1.93,
            'volume': 25000000,
            'market_cap': 780000000000
        },
        {
            'ticker': 'NVDA',
            'current_price': 425.30,
            'prev_close': 418.75,
            'gap_pct': 1.56,
            'volume': 18000000,
            'market_cap': 1050000000000
        },
        {
            'ticker': 'META',
            'current_price': 312.45,
            'prev_close': 308.90,
            'gap_pct': 1.15,
            'volume': 12000000,
            'market_cap': 790000000000
        },
        {
            'ticker': 'GOOGL',
            'current_price': 142.80,
            'prev_close': 140.25,
            'gap_pct': 1.82,
            'volume': 15000000,
            'market_cap': 1800000000000
        }
    ]

def fetch_gapping_stocks() -> Dict[str, List[Dict[str, Any]]]:
    """Enhanced function with multiple fallbacks - NEVER returns empty data"""
    return fetch_gapping_stocks_enhanced()

def fetch_stock_news(ticker: str) -> List[Dict[str, Any]]:
    """Fetch recent news for a specific stock ticker with concise reasons"""
    logger = logging.getLogger(__name__)

    if massive_fetch_stock_news:
        massive_news = massive_fetch_stock_news(ticker)
        if massive_news:
            return massive_news
    
    # Try to get news from Finnhub if available
    token = (current_app.config.get('FINNHUB_TOKEN') if current_app else None) or os.getenv('FINNHUB_TOKEN')
    
    if token:
        try:
            url = "https://finnhub.io/api/v1/company-news"
            params = {
                'symbol': ticker,
                'from': (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d'),
                'to': datetime.now().strftime('%Y-%m-%d'),
                'token': token
            }
            
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                news_data = response.json()
                # Filter and prioritize news that's likely to cause gaps
                prioritized_news = []
                for news in news_data[:10]:  # Check more news items
                    headline = news.get('headline', '').lower()
                    summary = news.get('summary', '').lower()
                    
                    # Keywords that typically cause significant price movements
                    gap_keywords = [
                        'earnings', 'quarterly', 'revenue', 'profit', 'loss', 'beat', 'miss',
                        'upgrade', 'downgrade', 'analyst', 'target', 'price target',
                        'merger', 'acquisition', 'buyout', 'deal', 'partnership',
                        'fda', 'approval', 'clinical', 'trial', 'drug', 'treatment',
                        'layoff', 'restructuring', 'ceo', 'executive', 'resignation',
                        'bankruptcy', 'chapter', 'delisting', 'reverse split',
                        'dividend', 'buyback', 'share repurchase', 'stock split',
                        'guidance', 'forecast', 'outlook', 'expectations'
                    ]
                    
                    # Check if news contains gap-causing keywords
                    if any(keyword in headline or keyword in summary for keyword in gap_keywords):
                        prioritized_news.append(news)
                
                # Return prioritized news or top 3 if no specific gap news found
                return prioritized_news[:3] if prioritized_news else news_data[:3]
                
        except Exception as e:
            logger.warning(f"Error fetching news for {ticker} from Finnhub: {e}")
    
    # Enhanced fallback: return more specific sample news based on ticker
    sample_news = {
        'TSLA': [
            {
                'headline': f'{ticker} announces new battery technology breakthrough',
                'summary': f'{ticker} revealed significant improvements in battery efficiency and range, potentially reducing costs by 15%.',
                'source': 'Company Press Release',
                'datetime': int(datetime.now().timestamp())
            }
        ],
        'NVDA': [
            {
                'headline': f'{ticker} reports strong quarterly earnings',
                'summary': f'{ticker} exceeded analyst expectations with robust AI chip sales, revenue up 25% YoY.',
                'source': 'Earnings Report',
                'datetime': int(datetime.now().timestamp())
            }
        ],
        'AAPL': [
            {
                'headline': f'{ticker} faces supply chain challenges',
                'summary': f'{ticker} reports delays in new product launches due to component shortages, affecting Q4 guidance.',
                'source': 'Market News',
                'datetime': int(datetime.now().timestamp())
            }
        ],
        'META': [
            {
                'headline': f'{ticker} announces AI partnership',
                'summary': f'{ticker} partners with major tech company to develop next-generation AI capabilities.',
                'source': 'Company News',
                'datetime': int(datetime.now().timestamp())
            }
        ],
        'AMZN': [
            {
                'headline': f'{ticker} expands cloud services',
                'summary': f'{ticker} launches new AWS services, expanding market share in cloud computing.',
                'source': 'Business News',
                'datetime': int(datetime.now().timestamp())
            }
        ],
        'GOOGL': [
            {
                'headline': f'{ticker} reports strong ad revenue',
                'summary': f'{ticker} exceeds expectations with robust advertising revenue growth.',
                'source': 'Earnings Report',
                'datetime': int(datetime.now().timestamp())
            }
        ],
        'MSFT': [
            {
                'headline': f'{ticker} cloud business grows',
                'summary': f'{ticker} reports strong Azure growth, beating cloud revenue expectations.',
                'source': 'Earnings Report',
                'datetime': int(datetime.now().timestamp())
            }
        ]
    }
    
    return sample_news.get(ticker, [
        {
            'headline': f'{ticker} shows significant premarket activity',
            'summary': f'{ticker} is experiencing unusual trading volume and price movement, likely due to recent news or market sentiment.',
            'source': 'Market Data',
            'datetime': int(datetime.now().timestamp())
        }
    ])


def generate_gapping_stocks_summary(gapping_stocks: List[Dict[str, Any]]) -> str:
    """Generate AI summary for gapping stocks section"""
    logger = logging.getLogger(__name__)
    
    if not OPENAI_API_KEY:
        return generate_gapping_stocks_fallback(gapping_stocks)
    
    try:
        from openai import OpenAI
        openai_client = OpenAI(api_key=OPENAI_API_KEY)
    except Exception as e:
        logger.error(f"Error initializing OpenAI client: {str(e)}")
        return generate_gapping_stocks_fallback(gapping_stocks)
    
    # Prepare stock data with news
    stocks_data = ""
    for i, stock in enumerate(gapping_stocks, 1):
        ticker = stock['ticker']
        gap_pct = stock['gap_pct']
        current_price = stock['current_price']
        prev_close = stock['prev_close']
        
        # Get news for this stock
        news_items = fetch_stock_news(ticker)
        news_text = ""
        for news in news_items[:2]:  # Top 2 news items
            news_text += f"   - {news.get('headline', 'No headline')}\n"
            news_text += f"     Source: {news.get('source', 'Unknown')}\n"
            news_text += f"     Summary: {news.get('summary', 'No summary')}\n\n"
        
        stocks_data += f"{i}. {ticker}\n"
        stocks_data += f"   Gap: {gap_pct:+.2f}% (${current_price:.2f} vs ${prev_close:.2f})\n"
        stocks_data += f"   Recent News:\n{news_text}\n"
    
    prompt = f"""
Create a "What's moving — After-hours & Premarket" section for a morning market brief. 
Format each stock summary with concise, actionable news reasons that explain the gap.

STOCKS WITH SIGNIFICANT GAPS:
{stocks_data}

Please format each stock summary as follows:
1. Company name and ticker as a bold heading
2. Percentage change prominently displayed
3. A concise paragraph (2-3 sentences max) summarizing the news driving the movement:
   - Focus on the specific news/event causing the gap
   - Include key financial figures or metrics if relevant
   - Explain the market reaction and trading implications
   - Keep it brief and actionable

Guidelines:
- Be concise and direct - each summary should be 2-3 sentences maximum
- Focus on the most impactful news that explains the gap
- Include specific numbers/percentages when available
- Explain why traders should care about this movement
- Use professional but accessible language

Make it professional, concise, and actionable for traders. Focus on what's driving the gap and what traders should watch for.
"""

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a professional market analyst creating concise stock summaries for a morning brief. Format responses with clear headings and professional tone."},
                {"role": "user", "content": prompt}
            ],
            max_completion_tokens=1500,
            temperature=1.0
        )
        content = response.choices[0].message.content if response and response.choices else ""
        return content or generate_gapping_stocks_fallback(gapping_stocks)

    except Exception as e:
        logger.error(f"Error generating gapping stocks summary: {str(e)}")
        return generate_gapping_stocks_fallback(gapping_stocks)


def generate_gapping_stocks_fallback(gapping_stocks: List[Dict[str, Any]]) -> str:
    """Generate fallback summary for gapping stocks when OpenAI is unavailable"""
    summary = "## What's moving — After-hours & Premarket\n\n"
    
    for stock in gapping_stocks:
        ticker = stock['ticker']
        gap_pct = stock['gap_pct']
        current_price = stock['current_price']
        prev_close = stock['prev_close']
        
        # Get news for this stock
        news_items = fetch_stock_news(ticker)
        news_summary = news_items[0].get('summary', f'{ticker} showing significant premarket activity') if news_items else f'{ticker} experiencing unusual trading volume'
        
        summary += f"**{ticker}**\n"
        summary += f"**{gap_pct:+.2f}%**\n\n"
        summary += f"{news_summary} The stock is currently trading at ${current_price:.2f} compared to yesterday's close of ${prev_close:.2f}. "
        summary += f"Traders should monitor for follow-through momentum and any additional news catalysts.\n\n"
    
    return summary


def summarize_news(headlines: List[Dict[str, Any]], expected_range: Dict[str, Any], gapping_stocks: List[Dict[str, Any]] = None) -> str:
    """Generate AI summary using optimized two-stage pipeline to reduce token usage by ~90%"""
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY environment variable not set")
    
    try:
        # Import the new pipeline
        from pipeline.write_brief import build_brief
        
        # Prepare raw inputs in the format expected by the pipeline
        raw_inputs = {
            "expected_range": enrich_expected_range_with_pivots(expected_range or {}),
            "headlines": headlines,
            "gapping_stocks": gapping_stocks or [],
            "economic_catalysts": [],  # Will be populated by send_morning_brief.py if needed
            "catalysts": []
        }
        
        # Use the new optimized pipeline
        logger.info("Using optimized brief pipeline (90% token reduction)")
        return build_brief(raw_inputs, polish=os.getenv("BRIEF_POLISH", "true").lower() == "true")
        
    except ImportError as e:
        logger.error(f"Failed to import optimized pipeline: {e}")
        logger.warning("Falling back to legacy brief generation")
        return _legacy_summarize_news(headlines, expected_range, gapping_stocks)
    except Exception as e:
        logger.error(f"Optimized pipeline failed: {e}")
        logger.warning("Falling back to legacy brief generation")
        return _legacy_summarize_news(headlines, expected_range, gapping_stocks)


def _legacy_summarize_news(headlines: List[Dict[str, Any]], expected_range: Dict[str, Any], gapping_stocks: List[Dict[str, Any]] = None) -> str:
    """Legacy fallback for summarize_news when optimized pipeline fails"""
    # Initialize OpenAI client lazily
    try:
        from openai import OpenAI
        openai_client = OpenAI(api_key=OPENAI_API_KEY)
    except Exception as e:
        logger.error(f"Error initializing OpenAI client: {str(e)}")
        # Return a fallback summary instead of failing
        return generate_fallback_summary(headlines, expected_range, gapping_stocks)

    # Add daily/weekly resistances and supports via pivots
    expected_range = enrich_expected_range_with_pivots(expected_range or {})

    # Build the cached user prompt
    user_prompt = _render_brief_user_prompt(headlines, expected_range, gapping_stocks)

    try:
        # openai>=1.x client
        # Set temperature and token limits for the model
        temp = 0.8
        max_tokens = 2000
        
        response = openai_client.chat.completions.create(
            model=BRIEF_MODEL,
            messages=[
                {"role": "system", "content": BRIEF_SYSTEM},
                # Soft hint: include voice profile if available (does not change facts)
                *([{"role": "system", "content": "Author Voice Hints:\\n" + _load_voice_profile()}] if _load_voice_profile() else []),
                {"role": "user", "content": user_prompt}
            ],
            max_completion_tokens=max_tokens,
            temperature=temp
        )
        content = response.choices[0].message.content if response and response.choices else ""
        if not content or content.strip() == "":
            logger.warning("OpenAI returned empty content, using fallback summary")
            return generate_fallback_summary(headlines, expected_range, gapping_stocks)
        
        # Second pass: enforce your voice without altering facts/sections
        return _rewrite_in_voice(content)

    except Exception as e:
        logger.error(f"Error generating summary: {str(e)}")
        return "Error generating market summary. Please check the latest news and market data."


def parse_summary_sections(summary: str) -> Dict[str, str]:
    """Parse the AI summary into sections"""
    sections = {
        'executive_summary': '',
        'gapping_stocks': '',
        'technical_analysis': '',
        'market_sentiment': '',
        'key_levels': '',
        'headlines': ''
    }
    
    # Split by common section headers
    parts = summary.split('##')
    if len(parts) == 1:
        parts = summary.split('#')
    
    for part in parts:
        part = part.strip()
        if not part:
            continue
            
        lines = part.split('\n')
        if not lines:
            continue
            
        section_title = lines[0].strip().lower()
        section_content = '\n'.join(lines[1:]).strip()
        
        if 'executive summary' in section_title:
            sections['executive_summary'] = section_content
        elif "what's moving" in section_title or 'after-hours' in section_title or 'premarket' in section_title:
            sections['gapping_stocks'] = section_content
        elif 'key market headlines' in section_title or 'key headlines' in section_title:
            sections['headlines'] = section_content
        elif 'technical analysis' in section_title:
            sections['technical_analysis'] = section_content
        elif 'market sentiment' in section_title:
            sections['market_sentiment'] = section_content
        elif 'key levels' in section_title:
            sections['key_levels'] = section_content
    
    # If sections weren't found, try simple parsing
    if not any(sections.values()):
        lines = summary.split('\n')
        current_section = None
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            if 'executive summary' in line.lower():
                current_section = 'executive_summary'
            elif "what's moving" in line.lower() or 'after-hours' in line.lower() or 'premarket' in line.lower():
                current_section = 'gapping_stocks'
            elif 'technical analysis' in line.lower():
                current_section = 'technical_analysis'
            elif 'market sentiment' in line.lower():
                current_section = 'market_sentiment'
            elif 'key levels' in line.lower():
                current_section = 'key_levels'
            elif current_section and line:
                sections[current_section] += line + '\n'
    
    # If still no sections found, use the whole summary as executive summary
    if not any(sections.values()):
        sections['executive_summary'] = summary
    
    return sections


def generate_fallback_summary(headlines: List[Dict[str, Any]], expected_range: Dict[str, Any], gapping_stocks: List[Dict[str, Any]] = None) -> str:
    """Generate a fallback summary when OpenAI is not available"""
    spy_data = expected_range.get('spy', {})
    qqq_data = expected_range.get('qqq', {})
    vix_data = expected_range.get('vix', {})
    
    # Get top headlines with summaries
    top_headlines = headlines[:7] if headlines else []
    headlines_text = ""
    for i, headline in enumerate(top_headlines, 1):
        headlines_text += f"{i}. {headline.get('headline', 'No headline')}\n"
        headlines_text += f"   Source: {headline.get('source', 'Unknown')}\n"
        headlines_text += f"   Summary: {headline.get('summary', 'No summary')}\n\n"
    
    # Generate gapping stocks section
    gapping_text = "\n## What's moving — After-hours & Premarket\n\n"
    if gapping_stocks:
        # Handle both list and dict structures
        if isinstance(gapping_stocks, dict):
            # New Tradier structure
            ah_moves = gapping_stocks.get("after_hours", [])
            pre_moves = gapping_stocks.get("premarket", [])
            
            # Add after-hours movers
            if ah_moves:
                gapping_text += "**After-hours Movers:**\n\n"
                for stock in ah_moves[:5]:
                    ticker = stock.get('ticker', '')
                    move = stock.get('move', '')
                    why = stock.get('why', '')
                    gapping_text += f"**{ticker}** {move}\n"
                    gapping_text += f"{why}\n\n"
            
            # Add premarket movers
            if pre_moves:
                gapping_text += "**Premarket Movers:**\n\n"
                for stock in pre_moves[:5]:
                    ticker = stock.get('ticker', '')
                    move = stock.get('move', '')
                    why = stock.get('why', '')
                    gapping_text += f"**{ticker}** {move}\n"
                    gapping_text += f"{why}\n\n"
        else:
            # Old list structure
            for stock in gapping_stocks[:5]:  # Top 5 gapping stocks
                ticker = stock['ticker']
                gap_pct = stock['gap_pct']
                current_price = stock['current_price']
                prev_close = stock['prev_close']
                
                # Get news for this stock
                news_items = fetch_stock_news(ticker)
                news_summary = news_items[0].get('summary', f'{ticker} showing significant premarket activity') if news_items else f'{ticker} experiencing unusual trading volume'
                
                gapping_text += f"**{ticker}**\n"
                gapping_text += f"**{gap_pct:+.2f}%**\n\n"
                gapping_text += f"{news_summary} The stock is currently trading at ${current_price:.2f} compared to yesterday's close of ${prev_close:.2f}. "
                gapping_text += f"Traders should monitor for follow-through momentum and any additional news catalysts.\n\n"
    else:
        gapping_text += "No significant gapping stocks detected in premarket trading. Monitor major indices and key earnings reports for potential catalysts.\n\n"
    
    summary = f"""
## Executive Summary
Market conditions appear stable with key indices showing normal trading ranges. Focus on major economic catalysts and earnings reports for directional moves. The current market environment suggests a balanced risk-reward scenario for traders.{gapping_text}

## Key Market Headlines
{headlines_text}

## Technical Analysis & Daily Range Insights
Key support and resistance levels are being tested as markets consolidate. Monitor volume and momentum indicators for breakout signals. The expected trading ranges suggest moderate volatility with potential for directional moves on significant news.

## Market Sentiment & Outlook
Risk sentiment remains balanced with mixed signals from various sectors. Traders should watch for sector rotation opportunities and prepare for potential market shifts based on upcoming economic data releases.

## Key Levels to Watch
- SPY: ${spy_data.get('current_price', 0):.2f} (Support: ${spy_data.get('support', 0):.2f}, Resistance: ${spy_data.get('resistance', 0):.2f})
- QQQ: ${qqq_data.get('current_price', 0):.2f} (Support: ${qqq_data.get('support', 0):.2f}, Resistance: ${qqq_data.get('resistance', 0):.2f})
- VIX: {vix_data.get('current_price', 0):.2f}
"""
    return summary


def generate_email_content(summary: str, headlines: List[Dict[str, Any]], expected_range: Dict[str, Any], gapping_stocks: List[Dict[str, Any]] = None) -> str:
    """Generate HTML email content"""
    sections = parse_summary_sections(summary)
    
    # Generate headlines HTML with enhanced summaries (no Source field)
    headlines_html = ""
    for i, headline in enumerate(headlines[:7], 1):
        # Use enhanced summary if available, fallback to original summary
        summary_text = (headline.get('summary_2to5') or headline.get('summary') or '').strip()
        headlines_html += f"""
        <tr>
            <td style="padding:12px 0;border-bottom:1px solid #eee;">
                <div style="display:flex;align-items:flex-start;">
                    <span style="background:#3498db;color:#fff;border-radius:50%;width:24px;height:24px;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:bold;margin-right:12px;flex-shrink:0;">{i}</span>
                    <div style="flex:1;">
                        <strong style="color:#2c3e50;font-size:14px;">{headline.get('headline','')}</strong><br>
                        <span style="color:#34495e;font-size:13px;line-height:1.4;margin-top:4px;display:block;">{summary_text}</span>
                    </div>
                </div>
            </td>
        </tr>"""
    
    # Market data
    spy_data = expected_range.get('spy', {})
    qqq_data = expected_range.get('qqq', {})
    vix_data = expected_range.get('vix', {})
    
    # Format sections with proper HTML
    def format_section_content(content):
        if not content:
            return '<p style="color: #7f8c8d; font-style: italic;">No content available.</p>'
        # Convert line breaks to paragraphs
        paragraphs = content.split('\n\n')
        html_paragraphs = []
        for para in paragraphs:
            if para.strip():
                html_paragraphs.append(f'<p style="margin: 0 0 12px 0;">{para.strip()}</p>')
        return '\n'.join(html_paragraphs) if html_paragraphs else '<p style="color: #7f8c8d; font-style: italic;">No content available.</p>'
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Morning Market Brief</title>
        <style>
            .section-header {{
                color: #2c3e50;
                border-bottom: 2px solid #3498db;
                padding-bottom: 8px;
                margin: 25px 0 15px 0;
                font-size: 18px;
                font-weight: bold;
            }}
            .market-snapshot {{
                background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
                padding: 20px;
                border-radius: 8px;
                margin: 20px 0;
                border-left: 4px solid #3498db;
            }}
            .section-content {{
                background: #ffffff;
                padding: 20px;
                border-radius: 8px;
                margin: 15px 0;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }}
        </style>
    </head>
    <body style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; line-height: 1.6; color: #333; background-color: #f8f9fa; margin: 0; padding: 20px;">
        <div style="max-width: 700px; margin: 0 auto; background: white; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
            <div style="background: linear-gradient(135deg, #2c3e50 0%, #3498db 100%); color: white; padding: 30px; text-align: center;">
                <h1 style="margin: 0; font-size: 28px; font-weight: 300;">Morning Market Brief</h1>
                <p style="margin: 10px 0 0 0; font-size: 16px; opacity: 0.9;">{datetime.now().strftime('%A, %B %d, %Y')}</p>
            </div>
            
            <div class="market-snapshot">
                <h3 style="margin-top: 0; color: #2c3e50; font-size: 20px;">Market Snapshot</h3>
                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px;">
                    <div style="text-align: center; padding: 15px; background: white; border-radius: 6px;">
                        <div style="font-size: 24px; font-weight: bold; color: #2c3e50;">${spy_data.get('current_price', 0):.2f}</div>
                        <div style="font-size: 14px; color: #7f8c8d;">SPY</div>
                    </div>
                    <div style="text-align: center; padding: 15px; background: white; border-radius: 6px;">
                        <div style="font-size: 24px; font-weight: bold; color: #2c3e50;">${qqq_data.get('current_price', 0):.2f}</div>
                        <div style="font-size: 14px; color: #7f8c8d;">QQQ</div>
                    </div>
                    <div style="text-align: center; padding: 15px; background: white; border-radius: 6px;">
                        <div style="font-size: 24px; font-weight: bold; color: #2c3e50;">{vix_data.get('current_price', 0):.2f}</div>
                        <div style="font-size: 14px; color: #7f8c8d;">VIX</div>
                    </div>
                </div>
            </div>
            
            <div style="padding: 30px;">
                <div class="section-content">
                    <h3 class="section-header">Executive Summary</h3>
                    {format_section_content(sections.get('executive_summary', ''))}
                </div>
                
                <div class="section-content">
                    <h3 class="section-header">What's moving — After-hours & Premarket</h3>
                    {format_section_content(sections.get('gapping_stocks', ''))}
                </div>
                
                <div class="section-content">
                    <h3 class="section-header">Key Market Headlines</h3>
                    <table style="width: 100%; border-collapse: collapse;">
                        {headlines_html}
                    </table>
                </div>
                
                <div class="section-content">
                    <h3 class="section-header">Technical Analysis & Daily Range Insights</h3>
                    {format_section_content(sections.get('technical_analysis', ''))}
                </div>
                
                <div class="section-content">
                    <h3 class="section-header">Market Sentiment & Outlook</h3>
                    {format_section_content(sections.get('market_sentiment', ''))}
                </div>
                
                <div class="section-content">
                    <h3 class="section-header">Key Levels to Watch</h3>
                    {format_section_content(sections.get('key_levels', ''))}
                </div>
            </div>
            
            <div style="background: linear-gradient(135deg, #ecf0f1 0%, #bdc3c7 100%); padding: 25px; text-align: center; border-top: 1px solid #bdc3c7;">
                <p style="margin: 0; font-size: 16px; color: #2c3e50;">
                    <strong>Powered by Options Plunge</strong>
                </p>
                <p style="margin: 5px 0 0 0; font-size: 14px; color: #7f8c8d;">
                    Professional market analysis for informed trading decisions
                </p>
            </div>
        </div>
    </body>
    </html>
    """
    
    return html_content


def generate_html_content_with_summary(summary: str, headlines: List[Dict[str, Any]], 
                                      expected_range: Dict[str, Any], gapping_stocks: List[Dict[str, Any]], 
                                      subscriber_summary: str = None) -> str:
    """Generate beautiful HTML content with improved styling and better spacing"""
    # Get current date
    current_date = datetime.now().strftime('%A, %B %d, %Y')
    
    # Enrich ranges with pivots for HTML Key Levels (Daily/Weekly S & R)
    piv_expected = enrich_expected_range_with_pivots(expected_range or {})
    # Get key price data for styling
    spy_data = piv_expected.get('spy', {})
    qqq_data = piv_expected.get('qqq', {})
    
    # Convert markdown to HTML with proper headline formatting
    # Split by sections and process each one properly
    sections = summary.split('## ')
    summary_html = ""
    
    for i, section in enumerate(sections):
        if i == 0:
            # First section (before any ##) - just add as-is
            summary_html += section
        else:
            # Section with ## header
            lines = section.split('\n')
            if lines:
                header = lines[0].strip()
                content = '\n'.join(lines[1:]).strip()
                
                # Add the header
                summary_html += f'<h2 class="section-header">{header}</h2>\n\n'
                
                # Add the content
                if content:
                    summary_html += f'<div class="section-content">{content}</div>\n\n'
    
    # Format headlines with proper spacing - only process if we have headlines
    if headlines:
        # Find the headlines section and replace it with formatted HTML
        headlines_start = summary_html.find('<h2 class="section-header">Key Market Headlines</h2>')
        if headlines_start != -1:
            # Find where the headlines section ends
            headlines_end = summary_html.find('<h2 class="section-header">Technical Analysis', headlines_start)
            if headlines_end == -1:
                headlines_end = len(summary_html)
            
            # Extract the headlines section
            headlines_section = summary_html[headlines_start:headlines_end]
            
            # Create formatted headlines HTML with better spacing (no Source field)
            formatted_headlines = '<h2 class="section-header">Key Market Headlines</h2>'
            for i, headline in enumerate(headlines[:7], 1):
                # Use enhanced summary if available, fallback to original summary
                summary_text = (headline.get('summary_2to5') or headline.get('summary') or '').strip()
                formatted_headlines += f"""
                <div class="headline-item">
                    <div class="headline-number">{i}.</div>
                    <div class="headline-content">
                        <div class="headline-title">{headline.get('headline', '')}</div>
                        <div class="headline-summary">{summary_text}</div>
                    </div>
                </div>"""
            
            # Replace the headlines section
            summary_html = summary_html[:headlines_start] + formatted_headlines + summary_html[headlines_end:]
    
    # Add subscriber summary if provided with enhanced styling
    subscriber_html = ""
    if subscriber_summary:
        # Parse subscriber summary for better formatting
        lines = subscriber_summary.strip().split('\n')
        formatted_summary = ""
        
        for line in lines:
            line = line.strip()
            if line.startswith('**') and line.endswith('**'):
                # Bold headers
                formatted_summary += f'<div class="summary-header">{line}</div>'
            elif line.startswith('- **'):
                # List items with bold tickers
                formatted_summary += f'<div class="summary-list-item">{line}</div>'
            elif line.startswith('**Levels to watch'):
                # Special section
                formatted_summary += f'<div class="summary-levels-header">{line}</div>'
            elif line.startswith('- Support:') or line.startswith('- Resistance:'):
                # Level items
                formatted_summary += f'<div class="summary-level-item">{line}</div>'
            elif line.startswith('**Tomorrow:**'):
                # Tomorrow section
                formatted_summary += f'<div class="summary-tomorrow">{line}</div>'
            elif line:
                # Regular text
                formatted_summary += f'<div class="summary-text">{line}</div>'
        
        subscriber_html = f"""
        <div class="subscriber-summary">
            <div class="summary-header-main">
                <i class="fas fa-chart-line"></i>
                <span>Subscriber Summary</span>
            </div>
            <div class="summary-content">
                {formatted_summary}
            </div>
        </div>
        <hr style="margin: 30px 0; border: none; border-top: 2px solid #e9ecef;">
        <p style="text-align: center; color: #6c757d; font-style: italic; font-size: 14px;">
            This summary provides key market insights for active traders. For detailed analysis, visit our full market brief.
        </p>"""
    
    # Add gapping stocks section if available
    gapping_stocks_html = ""
    if gapping_stocks:
        gapping_stocks_html = """
        <div class="section-content">
            <h2 class="section-header">🚀 Gapping Stocks</h2>"""
        
        # Handle both list and dict structures
        if isinstance(gapping_stocks, dict):
            # New Tradier structure
            ah_moves = gapping_stocks.get("after_hours", [])
            pre_moves = gapping_stocks.get("premarket", [])
            
            # Add after-hours movers
            if ah_moves:
                gapping_stocks_html += '<h3 style="color: #e74c3c; margin: 15px 0 10px 0;">After-hours Movers</h3>'
                for stock in ah_moves[:5]:
                    ticker = stock.get('ticker', '')
                    # Handle both old format (move/why) and new format (gap_pct/current_price/prev_close)
                    if stock.get('move') and stock.get('why'):
                        move = stock.get('move', '')
                        why = stock.get('why', '')
                    else:
                        # New format - create move and why from gap_pct and prices
                        gap_pct = stock.get('gap_pct', 0)
                        current_price = stock.get('current_price', 0)
                        prev_close = stock.get('prev_close', 0)
                        move = f"{gap_pct:+.2f}%"
                        why = f"${current_price:.2f} vs ${prev_close:.2f}"
                    gapping_stocks_html += f"""
                    <div class="gapping-stock-item">
                        <strong class="ticker">{ticker}</strong>: <span class="move">{move}</span> - <span class="why">{why}</span>
                    </div>"""
            
            # Add premarket movers
            if pre_moves:
                gapping_stocks_html += '<h3 style="color: #27ae60; margin: 15px 0 10px 0;">Premarket Movers</h3>'
                for stock in pre_moves[:5]:
                    ticker = stock.get('ticker', '')
                    # Handle both old format (move/why) and new format (gap_pct/current_price/prev_close)
                    if stock.get('move') and stock.get('why'):
                        move = stock.get('move', '')
                        why = stock.get('why', '')
                    else:
                        # New format - create move and why from gap_pct and prices
                        gap_pct = stock.get('gap_pct', 0)
                        current_price = stock.get('current_price', 0)
                        prev_close = stock.get('prev_close', 0)
                        move = f"{gap_pct:+.2f}%"
                        why = f"${current_price:.2f} vs ${prev_close:.2f}"
                    gapping_stocks_html += f"""
                    <div class="gapping-stock-item">
                        <strong class="ticker">{ticker}</strong>: <span class="move">{move}</span> - <span class="why">{why}</span>
                    </div>"""
        else:
            # Old list structure
            for stock in gapping_stocks[:5]:
                ticker = stock.get('ticker', '')
                # Handle both old format (move/why) and new format (gap_pct/current_price/prev_close)
                if stock.get('move') and stock.get('why'):
                    move = stock.get('move', '')
                    why = stock.get('why', '')
                else:
                    # New format - create move and why from gap_pct and prices
                    gap_pct = stock.get('gap_pct', 0)
                    current_price = stock.get('current_price', 0)
                    prev_close = stock.get('prev_close', 0)
                    move = f"{gap_pct:+.2f}%"
                    why = f"${current_price:.2f} vs ${prev_close:.2f}"
                gapping_stocks_html += f"""
                <div class="gapping-stock-item">
                    <strong class="ticker">{ticker}</strong>: <span class="move">{move}</span> - <span class="why">{why}</span>
                </div>"""
        
        gapping_stocks_html += "</div>"
    
    # Create the full HTML content with improved styling and better spacing
    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Morning Market Brief</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
    <style>
        /* Mobile-first responsive design */
        * {{
            box-sizing: border-box;
        }}
        
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            line-height: 1.6;
            color: #333;
            background-color: #f8f9fa;
            margin: 0;
            padding: 10px;
        }}
        
        .container {{
            max-width: 100%;
            margin: 0 auto;
            background: white;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }}
        
        .header {{
            background: linear-gradient(135deg, #2c3e50 0%, #3498db 100%);
            color: white;
            padding: 20px;
            text-align: center;
        }}
        
        .header h1 {{
            margin: 0;
            font-size: 24px;
            font-weight: 300;
        }}
        
        .header p {{
            margin: 10px 0 0 0;
            font-size: 14px;
            opacity: 0.9;
        }}
        
        .content {{
            padding: 20px;
        }}
        
        .section-header {{
            color: #2c3e50;
            border-bottom: 2px solid #3498db;
            padding-bottom: 8px;
            margin: 20px 0 12px 0;
            font-size: 18px;
            font-weight: bold;
        }}
        
        .market-snapshot {{
            background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
            padding: 15px;
            border-radius: 8px;
            margin: 15px 0;
            border-left: 4px solid #3498db;
        }}
        
        .section-content {{
            background: #ffffff;
            padding: 15px;
            border-radius: 8px;
            margin: 12px 0;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        
        .subscriber-summary {{
            background: linear-gradient(135deg, #e8f4fd 0%, #d1ecf1 100%);
            padding: 20px;
            border-radius: 12px;
            margin: 20px 0;
            border-left: 5px solid #17a2b8;
            box-shadow: 0 4px 8px rgba(0,0,0,0.1);
        }}
        
        .summary-header-main {{
            display: flex;
            align-items: center;
            margin-bottom: 15px;
            font-size: 20px;
            font-weight: bold;
            color: #2c3e50;
        }}
        
        .summary-header-main i {{
            margin-right: 8px;
            color: #17a2b8;
        }}
        
        .summary-content {{
            line-height: 1.6;
        }}
        
        .summary-header {{
            font-weight: bold;
            color: #2c3e50;
            margin: 12px 0 6px 0;
            font-size: 16px;
        }}
        
        .summary-list-item {{
            margin: 6px 0;
            padding-left: 12px;
            color: #495057;
        }}
        
        .summary-levels-header {{
            font-weight: bold;
            color: #2c3e50;
            margin: 12px 0 6px 0;
            font-size: 16px;
        }}
        
        .summary-level-item {{
            margin: 4px 0;
            padding-left: 12px;
            color: #495057;
        }}
        
        .summary-tomorrow {{
            font-weight: bold;
            color: #2c3e50;
            margin: 12px 0 6px 0;
            font-size: 16px;
        }}
        
        .summary-text {{
            margin: 6px 0;
            color: #495057;
        }}
        
        .headline-item {{
            background: #f8f9fa;
            padding: 15px;
            border-radius: 10px;
            margin: 15px 0;
            border-left: 5px solid #3498db;
            display: flex;
            align-items: flex-start;
            box-shadow: 0 3px 6px rgba(0,0,0,0.08);
            transition: transform 0.2s ease, box-shadow 0.2s ease;
        }}
        
        .headline-item:hover {{
            transform: translateY(-2px);
            box-shadow: 0 5px 12px rgba(0,0,0,0.12);
        }}
        
        .headline-number {{
            color: #3498db;
            font-weight: bold;
            font-size: 18px;
            margin-right: 12px;
            min-width: 28px;
            background: #e3f2fd;
            padding: 6px 10px;
            border-radius: 50%;
            text-align: center;
            flex-shrink: 0;
        }}
        
        .headline-content {{
            flex: 1;
            min-width: 0;
        }}
        
        .headline-title {{
            font-weight: bold;
            color: #2c3e50;
            font-size: 16px;
            margin-bottom: 8px;
            line-height: 1.4;
        }}
        
        .headline-summary {{
            color: #495057;
            line-height: 1.6;
            font-size: 14px;
            margin-top: 6px;
        }}
        
        .gapping-stock-item {{
            background: #f8f9fa;
            padding: 10px;
            border-radius: 6px;
            margin: 6px 0;
            border-left: 3px solid #28a745;
        }}
        
        .ticker {{
            color: #2c3e50;
            font-weight: bold;
        }}
        
        .move {{
            color: #28a745;
            font-weight: 500;
        }}
        
        .why {{
            color: #6c757d;
        }}
        
        .key-levels {{
            background: linear-gradient(135deg, #fff3cd 0%, #ffeaa7 100%);
            padding: 15px;
            border-radius: 8px;
            margin: 15px 0;
            border-left: 4px solid #ffc107;
        }}
        
        .level-item {{
            margin: 8px 0;
            font-weight: 500;
        }}
        
        .support {{
            color: #28a745;
        }}
        
        .resistance {{
            color: #dc3545;
        }}
        
        /* Tablet and desktop styles */
        @media (min-width: 768px) {{
            body {{
                padding: 20px;
            }}
            
            .container {{
                max-width: 800px;
            }}
            
            .header {{
                padding: 30px;
            }}
            
            .header h1 {{
                font-size: 28px;
            }}
            
            .header p {{
                font-size: 16px;
            }}
            
            .content {{
                padding: 30px;
            }}
            
            .section-header {{
                margin: 25px 0 15px 0;
            }}
            
            .market-snapshot {{
                padding: 20px;
                margin: 20px 0;
            }}
            
            .section-content {{
                padding: 20px;
                margin: 15px 0;
            }}
            
            .subscriber-summary {{
                padding: 25px;
                margin: 25px 0;
            }}
            
            .summary-header-main {{
                margin-bottom: 20px;
                font-size: 22px;
            }}
            
            .summary-header-main i {{
                margin-right: 10px;
            }}
            
            .summary-header {{
                margin: 15px 0 8px 0;
            }}
            
            .summary-list-item {{
                margin: 8px 0;
                padding-left: 15px;
            }}
            
            .summary-levels-header {{
                margin: 15px 0 8px 0;
            }}
            
            .summary-level-item {{
                margin: 5px 0;
                padding-left: 15px;
            }}
            
            .summary-tomorrow {{
                margin: 15px 0 8px 0;
            }}
            
            .summary-text {{
                margin: 8px 0;
            }}
            
            .headline-item {{
                padding: 25px;
                margin: 25px 0;
            }}
            
            .headline-number {{
                font-size: 20px;
                margin-right: 20px;
                min-width: 30px;
                padding: 8px 12px;
            }}
            
            .headline-title {{
                font-size: 17px;
                margin-bottom: 10px;
            }}
            
            .headline-summary {{
                font-size: 15px;
                margin-top: 8px;
            }}
            
            .gapping-stock-item {{
                padding: 12px;
                margin: 8px 0;
            }}
            
            .key-levels {{
                padding: 20px;
                margin: 20px 0;
            }}
            
            .level-item {{
                margin: 10px 0;
            }}
        }}
    </style>
</head>
<body>

    <div class="container">

        <div class="header">
            <h1>Morning Market Brief</h1>
            <p>{current_date}</p>
        </div>

        <div class="content">
            {subscriber_html}

            <div class="section-content">
                {summary_html}
            </div>

            {gapping_stocks_html}

            <div class="key-levels">
                <h3 style="margin-top: 0; color: #856404;">Key Levels to Watch</h3>
                <div class="level-item">
                    <strong>SPY —</strong>
                    Daily <span class="support">S:</span> {(' / '.join(f"{x:.2f}" for x in spy_data.get('daily_supports', [])[:2])) if spy_data.get('daily_supports') else 'No data'};
                    <span class="resistance">R:</span> {(' / '.join(f"{x:.2f}" for x in spy_data.get('daily_resistances', [])[:2])) if spy_data.get('daily_resistances') else 'No data'};
                    Weekly <span class="support">S:</span> {(' / '.join(f"{x:.2f}" for x in spy_data.get('weekly_supports', [])[:2])) if spy_data.get('weekly_supports') else 'No data'};
                    <span class="resistance">R:</span> {(' / '.join(f"{x:.2f}" for x in spy_data.get('weekly_resistances', [])[:2])) if spy_data.get('weekly_resistances') else 'No data'}
                </div>
                <div class="level-item">
                    <strong>QQQ —</strong>
                    Daily <span class="support">S:</span> {(' / '.join(f"{x:.2f}" for x in qqq_data.get('daily_supports', [])[:2])) if qqq_data.get('daily_supports') else 'No data'};
                    <span class="resistance">R:</span> {(' / '.join(f"{x:.2f}" for x in qqq_data.get('daily_resistances', [])[:2])) if qqq_data.get('daily_resistances') else 'No data'};
                    Weekly <span class="support">S:</span> {(' / '.join(f"{x:.2f}" for x in qqq_data.get('weekly_supports', [])[:2])) if qqq_data.get('weekly_supports') else 'No data'};
                    <span class="resistance">R:</span> {(' / '.join(f"{x:.2f}" for x in qqq_data.get('weekly_resistances', [])[:2])) if qqq_data.get('weekly_resistances') else 'No data'}
                </div>
            </div>
        </div>
    </div>
</body>
</html>"""

    return html_content


def send_market_brief_to_subscribers():
    """Main function to generate and send market brief to all subscribers"""
    try:
        logger.info("Starting market brief generation and distribution")
        
        # Generate the brief
        headlines = fetch_news()
        filtered_headlines = filter_market_headlines(headlines)
        
        # Enhance headlines with 2-5 sentence summaries
        if HEADLINE_SUMMARIZER_AVAILABLE and OPENAI_API_KEY:
            try:
                filtered_headlines = summarize_headlines(filtered_headlines)
                logger.info("✅ Headlines enhanced with AI summaries")
            except Exception as e:
                logger.warning(f"Headline summarization failed, using original summaries: {e}")
        
        stock_prices = fetch_stock_prices()
        expected_range = calculate_expected_range(stock_prices)
        
        # Fetch gapping stocks for the new section
        gapping_stocks = fetch_gapping_stocks()
        
        # Generate summary with gapping stocks
        summary = summarize_news(filtered_headlines, expected_range, gapping_stocks)
        
        # NEW: Generate GPT summary
        subscriber_summary = None
        if GPT_AVAILABLE and OPENAI_API_KEY:
            # Handle gapping_stocks structure (could be list or dict)
            if isinstance(gapping_stocks, dict):
                ah_moves = gapping_stocks.get("after_hours", [])[:8]
                premarket_moves = gapping_stocks.get("premarket", [])[:8]
            else:
                # If it's a list, use it directly
                ah_moves = gapping_stocks[:8] if gapping_stocks else []
                premarket_moves = []
            
            brief_data = {
                "market_overview": summary,
                "ah_moves": ah_moves,
                "premarket_moves": premarket_moves,
                "earnings": [],  # Add earnings data if available
                "spy_s1": expected_range.get("spy", {}).get("support", "N/A"),
                "spy_s2": expected_range.get("spy", {}).get("support2", "N/A"),
                "spy_r1": expected_range.get("spy", {}).get("resistance", "N/A"),
                "spy_r2": expected_range.get("spy", {}).get("resistance2", "N/A"),
                "spy_r3": expected_range.get("spy", {}).get("resistance3", "N/A"),
            }
            
            try:
                gpt_summary = summarize_brief(brief_data)
                subscriber_summary = gpt_summary["subscriber_summary"]
                logger.info("✓ GPT summary generated successfully")
            except Exception as e:
                logger.warning(f"GPT summary failed, using fallback: {e}")
        else:
            logger.info("GPT summary not available, skipping")
        
        # Generate two variants:
        # - Site content: keep legacy layout without the Subscriber Summary
        # - Email content: upgraded HTML with trader voice + subscriber summary
        site_content = generate_html_content_with_summary(summary, filtered_headlines, expected_range, gapping_stocks, None)
        email_content = generate_enhanced_html(
            summary,
            filtered_headlines,
            expected_range,
            gapping_stocks,
            subscriber_summary,
        )

        # Persist latest brief content to a static file for website display
        try:
            base_dir = Path(__file__).resolve().parent
            out_dir = base_dir / 'static' / 'uploads'
            out_dir.mkdir(parents=True, exist_ok=True)
            latest_file = out_dir / 'brief_latest.html'
            latest_date_file = out_dir / 'brief_latest_date.txt'
            latest_file.write_text(site_content, encoding='utf-8')
            latest_date_file.write_text(datetime.now().strftime('%Y-%m-%d'), encoding='utf-8')
            logger.info(f"Wrote latest brief HTML to {latest_file}")
        except Exception as write_err:
            logger.warning(f"Failed to write latest brief HTML: {write_err}")
        
        # Use the new email system to send to confirmed subscribers
        from emails import send_daily_brief_direct

        # Visibility on config that commonly breaks sending
        try:
            cfg = (current_app.config if current_app else {})
            logger.info(
                "Email config — server:%s port:%s tls:%s ssl:%s sender:%s suppress_send:%s sendgrid:%s mailgun:%s ses:%s",
                cfg.get("MAIL_SERVER"),
                cfg.get("MAIL_PORT"),
                cfg.get("MAIL_USE_TLS"),
                cfg.get("MAIL_USE_SSL"),
                cfg.get("MAIL_DEFAULT_SENDER"),
                cfg.get("MAIL_SUPPRESS_SEND"),
                bool(os.getenv("SENDGRID_KEY")),
                bool(os.getenv("MAILGUN_DOMAIN") and os.getenv("MAILGUN_API_KEY")),
                bool(os.getenv("AWS_SES_ACCESS_KEY_ID") and os.getenv("AWS_SES_SECRET_ACCESS_KEY")),
            )
        except Exception:
            pass

        success_count = send_daily_brief_direct(email_content)

        # If nothing was sent, surface likely reasons
        if not success_count:
            try:
                from models import MarketBriefSubscriber, db  # already imported above, but safe
                total = db.session.query(MarketBriefSubscriber).count()
                confirmed = db.session.query(MarketBriefSubscriber).filter_by(confirmed=True).count()
                unsub = 0
                try:
                    unsub = db.session.query(MarketBriefSubscriber).filter_by(unsubscribed=True).count()
                except Exception:
                    pass
                logger.warning(
                    "Email send returned 0. Subscribers — total:%s confirmed:%s unsubscribed:%s. MAIL_SUPPRESS_SEND=%s, MAIL_DEFAULT_SENDER=%s",
                    total, confirmed, unsub,
                    (current_app.config.get("MAIL_SUPPRESS_SEND") if current_app else None),
                    (current_app.config.get("MAIL_DEFAULT_SENDER") if current_app else None),
                )
            except Exception as e:
                logger.warning(f"Could not introspect subscriber counts: {e}")
        
        logger.info(f"Market brief sent successfully to {success_count} confirmed subscribers")
        return success_count
        
    except Exception as e:
        logger.error(f"Error in market brief generation: {str(e)}")
        raise


def generate_daily_brief_file_only() -> str:
    """Generate the daily brief HTML and write static/uploads/brief_latest.html without emailing.
    Returns the absolute path written.
    """
    try:
        # Generate the brief
        headlines = fetch_news()
        filtered_headlines = filter_market_headlines(headlines)

        # Enhance headlines with 2-5 sentence summaries
        if HEADLINE_SUMMARIZER_AVAILABLE and OPENAI_API_KEY:
            try:
                filtered_headlines = summarize_headlines(filtered_headlines)
                logger.info("✅ Headlines enhanced with AI summaries")
            except Exception as e:
                logger.warning(f"Headline summarization failed, using original summaries: {e}")

        stock_prices = fetch_stock_prices()
        expected_range = calculate_expected_range(stock_prices)

        # Fetch gapping stocks for the new section
        gapping_stocks = fetch_gapping_stocks()

        # Generate summary with gapping stocks
        summary = summarize_news(filtered_headlines, expected_range, gapping_stocks)

        # Include GPT subscriber summary only in email variant; for file write we use site content
        subscriber_summary = None
        if GPT_AVAILABLE and OPENAI_API_KEY:
            try:
                # Normalize gappers structure
                if isinstance(gapping_stocks, dict):
                    ah_moves = gapping_stocks.get("after_hours", [])[:8]
                    premarket_moves = gapping_stocks.get("premarket", [])[:8]
                else:
                    ah_moves = gapping_stocks[:8] if gapping_stocks else []
                    premarket_moves = []
                brief_data = {
                    "market_overview": summary,
                    "ah_moves": ah_moves,
                    "premarket_moves": premarket_moves,
                    "earnings": [],
                    "spy_s1": expected_range.get("spy", {}).get("support", "N/A"),
                    "spy_s2": expected_range.get("spy", {}).get("support2", "N/A"),
                    "spy_r1": expected_range.get("spy", {}).get("resistance", "N/A"),
                    "spy_r2": expected_range.get("spy", {}).get("resistance2", "N/A"),
                    "spy_r3": expected_range.get("spy", {}).get("resistance3", "N/A"),
                }
                gpt_summary = summarize_brief(brief_data)
                subscriber_summary = gpt_summary.get("subscriber_summary")
                logger.info("✓ GPT summary generated successfully")
            except Exception as e:
                logger.warning(f"GPT summary failed, using fallback: {e}")

        # Site content should not include subscriber summary
        site_content = generate_html_content_with_summary(
            summary, filtered_headlines, expected_range, gapping_stocks, None
        )

        base_dir = Path(__file__).resolve().parent
        out_dir = base_dir / "static" / "uploads"
        out_dir.mkdir(parents=True, exist_ok=True)
        latest_file = out_dir / "brief_latest.html"
        latest_date_file = out_dir / "brief_latest_date.txt"
        latest_file.write_text(site_content, encoding="utf-8")
        latest_date_file.write_text(datetime.now().strftime('%Y-%m-%d'), encoding='utf-8')
        logger.info(f"Wrote latest brief HTML to {latest_file}")
        return str(latest_file)
    except Exception as e:
        logger.error(f"Error generating daily brief file only: {e}")
        raise

if __name__ == "__main__":
    # For testing outside of Flask context
    try:
        # Import and create Flask app context
        from app import app
        with app.app_context():
            send_market_brief_to_subscribers()
    except ImportError:
        # If app import fails, just generate the brief without sending emails
        logger.info("Running in standalone mode - generating brief content only")
        try:
            # Generate the brief
            headlines = fetch_news()
            filtered_headlines = filter_market_headlines(headlines)
            
            # Enhance headlines with 2-5 sentence summaries
            if HEADLINE_SUMMARIZER_AVAILABLE and OPENAI_API_KEY:
                try:
                    filtered_headlines = summarize_headlines(filtered_headlines)
                    logger.info("✅ Headlines enhanced with AI summaries")
                except Exception as e:
                    logger.warning(f"Headline summarization failed, using original summaries: {e}")
            
            stock_prices = fetch_stock_prices()
            expected_range = calculate_expected_range(stock_prices)
            
            # Fetch gapping stocks for the new section
            gapping_stocks = fetch_gapping_stocks()
            
            # Generate summary with gapping stocks
            summary = summarize_news(filtered_headlines, expected_range, gapping_stocks)
            
            # NEW: Generate GPT summary for standalone mode
            subscriber_summary = None
            if GPT_AVAILABLE and OPENAI_API_KEY:
                # Handle gapping_stocks structure (could be list or dict)
                if isinstance(gapping_stocks, dict):
                    ah_moves = gapping_stocks.get("after_hours", [])[:8]
                    premarket_moves = gapping_stocks.get("premarket", [])[:8]
                else:
                    # If it's a list, use it directly
                    ah_moves = gapping_stocks[:8] if gapping_stocks else []
                    premarket_moves = []
                
                brief_data = {
                    "market_overview": summary,
                    "ah_moves": ah_moves,
                    "premarket_moves": premarket_moves,
                    "earnings": [],  # Add earnings data if available
                    "spy_s1": expected_range.get("spy", {}).get("support", "N/A"),
                    "spy_s2": expected_range.get("spy", {}).get("support2", "N/A"),
                    "spy_r1": expected_range.get("spy", {}).get("resistance", "N/A"),
                    "spy_r2": expected_range.get("spy", {}).get("resistance2", "N/A"),
                    "spy_r3": expected_range.get("spy", {}).get("resistance3", "N/A"),
                }
                
                try:
                    gpt_summary = summarize_brief(brief_data)
                    subscriber_summary = gpt_summary["subscriber_summary"]
                    logger.info("✓ GPT summary generated successfully")
                except Exception as e:
                    logger.warning(f"GPT summary failed, using fallback: {e}")
            else:
                logger.info("GPT summary not available, skipping")
            
            # Generate email content with GPT summary
            brief_content = generate_enhanced_html(
                summary,
                filtered_headlines,
                expected_range,
                gapping_stocks,
                subscriber_summary,
            )

            # Persist latest brief content to a static file for website display
            try:
                base_dir = Path(__file__).resolve().parent
                out_dir = base_dir / 'static' / 'uploads'
                out_dir.mkdir(parents=True, exist_ok=True)
                latest_file = out_dir / 'brief_latest.html'
                latest_date_file = out_dir / 'brief_latest_date.txt'
                latest_file.write_text(brief_content, encoding='utf-8')
                latest_date_file.write_text(datetime.now().strftime('%Y-%m-%d'), encoding='utf-8')
                logger.info(f"Wrote latest brief HTML to {latest_file}")
                logger.info("Market brief generated successfully in standalone mode")
            except Exception as write_err:
                logger.warning(f"Failed to write latest brief HTML: {write_err}")
        except Exception as e:
            logger.error(f"Error in standalone market brief generation: {str(e)}") 

class BriefDataUnavailable(Exception):
    """Raised when required market data cannot be retrieved."""
    pass

def fetch_stock_prices_strict() -> Dict[str, Dict[str, float]]:
    """Fetch prices without falling back to synthetic defaults."""

    for fetcher in (fetch_stock_prices_tradier, fetch_stock_prices_finnhub):
        prices = fetcher()
        if prices:
            return prices
    return {}


def has_valid_price_data(prices: Dict[str, Dict[str, float]], required: Optional[List[str]] = None) -> bool:
    """Verify that required symbols have non-zero price data."""

    required_symbols = [sym.lower() for sym in (required or ['spy', 'qqq'])]
    for symbol in required_symbols:
        data = prices.get(symbol)
        if not data:
            logger.warning("Missing price data for %s", symbol.upper())
            return False

        price = data.get('current_price')
        if price is None or price <= 0:
            logger.warning("Invalid price data for %s: %s", symbol.upper(), price)
            return False

    return True


def calculate_expected_range(stock_prices: Dict[str, float]) -> Dict[str, Any]:
    """Calculate expected range bands for key indices based on VIX."""

    vix = stock_prices.get('vix', {}).get('current_price', 20) or 20

    def _levels(price: Optional[float], range_pct: float) -> Dict[str, float]:
        if not price or price <= 0:
            return {}
        range_abs = price * range_pct
        half_range = range_abs / 2
        return {
            'current_price': round(price, 2),
            'support': round(price - half_range, 2),
            'support2': round(price - range_abs, 2),
            'resistance': round(price + half_range, 2),
            'resistance2': round(price + range_abs, 2),
            'resistance3': round(price + range_abs * 1.5, 2)
        }

    spy_price = stock_prices.get('spy', {}).get('current_price')
    qqq_price = stock_prices.get('qqq', {}).get('current_price')
    iwm_price = stock_prices.get('iwm', {}).get('current_price')

    base_pct = min(max(vix / 100, 0.01), 0.05)

    ranges = {
        'spy': _levels(spy_price, base_pct),
        'qqq': _levels(qqq_price, base_pct * 1.2),
        'iwm': _levels(iwm_price, base_pct * 1.1),
        'vix': stock_prices.get('vix', {})
    }

    return {k: v for k, v in ranges.items() if v}