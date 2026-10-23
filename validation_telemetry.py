"""Paths and trade-date helpers for production validation telemetry."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional

import pytz

NY = pytz.timezone("America/New_York")
TELEMETRY_ROOT = Path(__file__).resolve().parent / "validation_telemetry"
REPORTS_DIR = TELEMETRY_ROOT / "reports"
RESEARCH_LOG_DIR = TELEMETRY_ROOT / "research_log"

FEATURED_AUDIT_ET = time(17, 30)
RECALL_AUDIT_ET = time(21, 15)
AUDIT_WINDOW_MINUTES = 45


def ensure_telemetry_dirs() -> None:
    for path in (
        TELEMETRY_ROOT,
        REPORTS_DIR,
        RESEARCH_LOG_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


def day_dir(trade_date: date) -> Path:
    return TELEMETRY_ROOT / trade_date.isoformat()


def is_us_market_weekday(d: date) -> bool:
    return d.weekday() < 5


def prior_session(d: date) -> date:
    """Previous calendar day, skipping weekends."""
    cur = d - timedelta(days=1)
    while not is_us_market_weekday(cur):
        cur -= timedelta(days=1)
    return cur


def resolve_audit_trade_date(now: Optional[datetime] = None) -> date:
    """
    Trade date for quality audits.

    Before 5:30pm ET on a weekday, use the prior US session (yesterday, or
    Friday when run on Monday morning).
    """
    now = now or datetime.now(tz=NY)
    if now.tzinfo is None:
        now = NY.localize(now)
    else:
        now = now.astimezone(NY)

    today = now.date()
    if is_us_market_weekday(today) and now.time() < FEATURED_AUDIT_ET:
        return prior_session(today)
    if not is_us_market_weekday(today):
        return prior_session(today)
    return today


def audit_as_of_for_trade_date(trade_date: date, now: Optional[datetime] = None) -> datetime:
    """
    End of the news lookback window for catalyst recall on ``trade_date``.

    When the audit runs before 9:15pm ET on the same calendar day as
    ``trade_date``, cap at ``now`` so we do not pretend to see future headlines.
    """
    now = now or datetime.now(tz=NY)
    if now.tzinfo is None:
        now = NY.localize(now)
    else:
        now = now.astimezone(NY)

    end = NY.localize(datetime.combine(trade_date, RECALL_AUDIT_ET))
    if now.date() == trade_date and now < end:
        return now
    return end


def in_audit_window(
    scheduled: time,
    now: Optional[datetime] = None,
    window_minutes: int = AUDIT_WINDOW_MINUTES,
) -> bool:
    now = now or datetime.now(tz=NY)
    if now.tzinfo is None:
        now = NY.localize(now)
    else:
        now = now.astimezone(NY)
    start = datetime.combine(now.date(), scheduled)
    end = start + timedelta(minutes=window_minutes)
    cur = datetime.combine(now.date(), now.time())
    return start <= cur < end
