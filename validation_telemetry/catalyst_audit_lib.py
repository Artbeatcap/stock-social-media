"""
Shared helpers for catalyst recall and featured-mover audits.

Uses the same Massive/Polygon HTTP client as massive_client (MASSIVE_API_KEY).
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytz

NY = pytz.timezone("America/New_York")

REPO_ROOT = Path(__file__).resolve().parents[1]
TELEMETRY_DIR = REPO_ROOT / "validation_telemetry"
REPORTS_DIR = TELEMETRY_DIR / "reports"
POSTMARKET_DIR = TELEMETRY_DIR / "postmarket_sent"

POS_WORDS = frozenset(
    {
        "beat",
        "beats",
        "surge",
        "soar",
        "rally",
        "gain",
        "gains",
        "up",
        "rise",
        "rising",
        "upgrade",
        "growth",
        "record",
        "strong",
        "positive",
        "bullish",
        "outperform",
        "win",
        "wins",
        "high",
        "expands",
    }
)
NEG_WORDS = frozenset(
    {
        "miss",
        "misses",
        "slump",
        "plunge",
        "crash",
        "fall",
        "falls",
        "drop",
        "drops",
        "down",
        "downgrade",
        "cut",
        "cuts",
        "weak",
        "negative",
        "bearish",
        "lawsuit",
        "probe",
        "investigation",
        "warning",
        "layoff",
        "bankruptcy",
        "recall",
        "selloff",
    }
)

TOKEN_RE = re.compile(r"[a-z0-9]+", re.I)


def telemetry_date_et(now: Optional[datetime] = None) -> str:
    dt = now or datetime.now(tz=NY)
    return dt.strftime("%Y-%m-%d")


def load_postmarket_snapshot(trading_date_et: str) -> Optional[Dict[str, Any]]:
    path = POSTMARKET_DIR / f"{trading_date_et}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def tokenize(text: str) -> set[str]:
    if not text:
        return set()
    return {m.group(0).lower() for m in TOKEN_RE.finditer(text)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def heuristic_sentiment(headline: str) -> int:
    """Return -1, 0, or +1."""
    if not headline:
        return 0
    toks = tokenize(headline)
    p = len(toks & POS_WORDS)
    n = len(toks & NEG_WORDS)
    if p > n:
        return 1
    if n > p:
        return -1
    return 0


def insight_sentiment(item: Dict[str, Any]) -> Optional[int]:
    s = (item.get("sentiment") or "").lower()
    if s in ("positive", "bullish"):
        return 1
    if s in ("negative", "bearish"):
        return -1
    if s in ("neutral",):
        return 0
    return None


def combined_sentiment(headline: str, item: Dict[str, Any]) -> int:
    ins = insight_sentiment(item)
    if ins is not None and ins != 0:
        return ins
    return heuristic_sentiment(headline)


def sentiment_match(expected: int, headline_sent: int) -> float:
    """expected: +1 gainer, -1 loser."""
    if headline_sent == 0:
        return 0.7
    if headline_sent == expected:
        return 1.0
    return 0.2


def recency_weight(published_ts: int, now_ts: int, window_sec: int) -> float:
    age = max(0, now_ts - published_ts)
    if age >= window_sec:
        return 0.0
    return 1.0 - (age / window_sec)


def score_headline(
    pipeline_catalyst: str,
    expected_move_sign: int,
    headline: str,
    item: Dict[str, Any],
    now_ts: int,
    window_sec: int,
) -> float:
    pub = int(item.get("datetime") or now_ts)
    jac = jaccard(tokenize(pipeline_catalyst), tokenize(headline))
    sm = sentiment_match(expected_move_sign, combined_sentiment(headline, item))
    rw = recency_weight(pub, now_ts, window_sec)
    return 0.55 * jac + 0.35 * sm * rw + 0.10 * rw


def classify_hit(best_score: float, best_jaccard: float, sentiment_ok: bool) -> str:
    if best_score >= 0.45:
        return "hit"
    if best_jaccard >= 0.15 and sentiment_ok:
        return "hit"
    if best_score >= 0.28:
        return "weak_hit"
    return "miss"


def parse_featured_ticker(featured_stock: Optional[str]) -> Optional[str]:
    if not featured_stock:
        return None
    m = re.match(r"^([A-Z]{1,5})\b", str(featured_stock).strip().upper())
    return m.group(1) if m else None


def featured_ticker_from_postmarket_payload(payload: Dict[str, Any]) -> Optional[str]:
    """Match postmarket emailer logic: explicit featured_stock, else first mover symbol found in tweet."""
    pd = payload.get("post_data") or {}
    fs = pd.get("featured_stock")
    t = parse_featured_ticker(fs if isinstance(fs, str) else None)
    if t:
        return t
    post = str(pd.get("post") or pd.get("short_version") or "")
    ctx = payload.get("context") or {}
    symbols: List[str] = []
    for side in ("top_gainers", "top_losers"):
        for m in ctx.get(side) or []:
            sym = str(m.get("ticker") or m.get("symbol") or "").strip().upper()
            if sym:
                symbols.append(sym)
    for sym in sorted(set(symbols), key=len, reverse=True):
        if re.search(rf"(?<![A-Z])\$?{re.escape(sym)}\b", post):
            return sym
    return None


def ensure_dirs() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    POSTMARKET_DIR.mkdir(parents=True, exist_ok=True)
