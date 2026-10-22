import unittest
from datetime import date, datetime

import pytz

from scripts.audit_common import (
    audit_as_of_for_trade_date,
    catalyst_hit,
    effective_audit_trade_date,
    token_overlap,
)

NY = pytz.timezone("America/New_York")


class AuditCommonTests(unittest.TestCase):
    def test_token_overlap_hit(self):
        a = "AMD beats earnings expectations, shares surge"
        b = "AMD beats Q3 earnings expectations; stock surges"
        self.assertGreaterEqual(token_overlap(a, b), 0.35)

    def test_catalyst_hit(self):
        pipeline = "NVIDIA raises guidance on AI demand"
        headlines = [{"headline": "NVIDIA raises full-year guidance on strong AI chip demand"}]
        hit, _ = catalyst_hit(pipeline, headlines)
        self.assertTrue(hit)

    def test_weekend_trade_date_friday(self):
        saturday = NY.localize(datetime(2026, 10, 11, 10, 0))
        self.assertEqual(effective_audit_trade_date(saturday), date(2026, 10, 9))

    def test_audit_as_of_caps_before_recall_window(self):
        trade = date(2026, 10, 12)
        morning = NY.localize(datetime(2026, 10, 12, 9, 0))
        capped = audit_as_of_for_trade_date(trade, morning)
        self.assertEqual(capped, morning)


if __name__ == "__main__":
    unittest.main()
