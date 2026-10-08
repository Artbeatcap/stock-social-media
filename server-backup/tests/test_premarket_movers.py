#!/usr/bin/env python3
"""Premarket movers window: field mapping, gates, scoring, and merge."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from movers_filter import (  # noqa: E402
    FUND_ISSUER_PATTERN,
    FUND_NAME_PATTERN,
    is_real_operating_company,
    merge_windows,
    select_premarket_movers,
)
from premarket_movers import (  # noqa: E402
    PREMARKET,
    PREMARKET_CONFIG,
    PREMARKET_DOLLAR_VOLUME_FLOOR,
    PREMARKET_SCORE_DIVISOR,
    PRIOR_SESSION,
    classify_news,
    is_stale,
    market_phase,
    passes_premarket_gate,
    read_window_fields,
    relative_volume,
    score_in_window,
)
from ticker_meta import TickerMeta  # noqa: E402

# Real 08:38 ET premarket payload: `day` is all literal zeros while `min`
# carries the accumulated premarket session.
DKS_PREMARKET = {
    "ticker": "DKS",
    "todaysChangePerc": -1.1423055265063162,
    "updated": 1787747925543267592,
    "day": {"o": 0, "h": 0, "l": 0, "c": 0, "v": 0},
    "lastTrade": {"p": 122.89, "t": 1787747925543267592},
    "min": {"av": 272064, "dav": "272064.601441", "c": 122.90, "n": 74, "t": 1787747880000},
    "prevDay": {"o": 142.36, "h": 146.48, "l": 124.00, "c": 124.31, "v": 38849960},
}

# 08:38 ET on the same day, in epoch ms.
NOW_MS = 1787747925543.0


def meta(ticker="DKS", name="Dick's Sporting Goods, Inc.", cap=9_000_000_000):
    return TickerMeta(
        ticker=ticker,
        name=name,
        type="CS",
        market="stocks",
        active=True,
        primary_exchange="XNYS",
        market_cap=cap,
    )


class TestPremarketFieldMapping(unittest.TestCase):
    def test_zero_day_aggregate_does_not_become_the_price(self):
        """
        Regression for the bug that would have emptied the section every
        morning: premarket `day.c` is 0, not null, so a None-check (or `??`)
        keeps the zero and the ticker fails `price < min_price`.
        """
        fields = read_window_fields(DKS_PREMARKET, PREMARKET)
        self.assertNotEqual(fields["price"], 0.0, "premarket price fell through to day.c == 0")
        self.assertAlmostEqual(fields["price"], 122.89, places=2)

    def test_price_falls_back_to_min_close_without_last_trade(self):
        """This provider returns no lastTrade; min.c must carry the price."""
        snap = dict(DKS_PREMARKET)
        snap.pop("lastTrade")
        fields = read_window_fields(snap, PREMARKET)
        self.assertAlmostEqual(fields["price"], 122.90, places=2)

    def test_volume_reads_min_av_not_day_v(self):
        fields = read_window_fields(DKS_PREMARKET, PREMARKET)
        self.assertEqual(fields["volume"], 272064)
        self.assertGreater(fields["price"] * fields["volume"], 33_000_000)

    def test_prior_session_window_reads_prev_day(self):
        fields = read_window_fields(DKS_PREMARKET, PRIOR_SESSION)
        self.assertAlmostEqual(fields["price"], 124.31, places=2)
        self.assertEqual(fields["volume"], 38849960)


class TestPremarketGate(unittest.TestCase):
    def test_dks_clears_liquidity_but_fails_the_3pct_change_gate(self):
        """
        DKS had $33.4M premarket dollar volume against a $2M floor, so it
        clears liquidity comfortably — but at -1.14% it is below the 3%
        premarket change gate and must not print.
        """
        fields = read_window_fields(DKS_PREMARKET, PREMARKET)
        self.assertGreater(
            fields["price"] * fields["volume"], PREMARKET_CONFIG.min_dollar_volume
        )
        self.assertFalse(is_stale(fields["quote_ms"], NOW_MS))
        self.assertFalse(passes_premarket_gate(DKS_PREMARKET, meta(), NOW_MS))

    def test_same_liquidity_with_a_real_gap_passes(self):
        snap = dict(DKS_PREMARKET, todaysChangePerc=-8.4)
        self.assertTrue(passes_premarket_gate(snap, meta(), NOW_MS))

    def test_thin_print_rejected(self):
        snap = {
            "ticker": "THIN",
            "todaysChangePerc": 12.0,
            "day": {"c": 0, "v": 0},
            "min": {"av": 5000, "c": 8.0, "t": NOW_MS},
            "prevDay": {"c": 7.14, "v": 900_000},
        }
        self.assertFalse(passes_premarket_gate(snap, meta("THIN", "Thin Corp"), NOW_MS))

    def test_stale_quote_rejected(self):
        stale_ms = NOW_MS - 45 * 60_000
        self.assertTrue(is_stale(stale_ms, NOW_MS))
        self.assertFalse(is_stale(NOW_MS - 5 * 60_000, NOW_MS))

    def test_missing_timestamp_is_stale(self):
        self.assertTrue(is_stale(0, NOW_MS))

    def test_halted_stock_with_old_quote_does_not_print(self):
        snap = dict(DKS_PREMARKET)
        snap["min"] = dict(snap["min"], t=NOW_MS - 60 * 60_000)
        snap["updated"] = int((NOW_MS - 60 * 60_000) * 1_000_000)
        self.assertFalse(passes_premarket_gate(snap, meta(), NOW_MS))


class TestWindowScoring(unittest.TestCase):
    def test_never_negative_at_or_below_either_floor(self):
        for window, floor in (
            (PREMARKET, PREMARKET_DOLLAR_VOLUME_FLOOR),
            (PRIOR_SESSION, 10_000_000),
        ):
            at_floor = score_in_window(10.0, floor / 10.0, 5.0, window)
            below = score_in_window(1.0, 1.0, 5.0, window)
            self.assertGreaterEqual(at_floor, 0.0)
            self.assertGreaterEqual(below, 0.0)

    def test_each_window_uses_its_own_divisor(self):
        """
        Identical dollar volume scores higher in the premarket window; that is
        what stops prior-session rows from winning every slot.
        """
        pm = score_in_window(100.0, 200_000, 5.0, PREMARKET)
        ps = score_in_window(100.0, 200_000, 5.0, PRIOR_SESSION)
        self.assertGreater(pm, ps)

    def test_divisor_sits_below_the_eligibility_floor(self):
        """
        If the divisor equalled the floor, the weakest survivor would score ~0
        and ordering in that band would be noise.
        """
        self.assertLess(PREMARKET_SCORE_DIVISOR, PREMARKET_DOLLAR_VOLUME_FLOOR)
        floor_score = score_in_window(10.0, PREMARKET_DOLLAR_VOLUME_FLOOR / 10.0, 1.0, PREMARKET)
        self.assertGreater(floor_score, 0.4)

    def test_no_dead_zone_just_above_the_floor(self):
        """
        A big move on a barely-eligible name must still outrank a small move on
        a somewhat larger one — the inversion that a coupled divisor allows.
        """
        big_move_small_volume = score_in_window(10.0, 600_000, 25.0, PREMARKET)   # $6M
        small_move_bigger_volume = score_in_window(10.0, 900_000, 4.0, PREMARKET)  # $9M
        self.assertGreater(big_move_small_volume, small_move_bigger_volume)

    def test_ordering_is_strict_across_the_surviving_range(self):
        floor = PREMARKET_DOLLAR_VOLUME_FLOOR
        scores = [
            score_in_window(10.0, dv / 10.0, 5.0, PREMARKET)
            for dv in (floor, floor * 1.2, floor * 1.5, floor * 3)
        ]
        self.assertEqual(scores, sorted(scores))
        self.assertTrue(all(b - a > 0.01 for a, b in zip(scores, scores[1:])))


class TestRelativeVolumeGate(unittest.TestCase):
    def test_quiet_morning_on_a_huge_name_is_rejected(self):
        """$6M premarket on a name that trades $2B a day is not an event."""
        snap = dict(DKS_PREMARKET, ticker="MEGA", todaysChangePerc=-4.0)
        snap["min"] = dict(snap["min"], av=50_000)  # 50k x $122.89 = $6.1M
        self.assertTrue(passes_premarket_gate(snap, meta("MEGA"), NOW_MS))
        self.assertFalse(
            passes_premarket_gate(snap, meta("MEGA"), NOW_MS, avg_dollar_volume=2_000_000_000)
        )

    def test_unusual_morning_on_a_small_name_survives(self):
        snap = dict(DKS_PREMARKET, ticker="SMALL", todaysChangePerc=-9.0)
        snap["min"] = dict(snap["min"], av=50_000)  # $6.1M premarket
        self.assertTrue(
            passes_premarket_gate(snap, meta("SMALL"), NOW_MS, avg_dollar_volume=20_000_000)
        )

    def test_unknown_baseline_is_not_disqualifying(self):
        snap = dict(DKS_PREMARKET, todaysChangePerc=-8.4)
        self.assertTrue(passes_premarket_gate(snap, meta(), NOW_MS, avg_dollar_volume=None))

    def test_relative_volume_math(self):
        snap = dict(DKS_PREMARKET)
        ratio = relative_volume(snap, 334_000_000)
        self.assertAlmostEqual(ratio, 0.1, places=2)
        self.assertIsNone(relative_volume(snap, 0))

    def test_raised_floor_rejects_two_million(self):
        """The floor moved 2M -> 3M; a $2.5M print is no longer eligible."""
        snap = dict(DKS_PREMARKET, ticker="THIN2", todaysChangePerc=-7.0)
        snap["min"] = dict(snap["min"], av=20_400)  # ~$2.5M
        self.assertFalse(passes_premarket_gate(snap, meta("THIN2"), NOW_MS))


class TestNameRegexNarrowing(unittest.TestCase):
    def test_real_reits_and_trusts_survive(self):
        for ticker, name in (
            ("DLR", "Digital Realty Trust, Inc."),
            ("NTRS", "Northern Trust Corporation"),
            ("VNO", "Vornado Realty Trust"),
        ):
            self.assertTrue(
                is_real_operating_company(meta(ticker, name)),
                f"{ticker} was silently dropped by the name regex",
            )

    def test_leveraged_products_still_excluded(self):
        for ticker, name in (
            ("HOOG", "Leverage Shares 2X Long HOOD Daily ETF"),
            ("HOOZ", "Defiance Daily Target 2X Short HOOD ETF"),
            ("CONI", "T-Rex 2X Inverse COIN Daily Target ETF"),
            ("SMST", "Defiance Daily Target 2X Short MSTR ETF"),
            ("AAOX", "Tradr 2X Long AAOI Daily ETF"),
            ("AAOZ", "Tradr 2X Short AAOI Daily ETF"),
            ("STXU", "Leverage Shares 2x Long STX Daily ETF"),
            ("STXL", "Defiance Daily Target 2X Long STX ETF"),
            ("WDCX", "Tradr 2X Long WDC Daily ETF"),
        ):
            hit = FUND_NAME_PATTERN.search(name) or FUND_ISSUER_PATTERN.search(name)
            self.assertTrue(hit, f"{ticker} slipped through the backstop")


class TestMergeWindows(unittest.TestCase):
    @staticmethod
    def row(ticker, pct, window, why="News", earnings=False):
        return {
            "ticker": ticker,
            "pct": f"{pct:+.2f}%",
            "dir": "down" if pct < 0 else "up",
            "why": why,
            "companyName": f"{ticker} Inc",
            "reasonUrl": None,
            "window": window,
            "isEarnings": earnings,
        }

    def test_ticker_in_both_windows_yields_one_premarket_row(self):
        pm = [self.row("DKS", -1.14, "premarket")]
        ps = [self.row("DKS", -27.0, "prior_session"), self.row("MRNA", 14.4, "prior_session")]
        merged = merge_windows(pm, ps)
        dks = [r for r in merged if r["ticker"] == "DKS"]
        self.assertEqual(len(dks), 1, "DKS printed twice")
        self.assertEqual(dks[0]["window"], "premarket")
        self.assertEqual(dks[0]["priorSessionPct"], "-27.00%")

    def test_premarket_reserved_slots_are_not_crowded_out(self):
        pm = [self.row(f"PM{i}", 5.0 + i, "premarket") for i in range(3)]
        ps = [self.row(f"PS{i}", -8.0 - i, "prior_session") for i in range(6)]
        merged = merge_windows(pm, ps)
        windows = [r["window"] for r in merged]
        self.assertEqual(windows.count("premarket"), 3)
        self.assertEqual(len(merged), 6)

    def test_premarket_rows_render_first(self):
        pm = [self.row("PM1", 6.0, "premarket")]
        ps = [self.row("PS1", -9.0, "prior_session")]
        merged = merge_windows(pm, ps)
        self.assertEqual(merged[0]["window"], "premarket")

    def test_backfills_from_prior_session_when_premarket_is_short(self):
        mixed = [
            self.row(f"PS{i}", (-8.0 - i) if i % 2 else (8.0 + i), "prior_session")
            for i in range(8)
        ]
        merged = merge_windows([], mixed)
        self.assertEqual(len(merged), 6)

    def test_direction_cap_holds_across_the_merge(self):
        pm = [self.row(f"PM{i}", 5.0 + i, "premarket") for i in range(3)]
        ps = [self.row(f"PS{i}", 8.0 + i, "prior_session") for i in range(6)]
        merged = merge_windows(pm, ps)
        self.assertLessEqual(sum(1 for r in merged if r["dir"] == "up"), 4)

    def test_empty_both_windows_renders_nothing(self):
        self.assertEqual(merge_windows([], []), [])

    def test_telemetry_fields_never_reach_the_published_row(self):
        from movers_filter import _strip_telemetry

        row = self.row("AAA", -8.0, "premarket")
        row.update({"_price": 12.0, "_dollarVolume": 6e6, "_relvol": 0.2, "_adv": 3e7})
        cleaned = _strip_telemetry([row])[0]
        self.assertEqual([k for k in cleaned if k.startswith("_")], [])
        self.assertEqual(cleaned["ticker"], "AAA")
        self.assertEqual(cleaned["window"], "premarket")


class TestEarningsTagging(unittest.TestCase):
    def test_earnings_and_sentiment_from_existing_payload(self):
        article = {
            "title": "DICK'S Sporting Goods cuts guidance",
            "keywords": ["earnings miss", "weak guidance", "Q2 2026 results"],
            "insights": [{"ticker": "DKS", "sentiment": "negative"}],
        }
        tags = classify_news(article, "DKS")
        self.assertTrue(tags["is_earnings"])
        self.assertEqual(tags["sentiment"], "negative")

    def test_non_earnings_news_not_tagged(self):
        article = {
            "title": "Company announces new CEO",
            "keywords": ["leadership", "management change"],
            "insights": [],
        }
        tags = classify_news(article, "XYZ")
        self.assertFalse(tags["is_earnings"])
        self.assertIsNone(tags["sentiment"])


class TestPhaseGuard(unittest.TestCase):
    def test_phase_boundaries(self):
        from datetime import datetime

        import pytz

        ny = pytz.timezone("America/New_York")
        wednesday = lambda h, m: ny.localize(datetime(2026, 8, 26, h, m))  # noqa: E731
        self.assertEqual(market_phase(wednesday(8, 30)), "premarket")
        self.assertEqual(market_phase(wednesday(9, 29)), "premarket")
        self.assertEqual(market_phase(wednesday(10, 0)), "open")
        self.assertEqual(market_phase(wednesday(17, 0)), "closed")
        saturday = ny.localize(datetime(2026, 8, 29, 8, 30))
        self.assertEqual(market_phase(saturday), "closed")


class TestPremarketSelection(unittest.TestCase):
    def test_selects_dks_end_to_end(self):
        rows, stats = select_premarket_movers(
            [DKS_PREMARKET],
            fetch_meta=lambda tickers: {"DKS": meta()},
            enrich=lambda _t: {
                "why": "Q2 EPS miss; FY guidance cut",
                "url": "https://example.com",
                "is_earnings": True,
                "sentiment": "negative",
            },
            now_ms=NOW_MS,
        )
        # -1.14% is below the 3% premarket gate, so DKS should NOT appear.
        self.assertEqual(rows, [])
        self.assertEqual(stats["pmRendered"], 0)

    def test_selects_a_real_premarket_gap(self):
        snap = dict(DKS_PREMARKET, ticker="GAPR", todaysChangePerc=-8.4)
        rows, stats = select_premarket_movers(
            [snap],
            fetch_meta=lambda tickers: {"GAPR": meta("GAPR", "Gapper Inc")},
            enrich=lambda _t: {
                "why": "Q2 EPS miss; FY guidance cut",
                "url": "https://example.com",
                "is_earnings": True,
                "sentiment": "negative",
            },
            now_ms=NOW_MS,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["window"], "premarket")
        self.assertEqual(rows[0]["pct"], "-8.40%")
        self.assertTrue(rows[0]["isEarnings"])
        self.assertEqual(stats["pmRendered"], 1)

    def test_stale_rows_counted_and_dropped(self):
        snap = dict(DKS_PREMARKET, ticker="HALT", todaysChangePerc=-15.0)
        snap["min"] = dict(snap["min"], t=NOW_MS - 90 * 60_000)
        snap["updated"] = int((NOW_MS - 90 * 60_000) * 1_000_000)
        rows, stats = select_premarket_movers(
            [snap],
            fetch_meta=lambda tickers: {"HALT": meta("HALT", "Halted Inc")},
            enrich=lambda _t: {"why": None, "url": None, "is_earnings": False, "sentiment": None},
            now_ms=NOW_MS,
        )
        self.assertEqual(rows, [])
        self.assertEqual(stats["staleRejected"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=1)
