"""Catalyst recall scoring: recency + sentiment alignment vs pipeline attribution."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

HIT_SIMILARITY = 0.35
RECENCY_WEIGHT = 0.55
SENTIMENT_WEIGHT = 0.45


def normalize_headline(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip().lower())
    cleaned = re.sub(r"[^\w\s$%+-]", "", cleaned)
    return cleaned


def _token_set(text: str) -> set[str]:
    normalized = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (text or "").lower())).strip()
    return {token for token in normalized.split() if len(token) > 2}


def headline_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    left_norm = normalize_headline(left)
    right_norm = normalize_headline(right)
    if left_norm in right_norm or right_norm in left_norm:
        return 1.0
    left_tokens = _token_set(left)
    right_tokens = _token_set(right)
    if not left_tokens or not right_tokens:
        return SequenceMatcher(None, left_norm, right_norm).ratio()
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def headlines_match(cited: str, candidate: str, threshold: float = HIT_SIMILARITY) -> bool:
    return headline_similarity(cited, candidate) >= threshold


def ticker_sentiment(item: dict[str, Any], ticker: str) -> str:
    """Return sentiment for *ticker* from Massive insights, else first insight."""
    ticker = ticker.upper()
    insights = item.get("insights") or []
    if isinstance(insights, list):
        for insight in insights:
            if str(insight.get("ticker") or "").upper() == ticker:
                return str(insight.get("sentiment") or "").lower()
        if insights:
            return str(insights[0].get("sentiment") or "").lower()
    return str(item.get("sentiment") or "").lower()


def score_headline(
    *,
    published_ts: int,
    now_ts: int,
    sentiment: str,
    is_gainer: bool,
    max_age_hours: float = 24.0,
) -> float:
    """Higher is better. Combines recency decay with directional sentiment fit."""
    hours_ago = max(0.0, (now_ts - published_ts) / 3600.0)
    if hours_ago > max_age_hours:
        return 0.0
    recency = max(0.0, (max_age_hours - hours_ago) / max_age_hours)

    wanted = "positive" if is_gainer else "negative"
    sentiment = (sentiment or "").lower()
    if sentiment == wanted:
        sentiment_fit = 1.0
    elif sentiment in {"", "neutral"}:
        sentiment_fit = 0.5
    elif sentiment in {"positive", "negative"}:
        sentiment_fit = 0.0
    else:
        sentiment_fit = 0.5

    return round(recency * RECENCY_WEIGHT + sentiment_fit * SENTIMENT_WEIGHT, 4)


def rank_headlines(
    news_items: list[dict[str, Any]],
    *,
    ticker: str,
    is_gainer: bool,
    now_ts: int,
    limit: int = 5,
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for item in news_items[:limit]:
        headline = str(item.get("headline") or item.get("title") or "")
        published_ts = int(item.get("datetime") or item.get("published_ts") or 0)
        sentiment = ticker_sentiment(item, ticker)
        score = score_headline(
            published_ts=published_ts,
            now_ts=now_ts,
            sentiment=sentiment,
            is_gainer=is_gainer,
        )
        if published_ts and score > 0:
            ranked.append(
                {
                    "headline": headline,
                    "published_ts": published_ts,
                    "sentiment": sentiment or "unknown",
                    "score": score,
                    "source": item.get("source") or "",
                    "url": item.get("url") or item.get("article_url") or "",
                }
            )
    ranked.sort(key=lambda row: row["score"], reverse=True)
    return ranked


def audit_mover_catalyst(
    mover: dict[str, Any],
    *,
    side: str,
    news_items: list[dict[str, Any]],
    now_ts: int,
) -> dict[str, Any]:
    """Compare pipeline catalyst against top Massive headlines for one mover."""
    ticker = mover["ticker"]
    is_gainer = side == "gainer"
    cited = str(mover.get("catalyst") or "").strip()
    ranked = rank_headlines(news_items, ticker=ticker, is_gainer=is_gainer, now_ts=now_ts)

    if not cited:
        result = "no_catalyst"
    elif not ranked:
        result = "miss"
    elif any(headlines_match(cited, row["headline"]) for row in ranked):
        result = "hit"
    else:
        result = "miss"

    best = ranked[0] if ranked else None
    return {
        "ticker": ticker,
        "side": side,
        "pct_move": mover.get("change_percentage"),
        "pipeline_catalyst": cited or "(none)",
        "best_massive_headline": best["headline"] if best else "(no news)",
        "best_massive_score": best["score"] if best else None,
        "top_massive_headlines": ranked,
        "result": result,
    }
