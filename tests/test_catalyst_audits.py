"""Unit tests for catalyst quality audit helpers."""

from __future__ import annotations

from datetime import datetime

import pytz

from scripts.catalyst_audit_common import (
    classify_recall,
    rank_top_headlines,
    score_headline,
    token_overlap_ratio,
)
from validation_telemetry import effective_audit_trade_date

NY = pytz.timezone("America/New_York")


def test_token_overlap_hit():
    a = "NVIDIA beats earnings expectations, raises guidance"
    b = "NVIDIA beats earnings expectations and raises full-year guidance"
    assert token_overlap_ratio(a, b) >= 0.35


def test_classify_recall_miss_without_catalyst():
    verdict, _, overlap = classify_recall("", [{"headline": "Some news"}])
    assert verdict == "miss"
    assert overlap == 0.0


def test_score_headline_prefers_matching_sentiment():
    as_of = NY.localize(datetime(2026, 10, 13, 21, 0))
    positive = {
        "headline": "Upgrade",
        "sentiment": "positive",
        "published_dt": NY.localize(datetime(2026, 10, 13, 20, 0)),
    }
    negative = {
        "headline": "Downgrade",
        "sentiment": "negative",
        "published_dt": NY.localize(datetime(2026, 10, 13, 20, 0)),
    }
    assert score_headline(positive, as_of, want_positive=True) > score_headline(
        negative, as_of, want_positive=True
    )


def test_rank_top_headlines_limits_to_five():
    as_of = NY.localize(datetime(2026, 10, 13, 21, 0))
    items = [
        {
            "headline": f"Story {i}",
            "sentiment": "positive",
            "published_dt": NY.localize(datetime(2026, 10, 13, 20 - i % 5, 0)),
        }
        for i in range(12)
    ]
    ranked = rank_top_headlines(items, as_of, want_positive=True, limit=5)
    assert len(ranked) == 5


def test_effective_audit_trade_date_weekend():
    saturday = NY.localize(datetime(2026, 10, 10, 12, 0))
    assert effective_audit_trade_date(saturday) == "2026-10-09"
