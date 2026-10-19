"""Tests for weekly_brief_v2 pipeline."""

from __future__ import annotations

import os
import sys
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SERVER_BACKUP = Path(__file__).resolve().parents[1] / "server-backup"
sys.path.insert(0, str(SERVER_BACKUP))


SAMPLE_MD = """## MAJOR DRIVER
Tech led while rates stayed in focus.

## WEEK IN REVIEW
SPY finished the week higher. $SPY and $QQQ both advanced as $XLK outperformed.

## KEY LEVEL
Watch $SPY at 610 — a break above opens room toward resistance.

## WEEK AHEAD
Catalysts lean macro-heavy; vol may stay bid into payrolls.

## BIGGEST RISK
A hot CPI print could unwind the recent risk-on tone.
"""


@pytest.fixture
def sample_context():
    return {
        "brand": {"name": "Options Plunge", "app_url": "https://optionsplunge.com/dashboard"},
        "week_label": "Week of June 23–27, 2026",
        "preheader": "Tech led while rates stayed in focus.",
        "tape": [
            {"symbol": "SPY", "price": "$610.00", "pct": 1.25},
            {"symbol": "QQQ", "price": "$540.00", "pct": 1.80},
            {"symbol": "IWM", "price": "$210.00", "pct": -0.40},
            {"symbol": "VIX", "price": "16.50", "pct": None},
        ],
        "sections": {
            "major_driver": "Tech led while rates stayed in focus.",
            "week_in_review_html": "<p>SPY finished the week higher.</p>",
            "key_level_note": "Watch SPY at 610.",
            "week_ahead_html": "<p>Catalysts lean macro-heavy.</p>",
            "biggest_risk": "A hot CPI print could unwind risk-on tone.",
        },
        "sectors": [
            {"name": "Technology", "etf": "XLK", "pct": 2.1},
            {"name": "Energy", "etf": "XLE", "pct": -1.2},
        ],
        "movers": [
            {
                "ticker": "NVDA",
                "pct": "+4.50%",
                "dir": "up",
                "why": "AI demand",
                "companyName": "NVIDIA Corporation",
            },
        ],
        "levels": {
            "spy": {
                "pivot": "608.00",
                "support": "605.00",
                "support2": "602.00",
                "resistance": "612.00",
                "resistance2": "615.00",
            }
        },
        "week_ahead_bullets": [
            {"date": "Tue", "event": "September Pending Home Sales", "why": "Housing"},
            {"date": "Thu", "event": "Initial Jobless Claims", "why": "Labor market"},
        ],
        "earnings_bullets": [
            {"date": "Tue", "event": "NFLX, IBM", "why": "After the close"},
        ],
        "data_stale": False,
        "unsubscribe_url": "https://optionsplunge.com/unsubscribe",
        "preferences_url": "https://optionsplunge.com/settings",
        "generated_at": "2026-06-29 08:00 ET",
        "date_range": {"monday": "2026-06-23", "friday": "2026-06-27", "label": "Week of June 23–27, 2026"},
        "subject": "Options Plunge — Weekly Brief — Week of June 23–27, 2026",
    }


class TestWeeklyBriefHelpers:
    def test_find_fabricated_detects_unknown_ticker(self):
        from weekly_brief_v2 import _find_fabricated

        allowed = {"SPY", "QQQ", "XLK"}
        bad = _find_fabricated("Leaders included $SPY and $FAKE", allowed)
        assert bad == ["FAKE"]

    def test_split_sections_parses_markdown(self):
        from weekly_brief_v2 import _split_sections

        secs = _split_sections(SAMPLE_MD)
        assert "major_driver" in secs
        assert "week_in_review" in secs
        assert "SPY" in secs["week_in_review"]

    def test_fmt_week_label_cross_platform(self):
        from weekly_brief_v2 import _fmt_week_label

        label = _fmt_week_label(date(2026, 6, 23), date(2026, 6, 27))
        assert label == "Week of June 23–27, 2026"

    def test_build_feed_includes_breadth_when_internals_present(self):
        from weekly_brief_v2 import _build_feed

        feed = _build_feed(
            "Week of June 23–27, 2026",
            [{"symbol": "SPY", "price": "$610", "pct": 1.0}],
            [{"name": "Technology", "etf": "XLK", "pct": 2.0}],
            [{"ticker": "NVDA", "pct": "+3%", "dir": "up", "why": ""}],
            {"spy": {"pivot": "608", "support": "605", "support2": "602", "resistance": "612", "resistance2": "615"}},
            [{"date": "Mon", "event": "PMI", "why": ""}],
            internals={
                "breadth": {"advancers": 1867, "decliners": 923, "ad_ratio": 2.02},
                "trend": {"pct_above": 1.5, "above_50dma": True},
                "sectors": [{"name": "Technology", "etf": "XLK", "pct": 1.2}],
            },
        )
        assert "A/D: 1867 advancers / 923 decliners" in feed
        assert "50DMA" in feed

    def test_major_us_events_keep_housing_sentiment_and_claims(self):
        from market_brief_generator import _select_major_us_events

        raw = [
            {"country": "US", "event": "September Pending Home Sales", "impact": "medium", "time": "2026-10-20 14:00:00"},
            {"country": "US", "event": "Initial Jobless Claims", "impact": "high", "time": "2026-10-22 12:30:00"},
            {"country": "US", "event": "Michigan Consumer Sentiment", "impact": "medium", "time": "2026-10-23 14:00:00"},
            {"country": "DE", "event": "CPI", "impact": "high", "time": "2026-10-21 06:00:00"},
            {"country": "US", "event": "API Crude Oil Stock Change", "impact": "low", "time": "2026-10-21 20:30:00"},
        ]
        names = [row["event"] for row in _select_major_us_events(raw, "2026-10-19")]
        assert names == [
            "September Pending Home Sales",
            "Initial Jobless Claims",
            "Michigan Consumer Sentiment",
        ]
        dated = _select_major_us_events(raw, "2026-10-19")
        assert dated[0]["date"] == "2026-10-20"
        assert dated[0]["why"] == "Housing"
        assert dated[1]["why"] == "Labor market"

    def test_trim_keeps_high_impact_when_the_week_is_crowded(self):
        from weekly_brief_v2 import _trim_week_events

        events = [
            {"date": "2026-10-19", "event": f"Print {i}", "impact": "medium"}
            for i in range(8)
        ]
        events[6] = {"date": "2026-10-23", "event": "CPI", "impact": "high"}
        kept = _trim_week_events(events, limit=6)
        assert len(kept) == 6
        assert any(row["event"] == "CPI" for row in kept)

    def test_earnings_groups_anticipated_names_by_session(self):
        from weekly_brief_v2 import _earnings_bullets_from_rows

        rows = [
            {"date": "2026-10-20", "symbol": "NFLX", "hour": "amc", "revenue_estimate": 11_000_000_000, "eps_estimate": 5},
            {"date": "2026-10-20", "symbol": "IBM", "hour": "amc", "revenue_estimate": 16_000_000_000, "eps_estimate": 2},
            {"date": "2026-10-20", "symbol": "TINY", "hour": "amc", "revenue_estimate": None, "eps_estimate": None},
            {"date": "2026-10-22", "symbol": "AAPL", "hour": "bmo", "revenue_estimate": 90_000_000_000, "eps_estimate": 1.5},
        ]
        bullets = _earnings_bullets_from_rows(rows)
        assert bullets[0] == {"date": "Tue", "event": "IBM, NFLX", "why": "After the close"}
        assert bullets[1] == {"date": "Thu", "event": "AAPL", "why": "Before the open"}


class TestBuildWeeklyBrief:
    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key", "MASSIVE_API_KEY": "test-massive"})
    @patch("weekly_brief_v2._friday_session_movers", return_value=[
        {"ticker": "NVDA", "pct": "+4.50%", "dir": "up", "why": "AI demand"},
    ])
    @patch("weekly_brief_v2._rewrite_in_voice", side_effect=lambda x: x)
    @patch("weekly_brief_v2._llm", return_value=SAMPLE_MD)
    @patch("weekly_brief_v2._fetch_friday_internals")
    @patch("weekly_brief_v2._weekly_pivots")
    @patch("weekly_brief_v2._weekly_sectors")
    @patch("weekly_brief_v2._weekly_index_change")
    @patch("weekly_brief_v2.fetch_stock_prices_strict")
    @patch("weekly_brief_v2._last_completed_week_range")
    def test_build_weekly_brief_context_shape(
        self,
        mock_week_range,
        mock_prices,
        mock_index_chg,
        mock_sectors,
        mock_pivots,
        mock_internals,
        mock_llm,
        mock_voice,
        _mock_movers,
    ):
        from weekly_brief_v2 import build_weekly_brief

        mock_week_range.return_value = (date(2026, 6, 23), date(2026, 6, 27))
        mock_prices.return_value = {
            "spy": {"current_price": 610.0},
            "qqq": {"current_price": 540.0},
            "iwm": {"current_price": 210.0},
            "vix": {"current_price": 16.5},
        }
        mock_index_chg.return_value = 1.25
        mock_sectors.return_value = [{"name": "Technology", "etf": "XLK", "pct": 2.1}]
        mock_pivots.return_value = {
            "pivot": "608.00",
            "support": "605.00",
            "support2": "602.00",
            "resistance": "612.00",
            "resistance2": "615.00",
        }
        mock_internals.return_value = {
            "movers": {
                "gainers": [{"symbol": "HOOG", "pct": 27.3, "last": 12}],
                "losers": [{"symbol": "HOOZ", "pct": -27.1, "last": 8}],
            },
            "breadth": {"advancers": 100, "decliners": 50},
            "sectors": [],
            "trend": {},
        }

        ctx = build_weekly_brief()

        assert ctx["week_label"].startswith("Week of June")
        assert ctx["tape"][0]["symbol"] == "SPY"
        assert ctx["sections"]["major_driver"]
        assert [m["ticker"] for m in ctx["movers"]] == ["NVDA"]
        mock_llm.assert_called_once()

    def test_friday_movers_use_type_filter_and_drop_private_fields(self):
        from weekly_brief_v2 import _friday_session_movers

        with patch("market_internals.get_session_bar_maps") as maps, patch(
            "movers_filter.session_snapshots", return_value=[{"ticker": "NVDA"}]
        ), patch("movers_filter.select_notable_movers") as select:
            maps.return_value = ({"NVDA": {"c": 1}}, {"NVDA": {"c": 1}}, "2026-06-27")
            select.return_value = [
                {
                    "ticker": "NVDA",
                    "pct": "+4.50%",
                    "dir": "up",
                    "why": "AI demand",
                    "companyName": "NVIDIA Corporation",
                    "_price": 100,
                    "_dollarVolume": 1,
                }
            ]
            rows = _friday_session_movers(date(2026, 6, 27))

        assert rows == [
            {
                "ticker": "NVDA",
                "pct": "+4.50%",
                "dir": "up",
                "why": "AI demand",
                "companyName": "NVIDIA Corporation",
            }
        ]
        select.assert_called_once()
        assert select.call_args.kwargs.get("require_news") is True

    def test_av_fallback_drops_leveraged_and_unvetted_names(self):
        from ticker_meta import TickerMeta
        from weekly_brief_v2 import _filter_av_movers

        def meta(ticker, name, typ="CS"):
            return TickerMeta(ticker, name, typ, "stocks", True, "XNAS", 1_000_000_000)

        rows = [
            {"ticker": "HOOG", "pct": "+27.00%", "dir": "up", "why": ""},
            {"ticker": "STXU", "pct": "-20.78%", "dir": "down", "why": ""},
            {"ticker": "STXL", "pct": "-20.69%", "dir": "down", "why": ""},
            {"ticker": "WDCX", "pct": "-20.15%", "dir": "down", "why": ""},
            {"ticker": "NVDA", "pct": "+4.50%", "dir": "up", "why": "AI demand"},
            {"ticker": "AAPL", "pct": "+3.20%", "dir": "up", "why": ""},
            {"ticker": "QBTX", "pct": "-16.00%", "dir": "down", "why": ""},
        ]
        with patch(
            "ticker_meta.get_ticker_meta",
            return_value={
                "HOOG": meta("HOOG", "Leverage Shares 2X Long HOOD Daily ETF", "ETF"),
                "STXU": meta("STXU", "Leverage Shares 2x Long STX Daily ETF", "ETF"),
                "STXL": meta("STXL", "Defiance Daily Target 2X Long STX ETF", "ETF"),
                "WDCX": meta("WDCX", "Tradr 2X Long WDC Daily ETF", "ETF"),
                "NVDA": meta("NVDA", "NVIDIA Corporation"),
                "AAPL": meta("AAPL", "Apple Inc."),
            },
        ):
            kept = _filter_av_movers(rows)

        assert [r["ticker"] for r in kept] == ["NVDA"]
        assert kept[0]["companyName"] == "NVIDIA Corporation"

    def test_sanitize_strips_greeting_signoff_and_rules(self):
        from weekly_brief_v2 import _sanitize_ai_field

        text = (
            "Hey guys, here is your weekly brief.\n\n"
            "Rates held the bid.\n\n"
            "---\n\n"
            "Have a great week guys!"
        )
        assert _sanitize_ai_field(text) == "Rates held the bid."

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=False)
    @patch("weekly_brief_v2.fetch_stock_prices_strict", return_value={})
    def test_aborts_on_missing_prices(self, _mock_prices):
        from weekly_brief_v2 import BriefDataUnavailable, build_weekly_brief

        with pytest.raises(BriefDataUnavailable, match="Missing live price"):
            build_weekly_brief()

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key", "MASSIVE_API_KEY": "x"})
    @patch("weekly_brief_v2._synthesize", return_value="")
    @patch("weekly_brief_v2._fetch_friday_internals", return_value=None)
    @patch("weekly_brief_v2._weekly_pivots", return_value={"pivot": "1", "support": "1", "support2": "1", "resistance": "1", "resistance2": "1"})
    @patch("weekly_brief_v2._weekly_sectors", return_value=[{"name": "Tech", "etf": "XLK", "pct": 1.0}])
    @patch("weekly_brief_v2._weekly_index_change", return_value=None)
    @patch("weekly_brief_v2.fetch_stock_prices_strict")
    @patch("weekly_brief_v2._last_completed_week_range")
    def test_aborts_when_no_weekly_index_returns(
        self,
        mock_week_range,
        mock_prices,
        mock_index_chg,
        mock_sectors,
        mock_pivots,
        mock_internals,
        mock_synth,
    ):
        from weekly_brief_v2 import BriefDataUnavailable, build_weekly_brief

        mock_week_range.return_value = (date(2026, 6, 23), date(2026, 6, 27))
        mock_prices.return_value = {
            "spy": {"current_price": 610.0},
            "qqq": {"current_price": 540.0},
            "iwm": {"current_price": 210.0},
            "vix": {"current_price": 16.5},
        }

        with pytest.raises(BriefDataUnavailable, match="Missing weekly index returns"):
            build_weekly_brief()


class TestTemplateRender:
    def test_render_weekly_brief_html_contains_narrative_sections(self, sample_context):
        from flask import Flask, render_template

        app = Flask(__name__, template_folder=str(SERVER_BACKUP / "templates"))
        with app.app_context():
            html = render_template("email/weekly_brief.html.jinja", **sample_context)
            text = render_template("email/weekly_brief.txt.jinja", **sample_context)

        assert "The Week in Markets" in html
        assert "Last Week in Review" in html
        assert "Sector Scorecard" in html
        assert "SPY" in html
        assert "NVIDIA Corporation" in html
        assert "NVIDIA Corporation" in text
        assert "Weekly Brief" in text
        assert "SECTOR SCORECARD" in text
        assert "Earnings on deck" in html
        assert "NFLX, IBM" in html
        assert "Pending Home Sales" in html
        assert "#f4efe6" in html
        assert "#0b0f17" not in html
        assert "EARNINGS ON DECK" in text


class TestSendWeeklyBriefAdmin:
    @patch("send_weekly_brief.run_weekly_brief")
    def test_blocks_non_sunday_without_force(self, mock_run):
        from pytz import timezone as tz
        from send_weekly_brief import send_weekly_brief_admin

        wednesday = datetime(2026, 8, 26, 10, 0, tzinfo=tz("America/New_York"))
        with patch("send_weekly_brief.datetime") as mock_dt:
            mock_dt.now.return_value = wednesday
            result = send_weekly_brief_admin(force=False)

        assert "not Sunday" in result
        mock_run.assert_not_called()

    @patch("send_weekly_brief.run_weekly_brief", return_value="static/uploads/brief_weekly_latest.html")
    def test_force_sends_on_weekday(self, mock_run):
        from pytz import timezone as tz
        from send_weekly_brief import send_weekly_brief_admin

        wednesday = datetime(2026, 8, 26, 10, 0, tzinfo=tz("America/New_York"))
        with patch("send_weekly_brief.datetime") as mock_dt:
            mock_dt.now.return_value = wednesday
            result = send_weekly_brief_admin(force=True)

        mock_run.assert_called_once_with(skip_confirm=True)
        assert result.endswith("brief_weekly_latest.html")

    @patch("send_weekly_brief.run_weekly_brief", return_value="static/uploads/brief_weekly_latest.html")
    def test_sends_on_sunday(self, mock_run):
        from pytz import timezone as tz
        from send_weekly_brief import send_weekly_brief_admin

        sunday = datetime(2026, 8, 30, 8, 0, tzinfo=tz("America/New_York"))
        with patch("send_weekly_brief.datetime") as mock_dt:
            mock_dt.now.return_value = sunday
            result = send_weekly_brief_admin(force=False)

        mock_run.assert_called_once_with(skip_confirm=True)
        assert result.endswith("brief_weekly_latest.html")


class TestRunWeeklyBriefFailClosed:
    @patch("send_weekly_brief.render_weekly_brief", side_effect=RuntimeError("template boom"))
    @patch("send_weekly_brief.build_weekly_brief")
    def test_render_failure_does_not_fallback(self, mock_build, _mock_render):
        from send_weekly_brief import run_weekly_brief

        mock_build.return_value = {
            "date_range": {"label": "Week of June 23–27, 2026"},
            "data_stale": False,
            "subject": "Options Plunge — Weekly Brief",
        }
        with pytest.raises(RuntimeError, match="template boom"):
            run_weekly_brief(dry_run=True)
