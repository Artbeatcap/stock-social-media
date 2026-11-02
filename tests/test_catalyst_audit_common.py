from datetime import date, datetime

import pytz

from scripts.catalyst_audit_common import (
    audit_as_of_for_trade_date,
    resolve_audit_trade_date,
    token_overlap,
)

NY = pytz.timezone("America/New_York")


def test_token_overlap_hit():
    a = "Nuwellis wins pediatric Aquadex approval, shares surge"
    b = "Nuwellis shares surge on pediatric Aquadex system approval"
    assert token_overlap(a, b) >= 0.35


def test_resolve_audit_trade_date_before_close():
    # Monday 8am ET → prior Friday
    now = NY.localize(datetime(2026, 11, 2, 8, 0, 0))
    assert resolve_audit_trade_date(now) == date(2026, 10, 30)


def test_audit_as_of_same_day_before_recall_window():
    trade = date(2026, 10, 30)
    now = NY.localize(datetime(2026, 10, 30, 15, 0, 0))
    assert audit_as_of_for_trade_date(trade, now) == now
