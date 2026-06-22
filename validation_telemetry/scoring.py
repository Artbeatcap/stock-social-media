"""Catalyst recall scoring: recency + sentiment alignment vs pipeline attribution."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Optional


def normalize_headline(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip().lower())
    cleaned = re.sub(r"[^\w\s$%+-]", "", cleaned)
    return cleaned


def headlines_match(cited: str, candidate: str, threshold: float = 0.55) -> bool:
    a = normalize_headline(cited)
    b = normalize_headline(candidate)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    return SequenceMatcher(None, a, b).ratio() >= threshold


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
) -> float:
    """Higher is better. Combines 24h recency decay with directional sentiment fit."""
    hours_ago = max(0.0, (now_ts - published_ts) / 3600.0)
    recency = max(0.0, 1.0 - hours_ago / 24.0)

    wanted = "positive" if is_gainer else "negative"
    sentiment = (sentiment or "").lower()
    if sentiment == wanted:
        sentiment_fit = 1.0
    elif sentiment in {"positive", "negative"}:
        sentiment_fit = 0.0
    else:
        sentiment_fit = 0.5

    return round(recency * 0.6 + sentiment_fit * 0.4, 4)


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
        ranked.append(
            {
                "headline": headline,
                "published_ts": published_ts,
                "sentiment": sentiment or "unknown",
                "score": score_headline(
                    published_ts=published_ts,
                    now_ts=now_ts,
                    sentiment=sentiment,
                    is_gainer=is_gainer,
                ),
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
