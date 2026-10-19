#!/usr/bin/env python3
"""Unit tests for notable-movers type/liquidity filter. No live API."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from movers_filter import (
    FLOW_DRIVEN_WHY,
    is_real_operating_company,
    passes_quality_gate,
    score_mover,
    select_notable_movers,
)
from ticker_meta import TickerMeta as TM


def meta(
    ticker: str,
    name: str,
    type: str = "CS",
    market: str = "stocks",
    active: bool = True,
    exchange: str = "XNAS",
    cap: float = 2_000_000_000,
) -> TM:
    return TM(ticker, name, type, market, active, exchange, cap)


EXCLUDED = {
    "HOOZ": meta("HOOZ", "Defiance Daily Target 2X Short HOOD ETF", type="ETF"),
    "HOOG": meta("HOOG", "Leverage Shares 2X Long HOOD Daily ETF", type="ETF"),
    "CONI": meta("CONI", "T-Rex 2X Inverse COIN Daily Target ETF", type="ETF"),
    "SMST": meta("SMST", "Defiance Daily Target 2X Short MSTR ETF", type="ETF"),
    "BABX": meta("BABX", "GraniteShares 2X Short BABA Daily ETF", type="ETF"),
    "AAOX": meta("AAOX", "Tradr 2X Long AAOI Daily ETF", type="ETF"),
    "AAOZ": meta("AAOZ", "Tradr 2X Short AAOI Daily ETF", type="ETF"),
    "STXU": meta("STXU", "Leverage Shares 2x Long STX Daily ETF", type="ETF"),
    "STXL": meta("STXL", "Defiance Daily Target 2X Long STX ETF", type="ETF"),
    "WDCX": meta("WDCX", "Tradr 2X Long WDC Daily ETF", type="ETF"),
    "ASTX": meta("ASTX", "Tradr 2X Long ASTS Daily ETF", type="ETF"),
    "QBTX": meta("QBTX", "Tradr 2X Long IONQ Daily ETF", type="ETF"),
    "TJGC": meta("TJGC", "TJGC Corp", cap=12_000_000),
    "CIFU": meta("CIFU", "CIFU Holdings Ltd", cap=8_000_000),
}

RETAINED = {
    "RGNX": meta("RGNX", "REGENXBIO Inc."),
    "NVDA": meta("NVDA", "NVIDIA Corporation", cap=3_000_000_000_000),
    "AAPL": meta("AAPL", "Apple Inc.", cap=3_000_000_000_000),
    "PLTR": meta("PLTR", "Palantir Technologies Inc.", cap=80_000_000_000),
    "SMCI": meta("SMCI", "Super Micro Computer, Inc.", cap=25_000_000_000),
}

ALL_META = {**EXCLUDED, **RETAINED}


def liquid_snap(ticker: str, pct: float, price: float = 25.0, volume: float = 8_000_000) -> dict:
    return {
        "ticker": ticker,
        "todaysChangePerc": pct,
        "day": {"c": price, "v": volume},
        "prevDay": {"v": volume},
        "lastTrade": {"p": price},
    }


class TypeFilterTests(unittest.TestCase):
    def test_excluded_tickers_are_not_operating_companies(self):
        for ticker, m in EXCLUDED.items():
            if ticker in ("TJGC", "CIFU"):
                # Real CS names; liquidity/cap gate drops them, not type.
                self.assertTrue(is_real_operating_company(m), ticker)
                continue
            self.assertFalse(is_real_operating_company(m), ticker)

    def test_name_backstop_catches_mistagged_levered_etf(self):
        mistagged = meta("HOOG", "Leverage Shares 2X Long HOOD Daily ETF", type="CS")
        self.assertFalse(is_real_operating_company(mistagged))

    def test_oct4_weekly_derivatives_are_not_operating_companies(self):
        """STXU/STXL/WDCX are 2X single-stock ETFs, not STX or WDC."""
        cases = (
            ("STXU", "Leverage Shares 2x Long STX Daily ETF"),
            ("STXL", "Defiance Daily Target 2X Long STX ETF"),
            ("WDCX", "Tradr 2X Long WDC Daily ETF"),
        )
        for ticker, name in cases:
            self.assertFalse(is_real_operating_company(meta(ticker, name, type="ETF")), ticker)
            self.assertFalse(is_real_operating_company(meta(ticker, name, type="CS")), ticker)

    def test_retained_are_operating_companies(self):
        for ticker, m in RETAINED.items():
            self.assertTrue(is_real_operating_company(m), ticker)

    def test_microcaps_fail_quality_gate(self):
        snap = liquid_snap("TJGC", 31.0, price=1.2, volume=80_000)
        self.assertFalse(passes_quality_gate(snap, EXCLUDED["TJGC"]))
        snap2 = liquid_snap("CIFU", -17.0, price=2.1, volume=40_000)
        self.assertFalse(passes_quality_gate(snap2, EXCLUDED["CIFU"]))


class RankingTests(unittest.TestCase):
    def test_large_liquid_move_outranks_illiquid_spike(self):
        big = liquid_snap("NVDA", 9.0, price=120.0, volume=20_000_000)  # $2.4B
        small = liquid_snap("RGNX", 30.0, price=12.0, volume=1_000_000)  # $12M
        self.assertGreater(score_mover(big), score_mover(small))
        self.assertGreater(score_mover(big), 8.0)

    def test_mirror_pair_never_both_survive(self):
        snaps = [
            liquid_snap("HOOG", 27.29),
            liquid_snap("HOOZ", -27.33),
            liquid_snap("RGNX", -24.86),
        ]

        def fetch_meta(tickers):
            return {t: ALL_META[t] for t in tickers if t in ALL_META}

        rows = select_notable_movers(
            snaps,
            fetch_meta=fetch_meta,
            split_check=lambda *_: False,
            enrich=lambda *_: (None, None),
            enrich_news=False,
        )
        tickers = {r["ticker"] for r in rows}
        self.assertNotIn("HOOG", tickers)
        self.assertNotIn("HOOZ", tickers)
        self.assertTrue("RGNX" in tickers or len(rows) == 0)

    def test_omit_section_when_zero_survive(self):
        snaps = [liquid_snap(t, 20.0) for t in ("HOOG", "HOOZ", "AAOX", "AAOZ")]

        def fetch_meta(tickers):
            return {t: ALL_META[t] for t in tickers if t in ALL_META}

        rows = select_notable_movers(
            snaps,
            fetch_meta=fetch_meta,
            split_check=lambda *_: False,
            enrich=lambda *_: (None, None),
            enrich_news=False,
        )
        self.assertEqual(rows, [])

    def test_reserves_slots_for_raw_move_catalysts(self):
        """Mega-caps must not crowd out a genuine single-stock catalyst."""
        # The mega-caps have to genuinely outscore the catalyst on liquidity
        # weighting, otherwise the reserved slots are never exercised and this
        # test passes for the wrong reason.
        megacaps = [
            liquid_snap(t, 12.0 + i * 0.1, price=200.0, volume=40_000_000)
            for i, t in enumerate(("NVDA", "AAPL", "PLTR", "SMCI"))
        ]
        catalyst = liquid_snap("RGNX", -24.86, price=12.0, volume=1_000_000)
        self.assertGreater(score_mover(megacaps[-1]), score_mover(catalyst))

        def fetch_meta(tickers):
            return {t: ALL_META[t] for t in tickers if t in ALL_META}

        rows = select_notable_movers(
            megacaps + [catalyst],
            fetch_meta=fetch_meta,
            split_check=lambda *_: False,
            enrich=lambda *_: (None, None),
            enrich_news=False,
        )
        tickers = [r["ticker"] for r in rows]
        self.assertIn("RGNX", tickers)
        self.assertLessEqual(len([t for t in tickers if t != "RGNX"]), 4)

    def test_lower_divisor_lets_a_catalyst_win_on_merit(self):
        """
        Regression for the divisor decoupling. Dividing by the $10M eligibility
        gate compressed a 25% move on $24M below a 5% drift on $8B; dividing
        below the gate lets the catalyst rank on its own.
        """
        megacap = liquid_snap("NVDA", 5.0, price=200.0, volume=40_000_000)
        catalyst = liquid_snap("RGNX", -24.86, price=12.0, volume=2_000_000)
        self.assertGreater(score_mover(catalyst), score_mover(megacap))

    def test_score_never_negative_below_dollar_volume_gate(self):
        thin = liquid_snap("RGNX", 30.0, price=1.0, volume=1_000)
        self.assertGreater(score_mover(thin), 0.0)
        bigger_thin = liquid_snap("RGNX", 60.0, price=1.0, volume=1_000)
        self.assertGreater(score_mover(bigger_thin), score_mover(thin))

    def test_does_not_pad_with_junk_to_hit_six(self):
        snaps = [
            liquid_snap("NVDA", 8.0, price=120.0, volume=25_000_000),
            liquid_snap("AAPL", -6.5, price=180.0, volume=20_000_000),
            liquid_snap("HOOG", 27.0),
            liquid_snap("HOOZ", -27.0),
            liquid_snap("AAOX", -27.0),
            liquid_snap("TJGC", 31.0, price=1.1, volume=50_000),
        ]

        def fetch_meta(tickers):
            return {t: ALL_META[t] for t in tickers if t in ALL_META}

        rows = select_notable_movers(
            snaps,
            fetch_meta=fetch_meta,
            split_check=lambda *_: False,
            enrich=lambda *_: (None, None),
            enrich_news=False,
        )
        tickers = [r["ticker"] for r in rows]
        self.assertEqual(set(tickers), {"NVDA", "AAPL"})
        self.assertTrue(all(r["why"] == FLOW_DRIVEN_WHY for r in rows))

    def test_require_news_skips_a_move_with_no_headline(self):
        snaps = [
            liquid_snap("NVDA", 9.0, price=120.0, volume=20_000_000),
            liquid_snap("AAPL", 5.0, price=180.0, volume=15_000_000),
        ]

        def fetch_meta(tickers):
            return {t: ALL_META[t] for t in tickers if t in ALL_META}

        def enrich(ticker):
            if ticker == "AAPL":
                return ("iPhone demand lifts the quarter", "https://example.com/aapl")
            return (None, None)

        rows = select_notable_movers(
            snaps,
            fetch_meta=fetch_meta,
            split_check=lambda *_: False,
            enrich=enrich,
            require_news=True,
        )
        self.assertEqual([r["ticker"] for r in rows], ["AAPL"])
        self.assertEqual(rows[0]["why"], "iPhone demand lifts the quarter")


if __name__ == "__main__":
    unittest.main()
