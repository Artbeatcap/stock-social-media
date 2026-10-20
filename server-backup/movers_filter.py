#!/usr/bin/env python3
"""
Notable-movers quality filter.

Do not rank Polygon's raw gainers/losers list. 2X single-stock products
move twice the underlying by construction and will always dominate a
percentage ranking; new ones list weekly, so a ticker blocklist goes stale
immediately.

Pipeline: liquidity-cut the session universe → Polygon `type` (CS/ADRC)
with issuer/name regex backstop → split/halt guard → score by
abs(move) * log10(dollar volume) → cap direction → news "why".
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from ticker_meta import TickerMeta, get_ticker_meta, ticker_had_recent_split

logger = logging.getLogger("movers_filter")

ALLOWED_SECURITY_TYPES = frozenset({"CS", "ADRC"})
ALLOWED_EXCHANGES = frozenset({"XNYS", "XNAS", "ARCX", "BATS", "XASE"})

# Explicit leverage markers only. Bare TRUST/FUND/INDEX tokens are already
# covered by the Polygon `type` code and produced silent false negatives on
# real operating companies: Digital Realty Trust (DLR), Northern Trust (NTRS),
# Vornado Realty Trust (VNO).
FUND_NAME_PATTERN = re.compile(
    r"\b(\d+(\.\d+)?X|ULTRA(SHORT|PRO)?|LEVERAGED|INVERSE|"
    r"DAILY\s+TARGET|SINGLE[- ]STOCK|ETF|ETN|EXCHANGE[- ]TRADED|"
    r"(BULL|BEAR)\s*\d*X?)\b",
    re.I,
)
FUND_ISSUER_PATTERN = re.compile(
    r"\b(DEFIANCE|DIREXION|PROSHARES|GRANITESHARES|LEVERAGE\s+SHARES|"
    r"TRADR|T-REX|REX\s+SHARES|VOLATILITY\s+SHARES|YIELDMAX|KURV|"
    r"INNOVATOR|SIMPLIFY|BATTERY\s+SHARES)\b",
    re.I,
)

FLOW_DRIVEN_WHY = "No headline — flow-driven"
META_LOOKUP_CAP = 300
CATALYST_SLOTS = 2


@dataclass(frozen=True)
class MoverQualityConfig:
    min_price: float = 3.0
    min_avg_volume: int = 500_000
    min_dollar_volume: float = 10_000_000
    min_market_cap: float = 300_000_000
    min_abs_change_pct: float = 4.0
    max_abs_change_pct: float = 60.0


DEFAULT_MOVER_CONFIG = MoverQualityConfig()
RELAXED_CAP_CONFIG = MoverQualityConfig(min_market_cap=100_000_000)


def is_real_operating_company(meta: TickerMeta) -> bool:
    if not meta.active:
        return False
    if (meta.market or "").lower() != "stocks":
        return False
    if meta.type not in ALLOWED_SECURITY_TYPES:
        return False
    if meta.primary_exchange and meta.primary_exchange not in ALLOWED_EXCHANGES:
        return False
    if FUND_NAME_PATTERN.search(meta.name or ""):
        return False
    if FUND_ISSUER_PATTERN.search(meta.name or ""):
        return False
    return True


def _price(snap: Dict[str, Any]) -> float:
    day = snap.get("day") or {}
    last = snap.get("lastTrade") or {}
    for val in (day.get("c"), last.get("p"), snap.get("price"), snap.get("last")):
        try:
            if val is not None:
                return float(val)
        except (TypeError, ValueError):
            continue
    return 0.0


def _volume(snap: Dict[str, Any]) -> float:
    day = snap.get("day") or {}
    for val in (day.get("v"), snap.get("volume")):
        try:
            if val is not None:
                return float(val)
        except (TypeError, ValueError):
            continue
    return 0.0


def _prev_volume(snap: Dict[str, Any]) -> float:
    prev = snap.get("prevDay") or {}
    for val in (prev.get("v"), snap.get("prev_volume")):
        try:
            if val is not None:
                return float(val)
        except (TypeError, ValueError):
            continue
    return 0.0


def _change_pct(snap: Dict[str, Any]) -> float:
    for key in ("todaysChangePerc", "pct", "change_percent"):
        try:
            if snap.get(key) is not None:
                return float(snap[key])
        except (TypeError, ValueError):
            continue
    return 0.0


def passes_quality_gate(
    snap: Dict[str, Any],
    meta: TickerMeta,
    cfg: MoverQualityConfig = DEFAULT_MOVER_CONFIG,
) -> bool:
    price = _price(snap)
    volume = _volume(snap)
    prev_volume = _prev_volume(snap)
    abs_change = abs(_change_pct(snap))

    if price < cfg.min_price:
        return False
    if abs_change < cfg.min_abs_change_pct:
        return False
    if abs_change > cfg.max_abs_change_pct:
        return False
    if price * volume < cfg.min_dollar_volume:
        return False
    if max(volume, prev_volume) < cfg.min_avg_volume:
        return False
    if meta.market_cap is not None and meta.market_cap < cfg.min_market_cap:
        return False
    return True


def score_mover(snap: Dict[str, Any]) -> float:
    """
    Liquidity-weighted rank. Raw `abs(pct) * log10(dvol)` still lets a 30%
    print on $12M beat a 9% print on $2B, so dollar volume is scaled first.

    The divisor is NOT the $10M eligibility gate. Dividing by the gate puts the
    weakest survivors in the flat band just above log10(1), where their ordering
    is noise. Dividing below it (see SESSION_SCORE_DIVISOR) keeps every survivor
    on a meaningful part of the curve.

    Clamp at 1.01, not 1.0: anything at or under the divisor would otherwise
    collapse to exactly 0 and sort arbitrarily.
    """
    from premarket_movers import SESSION_SCORE_DIVISOR

    abs_change = abs(_change_pct(snap))
    dollar_volume = _price(snap) * _volume(snap)
    return abs_change * math.log10(max(dollar_volume / SESSION_SCORE_DIVISOR, 1.01))


_LEGAL_SUFFIX = re.compile(
    r"\b(?:incorporated|inc|corp|corporation|ltd|limited|plc|"
    r"n\.v|s\.a|company|co|class\s+[a-z]|ordinary\s+shares|"
    r"common\s+stock|american\s+depositary\s+shares|depositary\s+shares)\b\.?",
    re.I,
)
_OPINION_HEADLINE = (
    re.compile(r"^\s*prediction\s*:", re.I),
    re.compile(r"\bis\b.+\b(?:stock\s+)?(?:still\s+)?a(?:\s+\w+){0,2}\s+buy\b", re.I),
    re.compile(r"here'?s why i'?m", re.I),
    re.compile(r"should you buy", re.I),
    re.compile(r"\bstocks to buy\b", re.I),
)


def company_base_name(name: str) -> str:
    """Drop legal suffixes so 'Class A Ordinary Shares' does not wrap as the name."""
    text = name or ""
    previous = None
    while previous != text:
        previous = text
        text = _LEGAL_SUFFIX.sub(" ", text)
        text = re.sub(r"[,.\s]+", " ", text).strip(" ,.-")
    return re.sub(r"\s+", " ", text).strip()


def is_opinion_headline(title: str) -> bool:
    return any(pattern.search(title or "") for pattern in _OPINION_HEADLINE)


def title_names_subject(title: str, ticker: str, company_name: str) -> bool:
    """A headline explains the move only when it names this ticker or company."""
    text = title or ""
    symbol = (ticker or "").strip()
    if symbol and re.search(rf"(?<![A-Za-z]){re.escape(symbol)}(?![A-Za-z])", text, re.I):
        return True
    base = company_base_name(company_name)
    if len(base) >= 2 and re.search(rf"(?<![A-Za-z]){re.escape(base)}(?![A-Za-z])", text, re.I):
        return True
    return False


def pick_explaining_headline(
    titles: List[str],
    ticker: str,
    company_name: str,
) -> Optional[str]:
    """Best headline that names the company. Opinion pieces rank below facts."""
    best: Optional[str] = None
    best_score = -1
    for title in titles:
        cleaned = (title or "").strip()
        if not cleaned or not title_names_subject(cleaned, ticker, company_name):
            continue
        score = 1 if is_opinion_headline(cleaned) else 2
        if score > best_score:
            best = cleaned
            best_score = score
    return best


def _truncate_headline(text: str, limit: int = 90) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return (cut or text[:limit]).rstrip(".,;:") + "…"


def _fetch_news_articles(
    ticker: str,
    limit: int = 10,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    from datetime import timezone as tz
    from market_internals import PolygonError, _polygon_get

    if since is None:
        since = datetime.now(tz.utc) - timedelta(hours=36)
    params = {
        "ticker": ticker,
        "published_utc.gte": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "order": "desc",
        "sort": "published_utc",
        "limit": limit,
    }
    if until is not None:
        params["published_utc.lte"] = until.strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        data = _polygon_get(
            "/v2/reference/news",
            params=params,
        )
    except PolygonError as exc:
        logger.info("mover news lookup failed for %s: %s", ticker, exc)
        return []
    return list(data.get("results") or [])


def _explaining_news(
    ticker: str,
    company_name: str,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Headline that names this company. A tagged-but-unrelated story does not count."""
    from premarket_movers import classify_news

    empty = {"why": None, "url": None, "is_earnings": False, "sentiment": None, "headlines": []}
    articles = _fetch_news_articles(ticker, since=since, until=until)
    titles = [
        (article.get("title") or article.get("headline") or "").strip()
        for article in articles
    ]
    # Opinion columns can name the company and still not explain the move.
    factual_titles = [title for title in titles if title and not is_opinion_headline(title)]
    chosen = pick_explaining_headline(factual_titles, ticker, company_name)
    if not chosen:
        return empty
    match = next(
        (
            article
            for article in articles
            if (article.get("title") or article.get("headline") or "").strip() == chosen
        ),
        {},
    )
    url = match.get("article_url") or match.get("url")
    tags = classify_news(match, ticker) if match else {"is_earnings": False, "sentiment": None}
    qualifying = [
        title
        for title in titles
        if title and title_names_subject(title, ticker, company_name)
    ]
    factual = [title for title in qualifying if not is_opinion_headline(title)]
    return {
        "why": _truncate_headline(chosen),
        "url": str(url) if url else None,
        "is_earnings": tags["is_earnings"],
        "sentiment": tags["sentiment"],
        "headlines": factual or qualifying,
    }


def _enrich_reason_full(ticker: str, *, strict: bool = True) -> Dict[str, Any]:
    """
    Headline, url, and the earnings/sentiment tags already in the payload.

    `strict=False` accepts an article that merely mentions the ticker rather
    than leading with it. Used for index drivers, where a row that says the
    index moved but refuses to say why is the worst row in the email.
    """
    from datetime import timezone as tz
    from market_internals import PolygonError, _polygon_get
    from premarket_movers import classify_news

    empty = {"why": None, "url": None, "is_earnings": False, "sentiment": None}
    since = (datetime.now(tz.utc) - timedelta(hours=36)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        data = _polygon_get(
            "/v2/reference/news",
            params={
                "ticker": ticker,
                "published_utc.gte": since,
                "order": "desc",
                "sort": "published_utc",
                "limit": 3,
            },
        )
    except PolygonError as exc:
        logger.info("mover news lookup failed for %s: %s", ticker, exc)
        return empty

    for article in data.get("results") or []:
        tickers = [str(t).upper() for t in (article.get("tickers") or [])]
        relevant = tickers[:2] if strict else tickers
        if ticker not in relevant:
            continue
        title = (article.get("title") or article.get("headline") or "").strip()
        if not title:
            continue
        url = article.get("article_url") or article.get("url")
        tags = classify_news(article, ticker)
        return {
            "why": _truncate_headline(title),
            "url": str(url) if url else None,
            "is_earnings": tags["is_earnings"],
            "sentiment": tags["sentiment"],
        }
    return empty


def _normalize_enrichment(result: Any) -> Dict[str, Any]:
    """Accept either the full dict or the legacy (why, url) tuple."""
    if isinstance(result, dict):
        return {
            "why": result.get("why"),
            "url": result.get("url"),
            "is_earnings": bool(result.get("is_earnings")),
            "sentiment": result.get("sentiment"),
        }
    why, url = (result or (None, None))
    return {"why": why, "url": url, "is_earnings": False, "sentiment": None}


def _enrich_reason(ticker: str) -> Tuple[Optional[str], Optional[str]]:
    from datetime import timezone as tz
    from market_internals import PolygonError, _polygon_get

    since = (datetime.now(tz.utc) - timedelta(hours=36)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        data = _polygon_get(
            "/v2/reference/news",
            params={
                "ticker": ticker,
                "published_utc.gte": since,
                "order": "desc",
                "sort": "published_utc",
                "limit": 3,
            },
        )
    except PolygonError as exc:
        logger.info("mover news lookup failed for %s: %s", ticker, exc)
        return None, None

    for article in data.get("results") or []:
        tickers = [str(t).upper() for t in (article.get("tickers") or [])]
        if ticker not in tickers[:2]:
            continue
        title = (article.get("title") or article.get("headline") or "").strip()
        if not title:
            continue
        url = article.get("article_url") or article.get("url")
        return _truncate_headline(title), (str(url) if url else None)
    return None, None


def _bar_to_snap(ticker: str, today: Dict[str, Any], yest: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        close = float(today["c"])
        vol = float(today["v"])
        prev_close = float(yest["c"])
        prev_vol = float(yest.get("v") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    if prev_close <= 0:
        return None
    pct = ((close - prev_close) / prev_close) * 100
    return {
        "ticker": ticker,
        "todaysChangePerc": pct,
        "day": {"c": close, "v": vol},
        "prevDay": {"v": prev_vol},
        "lastTrade": {"p": close},
    }


def session_snapshots(
    today_map: Dict[str, Dict[str, Any]],
    yest_map: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    snaps: List[Dict[str, Any]] = []
    for ticker, today in today_map.items():
        if "." in ticker or len(ticker) > 5:
            continue
        yest = yest_map.get(ticker)
        if not yest:
            continue
        snap = _bar_to_snap(ticker, today, yest)
        if snap:
            snaps.append(snap)
    return snaps


def _filter_pool(
    snaps: List[Dict[str, Any]],
    meta_by_ticker: Dict[str, TickerMeta],
    cfg: MoverQualityConfig,
    split_since: str,
    split_check: Callable[[str, str], bool],
) -> List[Dict[str, Any]]:
    kept: List[Dict[str, Any]] = []
    for snap in snaps:
        ticker = str(snap.get("ticker") or "").upper()
        meta = meta_by_ticker.get(ticker)
        if meta is None:
            continue
        if not is_real_operating_company(meta):
            continue
        if not passes_quality_gate(snap, meta, cfg):
            continue
        if split_check(ticker, split_since):
            continue
        snap = dict(snap)
        snap["_meta"] = meta
        kept.append(snap)
    return kept


def select_slots(
    pool: List[Dict[str, Any]],
    limit: int = 6,
    max_same: int = 4,
    catalyst_slots: int = CATALYST_SLOTS,
) -> List[Dict[str, Any]]:
    """
    Ranking purely by dollar volume replaces "leveraged ETFs always win" with
    "mega-caps always win" — a 9% NVDA day buries a real single-stock catalyst
    every morning. Fill most slots by liquidity, then reserve `catalyst_slots`
    for the biggest raw moves that survived the same filters.
    """
    chosen: List[Dict[str, Any]] = []
    taken: set[str] = set()
    up = down = 0

    def _try_add(snap: Dict[str, Any]) -> None:
        nonlocal up, down
        ticker = snap["ticker"]
        if ticker in taken:
            return
        pct = _change_pct(snap)
        if pct >= 0:
            if up >= max_same:
                return
            up += 1
        else:
            if down >= max_same:
                return
            down += 1
        taken.add(ticker)
        chosen.append(snap)

    liquidity_cap = max(limit - catalyst_slots, 0)
    for snap in sorted(pool, key=score_mover, reverse=True):
        if len(chosen) >= liquidity_cap:
            break
        _try_add(snap)

    # Remaining slots go to the largest raw moves still unpicked; if there are
    # none left this simply backfills by move and the list stays short.
    for snap in sorted(pool, key=lambda s: abs(_change_pct(s)), reverse=True):
        if len(chosen) >= limit:
            break
        _try_add(snap)

    return chosen


def prior_session_label(now: Optional[datetime] = None) -> str:
    """"Yesterday" is wrong on a Monday send — name the actual prior session."""
    now = now or datetime.now()
    prior = now - timedelta(days=1)
    while prior.weekday() > 4:
        prior -= timedelta(days=1)
    if (now.date() - prior.date()).days <= 1:
        return "Yesterday"
    return prior.strftime("%A")


def _to_brief_row(
    snap: Dict[str, Any],
    reason: Optional[str],
    reason_url: Optional[str],
    *,
    window: str = "prior_session",
    prior_session_pct: Optional[float] = None,
    is_earnings: bool = False,
    window_label: Optional[str] = None,
) -> Dict[str, Any]:
    pct = _change_pct(snap)
    meta: TickerMeta = snap["_meta"]
    row = {
        "ticker": str(snap["ticker"]).upper(),
        "pct": f"{pct:+.2f}%",
        "dir": "down" if pct < 0 else "up",
        "why": reason or FLOW_DRIVEN_WHY,
        "companyName": meta.name,
        "reasonUrl": reason_url,
        "window": window,
        "isEarnings": bool(is_earnings),
        "windowLabel": window_label
        or ("Premarket" if window == "premarket" else prior_session_label()),
    }
    if prior_session_pct is not None:
        row["priorSessionPct"] = f"{prior_session_pct:+.2f}%"
    return row


def select_notable_movers(
    snaps: List[Dict[str, Any]],
    *,
    cfg: MoverQualityConfig = DEFAULT_MOVER_CONFIG,
    fetch_meta: Optional[Callable[[List[str]], Dict[str, TickerMeta]]] = None,
    split_check: Optional[Callable[[str, str], bool]] = None,
    enrich: Optional[Callable[[str], Tuple[Optional[str], Optional[str]]]] = None,
    now: Optional[datetime] = None,
    enrich_news: bool = True,
    window: str = "prior_session",
    window_label: Optional[str] = None,
    require_news: bool = False,
    news_since: Optional[datetime] = None,
    news_until: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """
    Return at most 6 brief-ready movers. Empty list → omit the section.
    Never pads with the next-best junk.

    require_news keeps walking the quality pool until the slots are names
    with a headline. A move with no article does not render.
    """
    fetch_meta = fetch_meta or get_ticker_meta
    split_check = split_check or ticker_had_recent_split
    use_explaining = require_news and enrich is None
    enrich = enrich or _enrich_reason_full
    now = now or datetime.now()
    split_since = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    coarse: List[Dict[str, Any]] = []
    for snap in snaps:
        ticker = str(snap.get("ticker") or "").upper()
        if not ticker:
            continue
        price = _price(snap)
        volume = _volume(snap)
        abs_change = abs(_change_pct(snap))
        if price < cfg.min_price:
            continue
        if abs_change < cfg.min_abs_change_pct or abs_change > cfg.max_abs_change_pct:
            continue
        if price * volume < cfg.min_dollar_volume:
            continue
        if max(volume, _prev_volume(snap)) < cfg.min_avg_volume:
            continue
        snap = dict(snap)
        snap["ticker"] = ticker
        coarse.append(snap)

    coarse.sort(key=score_mover, reverse=True)
    lookup_pool = coarse[:META_LOOKUP_CAP]
    candidates_n = len(snaps)
    after_liquidity = len(lookup_pool)

    meta_by_ticker = fetch_meta([s["ticker"] for s in lookup_pool])
    after_type = sum(
        1
        for s in lookup_pool
        if (m := meta_by_ticker.get(s["ticker"])) is not None and is_real_operating_company(m)
    )

    pool = _filter_pool(lookup_pool, meta_by_ticker, cfg, split_since, split_check)
    after_quality = len(pool)
    if len(pool) < 3:
        relaxed = _filter_pool(lookup_pool, meta_by_ticker, RELAXED_CAP_CONFIG, split_since, split_check)
        if len(relaxed) > len(pool):
            pool = relaxed
            after_quality = len(pool)

    chosen = select_slots(pool)
    if require_news:
        chosen_ids = {s["ticker"] for s in chosen}
        rest = sorted(
            (s for s in pool if s["ticker"] not in chosen_ids),
            key=score_mover,
            reverse=True,
        )
        scan = list(chosen) + rest[:30]
    else:
        scan = chosen

    rows: List[Dict[str, Any]] = []
    with_news = 0
    up = down = 0
    for snap in scan:
        if require_news and len(rows) >= 6:
            break
        news = {"why": None, "url": None, "is_earnings": False, "sentiment": None, "headlines": []}
        if enrich_news:
            if use_explaining:
                company = ""
                meta = snap.get("_meta")
                if meta is not None:
                    company = getattr(meta, "name", "") or ""
                news = _explaining_news(
                    snap["ticker"],
                    company,
                    since=news_since,
                    until=news_until,
                )
            else:
                news = _normalize_enrichment(enrich(snap["ticker"]))
        if require_news and not (news.get("why") or "").strip():
            continue
        if require_news:
            pct = _change_pct(snap)
            if pct >= 0:
                if up >= 4:
                    continue
                up += 1
            else:
                if down >= 4:
                    continue
                down += 1
        if news["why"]:
            with_news += 1
        row = _to_brief_row(
            snap,
            news["why"],
            news["url"],
            window=window,
            window_label=window_label,
            is_earnings=news["is_earnings"],
        )
        if news.get("headlines"):
            row["_headlines"] = list(news["headlines"])
        row["_price"] = round(_price(snap), 4)
        row["_dollarVolume"] = round(_price(snap) * _volume(snap), 2)
        rows.append(row)

    payload = {
        "event": "movers_filter",
        "candidates": candidates_n,
        "afterTypeFilter": after_type,
        "afterQualityGate": after_quality,
        "withNews": with_news,
        "rendered": len(rows),
    }
    if len(rows) < 3:
        logger.warning("%s", payload)
    else:
        logger.info("%s", payload)
    return rows


def select_premarket_movers(
    snaps: List[Dict[str, Any]],
    *,
    fetch_meta: Optional[Callable[[List[str]], Dict[str, TickerMeta]]] = None,
    fetch_adv: Optional[Callable[[List[str]], Dict[str, float]]] = None,
    enrich: Optional[Callable[[str], Any]] = None,
    now_ms: Optional[float] = None,
    enrich_news: bool = True,
    limit: int = 6,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Premarket-window movers from a full-market snapshot."""
    from premarket_movers import (
        PREMARKET,
        PREMARKET_CONFIG,
        is_stale,
        passes_premarket_gate,
        read_window_fields,
        relative_volume,
        score_in_window,
    )

    fetch_meta = fetch_meta or get_ticker_meta
    enrich = enrich or _enrich_reason_full
    now_ms = now_ms if now_ms is not None else datetime.now().timestamp() * 1000
    if fetch_adv is None:
        from adv_cache import get_avg_dollar_volume

        fetch_adv = get_avg_dollar_volume

    stale_rejected = 0
    coarse: List[Dict[str, Any]] = []
    for snap in snaps:
        ticker = str(snap.get("ticker") or "").upper()
        if not ticker or "." in ticker or len(ticker) > 5:
            continue
        fields = read_window_fields(snap, PREMARKET)
        abs_change = abs(fields["change_pct"])
        if abs_change < PREMARKET_CONFIG.min_abs_change_pct:
            continue
        if abs_change > PREMARKET_CONFIG.max_abs_change_pct:
            continue
        if fields["price"] < PREMARKET_CONFIG.min_price:
            continue
        if fields["price"] * fields["volume"] < PREMARKET_CONFIG.min_dollar_volume:
            continue
        if is_stale(fields["quote_ms"], now_ms, PREMARKET_CONFIG.max_quote_age_min):
            stale_rejected += 1
            continue
        snap = dict(snap)
        snap["ticker"] = ticker
        snap["_fields"] = fields
        coarse.append(snap)

    coarse.sort(
        key=lambda s: score_in_window(
            s["_fields"]["price"], s["_fields"]["volume"], s["_fields"]["change_pct"], PREMARKET
        ),
        reverse=True,
    )
    lookup_pool = coarse[:META_LOOKUP_CAP]
    meta_by_ticker = fetch_meta([s["ticker"] for s in lookup_pool])

    # Resolve the 20-day baseline only for names that already look like real
    # companies, so the relative-volume gate costs ~80 cached lookups, not 190.
    typed = [s for s in lookup_pool if (m := meta_by_ticker.get(s["ticker"])) and is_real_operating_company(m)]
    adv_by_ticker = fetch_adv([s["ticker"] for s in typed]) if typed else {}

    pool: List[Dict[str, Any]] = []
    relvol_rejected = 0
    for snap in typed:
        meta = meta_by_ticker[snap["ticker"]]
        adv = adv_by_ticker.get(snap["ticker"])
        if not passes_premarket_gate(snap, meta, now_ms, avg_dollar_volume=adv):
            ratio = relative_volume(snap, adv)
            if ratio is not None and ratio < PREMARKET_CONFIG.min_relative_volume:
                relvol_rejected += 1
            continue
        snap["_meta"] = meta
        snap["_adv"] = adv
        snap["_relvol"] = relative_volume(snap, adv)
        pool.append(snap)

    chosen: List[Dict[str, Any]] = []
    up = down = 0
    for snap in pool:
        if len(chosen) >= limit:
            break
        pct = snap["_fields"]["change_pct"]
        if pct >= 0:
            if up >= 4:
                continue
            up += 1
        else:
            if down >= 4:
                continue
            down += 1
        chosen.append(snap)

    rows: List[Dict[str, Any]] = []
    for snap in chosen:
        news = {"why": None, "url": None, "is_earnings": False, "sentiment": None}
        if enrich_news:
            news = _normalize_enrichment(enrich(snap["ticker"]))
        snap["todaysChangePerc"] = snap["_fields"]["change_pct"]
        row = _to_brief_row(
            snap,
            news["why"],
            news["url"],
            window=PREMARKET,
            is_earnings=news["is_earnings"],
        )
        # Underscore-prefixed: telemetry only. The publisher strips these so
        # they never reach the email artifact.
        fields = snap["_fields"]
        row["_price"] = round(fields["price"], 4)
        row["_dollarVolume"] = round(fields["price"] * fields["volume"], 2)
        row["_adv"] = snap.get("_adv")
        row["_relvol"] = round(snap["_relvol"], 5) if snap.get("_relvol") is not None else None
        rows.append(row)

    stats = {
        "pmCandidates": len(coarse),
        "pmSurvivors": len(pool),
        "pmRendered": len(rows),
        "staleRejected": stale_rejected,
        "relvolRejected": relvol_rejected,
    }
    return rows, stats


NO_CATALYST_WHY = "No catalyst in the last 36 hours"


def _default_catalyst_fetch(ticker: str, company_name: Optional[str] = None) -> Any:
    from catalyst import fetch_catalyst as _fetch

    return _fetch(ticker, company_name)


def _driver_to_brief_row(
    contribution: Any,
    meta: Optional[TickerMeta],
    catalyst: Any,
) -> Dict[str, Any]:
    from index_drivers import DRIVER
    from premarket_movers import PREMARKET

    headline_pct = (
        contribution.premarket_change_pct
        if contribution.headline_window == PREMARKET
        else contribution.prior_session_change_pct
    )
    row: Dict[str, Any] = {
        "ticker": contribution.ticker,
        "pct": f"{headline_pct:+.2f}%",
        "dir": "down" if contribution.headline_bps < 0 else "up",
        "why": catalyst.text if catalyst else NO_CATALYST_WHY,
        "companyName": getattr(meta, "name", None) or contribution.ticker,
        "reasonUrl": catalyst.url if catalyst else None,
        "window": DRIVER,
        "windowLabel": "Index Driver",
        # Which session the headline percentage came from. Carried explicitly
        # rather than inferred from which companion percentage is present —
        # a driver flat in the other window has no companion to infer from.
        "headlineWindow": contribution.headline_window,
        "isEarnings": bool(catalyst.is_earnings) if catalyst else False,
        "hasCatalyst": catalyst is not None,
        # Basis points rank, steer, and log. They never render, so they stay
        # underscore-prefixed and are stripped before the row is published.
        "_contributionBps": round(contribution.headline_bps, 2),
        "_indexWeightPct": round(contribution.weight_pct, 2),
        "_catalystScore": catalyst.score if catalyst else None,
        "_price": round(contribution.price, 4),
        "_dollarVolume": round(contribution.dollar_volume, 2),
    }
    if contribution.headline_window == PREMARKET:
        if abs(contribution.prior_session_change_pct) >= 0.01:
            row["priorSessionPct"] = f"{contribution.prior_session_change_pct:+.2f}%"
    elif abs(contribution.premarket_change_pct) >= 0.01:
        row["premarketPct"] = f"{contribution.premarket_change_pct:+.2f}%"
    return row


def prior_session_pct_map(
    today_map: Dict[str, Any],
    yest_map: Dict[str, Any],
    tickers: List[str],
) -> Dict[str, float]:
    """
    Close-to-close move for the named tickers from the grouped daily bars.

    Premarket the snapshot cannot supply this: `day` is zeroed and `prevDay` is
    the session that just closed, so there is nothing to compare it against.
    These maps are already fetched for sectors and breadth.
    """
    out: Dict[str, float] = {}
    for ticker in tickers:
        today = today_map.get(ticker)
        yest = yest_map.get(ticker)
        if not today or not yest:
            continue
        try:
            close = float(today.get("c") or 0)
            prev = float(yest.get("c") or 0)
        except (TypeError, ValueError):
            continue
        if close > 0 and prev > 0:
            out[ticker] = (close / prev - 1) * 100
    return out


def select_index_driver_movers(
    snaps: Optional[List[Dict[str, Any]]] = None,
    *,
    now: Optional[datetime] = None,
    limit: int = 2,
    fetch_meta: Optional[Callable[[List[str]], Dict[str, TickerMeta]]] = None,
    fetch_catalyst: Optional[Callable[..., Any]] = None,
    prior_session_pcts: Optional[Dict[str, float]] = None,
    weights: Optional[Any] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Top index drivers as brief rows, plus stats for structured logging."""
    from index_drivers import fetch_watchlist_snapshot, select_index_drivers
    from index_weights import load_index_weights

    weights = weights if weights is not None else load_index_weights()
    empty_stats = {
        "driverCandidates": 0,
        "driverEligible": 0,
        "topDriverBps": 0.0,
        "driversDroppedNoCatalyst": 0,
        "catalystTopScore": 0,
        "weightsAsOf": weights.as_of_iso or "unknown",
    }
    if not len(weights):
        logger.warning("no index weights loaded — driver window disabled")
        return [], empty_stats

    if snaps is None:
        snaps = fetch_watchlist_snapshot()
    if not snaps:
        return [], empty_stats

    now = now or datetime.now()
    now_ms = now.timestamp() * 1000.0
    top, stats = select_index_drivers(
        snaps,
        weights,
        now_ms,
        limit=limit,
        prior_session_pct_by_ticker=prior_session_pcts,
    )
    if not top:
        return [], stats

    from index_drivers import NO_CATALYST_OVERRIDE_BPS

    fetch_meta = fetch_meta or get_ticker_meta
    catalyst_fetch = fetch_catalyst or _default_catalyst_fetch
    try:
        metas = fetch_meta([c.ticker for c in top])
    except Exception as exc:
        logger.warning("driver meta lookup failed: %s", exc)
        metas = {}

    # Resolve catalysts before finalising slots. A driver whose entire purpose
    # is explanation and which has none is a blank slot, so it is dropped and
    # the next-ranked driver is promoted into its place. This is deliberately
    # different from the mover rule, where "flow-driven" is honest information.
    rows: List[Dict[str, Any]] = []
    dropped = 0
    top_score: Optional[int] = None
    for contribution in top:
        if len(rows) >= limit:
            break
        name = getattr(metas.get(contribution.ticker), "name", None)
        try:
            catalyst = catalyst_fetch(contribution.ticker, name)
        except Exception as exc:
            logger.info("driver catalyst lookup failed for %s: %s", contribution.ticker, exc)
            catalyst = None

        if catalyst is not None:
            top_score = catalyst.score if top_score is None else max(top_score, catalyst.score)
        elif abs(contribution.headline_bps) < NO_CATALYST_OVERRIDE_BPS:
            dropped += 1
            logger.info(
                "driver %s dropped: no catalyst at %.1f bps",
                contribution.ticker,
                contribution.headline_bps,
            )
            continue

        rows.append(_driver_to_brief_row(contribution, metas.get(contribution.ticker), catalyst))

    stats = {
        **stats,
        "driversDroppedNoCatalyst": dropped,
        "catalystTopScore": top_score if top_score is not None else 0,
    }
    return rows, stats


def classify_market_breadth(
    snaps: List[Dict[str, Any]],
    prior_pcts: Dict[str, float],
    is_premarket: bool,
) -> Any:
    """
    Breadth off the top 3 drivers by bps, before the catalyst filter — breadth
    is a fact about market structure, not about whether we found an article.
    """
    from breadth import BreadthRead, classify_breadth
    from index_drivers import build_driver_contribution, rank_drivers
    from index_weights import load_index_weights
    from premarket_movers import PREMARKET, read_window_fields

    def _change(ticker: str) -> Optional[float]:
        if is_premarket:
            for snap in snaps:
                if str(snap.get("ticker") or "").upper() == ticker:
                    return read_window_fields(snap, PREMARKET)["change_pct"]
            return None
        return prior_pcts.get(ticker)

    spy = _change("SPY")
    rsp = _change("RSP")
    if spy is None or rsp is None:
        return BreadthRead("unknown", None, None)

    weights = load_index_weights()
    now_ms = datetime.now().timestamp() * 1000.0
    contributions = []
    for snap in snaps:
        ticker = str(snap.get("ticker") or "").upper()
        weight = weights.get(ticker)
        if not weight:
            continue
        row = build_driver_contribution(
            snap, weight, now_ms, min_bps=0.0, prior_session_pct=prior_pcts.get(ticker)
        )
        if row:
            contributions.append(row)

    top3 = [c.headline_bps for c in rank_drivers(contributions)[:3]]
    return classify_breadth(top3, spy, spy, rsp)


def _strip_telemetry(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop underscore-prefixed measurement fields before the row is published."""
    return [{k: v for k, v in row.items() if not k.startswith("_")} for row in rows]


_WINDOW_ORDER = {"index_driver": 0, "premarket": 1, "prior_session": 2}


def merge_windows(
    premarket_rows: List[Dict[str, Any]],
    prior_rows: List[Dict[str, Any]],
    driver_rows: Optional[List[Dict[str, Any]]] = None,
    limit: int = 6,
    driver_slots: int = 2,
    premarket_slots: int = 2,
    max_same: int = 4,
) -> List[Dict[str, Any]]:
    """
    Allocate slots per window instead of sorting the windows against each other.

    The three windows do not share a base. Premarket dollar volumes run ~50x
    below a full session, and driver rows are ranked in basis points rather
    than percent. Any combined sort would hand every slot to one window and
    look exactly like the feature working.

    A ticker qualifying in more than one window keeps ONE row, tagged with the
    highest-precedence window (index_driver > premarket > prior_session), and
    carries the other windows' numbers on that row.
    """
    driver_rows = driver_rows or []
    prior_by_ticker = {r["ticker"]: r for r in prior_rows}
    premarket_by_ticker = {r["ticker"]: r for r in premarket_rows}

    merged: List[Dict[str, Any]] = []
    taken: set[str] = set()
    up = down = 0

    def _try_add(row: Dict[str, Any]) -> bool:
        nonlocal up, down
        if row["ticker"] in taken or len(merged) >= limit:
            return False
        if row["dir"] == "up":
            if up >= max_same:
                return False
            up += 1
        else:
            if down >= max_same:
                return False
            down += 1
        taken.add(row["ticker"])
        merged.append(row)
        return True

    def _absorb(row: Dict[str, Any], *others: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Carry lower-precedence windows' numbers onto the surviving row."""
        merged_row = dict(row)
        for other in others:
            if not other:
                continue
            if other.get("window") == "prior_session":
                merged_row.setdefault("priorSessionPct", other["pct"])
            elif other.get("window") == "premarket":
                merged_row.setdefault("premarketPct", other["pct"])
            if merged_row["why"] == FLOW_DRIVEN_WHY and other.get("why") != FLOW_DRIVEN_WHY:
                merged_row["why"] = other["why"]
                merged_row["reasonUrl"] = other.get("reasonUrl")
                merged_row["isEarnings"] = merged_row.get("isEarnings") or other.get("isEarnings", False)
        return merged_row

    for row in driver_rows[:driver_slots]:
        ticker = row["ticker"]
        _try_add(_absorb(row, premarket_by_ticker.get(ticker), prior_by_ticker.get(ticker)))

    for row in premarket_rows[:premarket_slots]:
        _try_add(_absorb(row, prior_by_ticker.get(row["ticker"])))

    prior_taken = 0
    for row in prior_rows:
        if prior_taken >= limit - driver_slots - premarket_slots:
            break
        if _try_add(row):
            prior_taken += 1

    # Backfill when a window came up short. Drivers are deliberately excluded:
    # their slot count is a hard cap, not a reservation. A section that fills
    # with mega-caps every morning stops being information, and the whole point
    # is to sit them beside the percentage movers rather than replace them.
    for pool in (premarket_rows[premarket_slots:], prior_rows):
        for row in pool:
            if len(merged) >= limit:
                break
            if row.get("window") == "premarket":
                row = _absorb(row, prior_by_ticker.get(row["ticker"]))
            _try_add(row)

    # Drivers answer "why is the tape here"; movers answer "what gapped".
    merged.sort(key=lambda r: _WINDOW_ORDER.get(r.get("window"), 9))
    return merged[:limit]


def build_notable_movers(
    session_date: Optional[str] = None,
    *,
    include_premarket: bool = True,
    include_drivers: bool = True,
    context_out: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Merged index-driver + premarket + prior-session movers. Empty → omit section.

    Two data sources by necessity: grouped daily bars give a true close-to-close
    prior-session move, while the snapshot's prevDay only offers open-to-close.
    The grouped pair is already fetched and cached for sectors and breadth, so
    the premarket snapshot is the only additional round trip.
    """
    from index_drivers import MIN_DRIVER_BPS
    from market_internals import get_session_bar_maps
    from premarket_movers import PREMARKET, fetch_market_snapshot, market_phase

    try:
        from daily_brief_v2 import NY

        now_et = datetime.now(tz=NY)
    except Exception:
        now_et = datetime.now()
    phase = market_phase(now_et)

    prior_rows: List[Dict[str, Any]] = []
    today_map, yest_map, _date = get_session_bar_maps(session_date)
    if today_map and yest_map:
        prior_rows = select_notable_movers(session_snapshots(today_map, yest_map))
    else:
        logger.warning("movers_filter: no session bars")

    premarket_rows: List[Dict[str, Any]] = []
    pm_stats: Dict[str, Any] = {"pmCandidates": 0, "pmRendered": 0, "staleRejected": 0}
    # `session_date` names which prior session feeds the prior_session window;
    # every live build passes one. Gating premarket on its absence meant the
    # window never ran in production. Backfills opt out explicitly instead.
    if phase == PREMARKET and include_premarket:
        snaps = fetch_market_snapshot()
        premarket_rows, pm_stats = select_premarket_movers(snaps)
    else:
        logger.info(
            "movers: phase=%s include_premarket=%s — prior-session rows only",
            phase,
            include_premarket,
        )

    driver_rows: List[Dict[str, Any]] = []
    driver_stats: Dict[str, Any] = {
        "driverCandidates": 0,
        "driverEligible": 0,
        "driverRendered": 0,
        "topDriverBps": 0.0,
        "weightsAsOf": "unknown",
    }
    breadth: Any = None
    if include_drivers:
        try:
            from index_drivers import BREADTH_PROXIES, DRIVER_WATCHLIST, fetch_watchlist_snapshot

            driver_snaps = fetch_watchlist_snapshot()
            prior_pcts = (
                prior_session_pct_map(
                    today_map, yest_map, list(DRIVER_WATCHLIST) + BREADTH_PROXIES
                )
                if today_map and yest_map
                else {}
            )
            driver_rows, driver_stats = select_index_driver_movers(
                snaps=driver_snaps, now=now_et, prior_session_pcts=prior_pcts
            )
            breadth = classify_market_breadth(
                driver_snaps, prior_pcts, phase == PREMARKET
            )
        except Exception as exc:
            logger.warning("index driver window failed: %s", exc)

    merged = merge_windows(premarket_rows, prior_rows, driver_rows)

    try:
        from movers_telemetry import record_rendered

        record_rendered(merged, now_et.strftime("%Y-%m-%d"))
    except Exception as exc:
        logger.warning("observation logging failed: %s", exc)

    rendered_drivers = sum(1 for r in merged if r.get("window") == "index_driver")
    driver_stats = {**driver_stats, "driverRendered": rendered_drivers}
    # The suppressed figures live here. A log is exactly where a number too
    # noisy for a newsletter belongs, and this is what threshold tuning reads.
    breadth_stats = {
        "breadthRegime": getattr(breadth, "regime", "unknown"),
        "concentrationRatio": (
            round(breadth.concentration_ratio, 2)
            if breadth is not None and breadth.concentration_ratio is not None
            else None
        ),
        "equalWeightSpreadBps": (
            round(breadth.equal_weight_spread_bps, 1)
            if breadth is not None and breadth.equal_weight_spread_bps is not None
            else None
        ),
    }
    payload = {
        "event": "movers_merge",
        "phase": phase,
        "psRendered": len(prior_rows),
        "rendered": len(merged),
        **pm_stats,
        **driver_stats,
        **breadth_stats,
    }

    warnings: List[str] = []
    if phase == PREMARKET and pm_stats.get("pmRendered", 0) == 0:
        warnings.append("premarket window produced no rows — check thresholds")
    # A qualifying driver that did not render means slot allocation or dedupe
    # ate it. Silent otherwise, because an empty drivers section is also the
    # correct output on a genuinely broad day. A catalyst drop is a known
    # cause, so it is reported separately rather than as an unexplained loss.
    dropped_no_catalyst = driver_stats.get("driversDroppedNoCatalyst", 0)
    if rendered_drivers == 0 and abs(driver_stats.get("topDriverBps") or 0) >= MIN_DRIVER_BPS:
        if dropped_no_catalyst:
            warnings.append(
                f"all drivers dropped for want of a catalyst ({dropped_no_catalyst}) — "
                f"MIN_CATALYST_SCORE may be too strict"
            )
        else:
            warnings.append(
                f"driver at {driver_stats['topDriverBps']} bps cleared the floor "
                f"but rendered no row"
            )
    elif dropped_no_catalyst > 1:
        warnings.append(
            f"{dropped_no_catalyst} drivers dropped for want of a catalyst — "
            f"read the discarded candidates before tuning"
        )
    if warnings:
        logger.warning("%s: %s", "; ".join(warnings), payload)
    else:
        logger.info("%s", payload)

    if context_out is not None:
        # The label crosses the boundary into the prompt. The numbers do not.
        context_out["breadth_regime"] = breadth_stats["breadthRegime"]

    return _strip_telemetry(merged)
