import unittest
from datetime import date, datetime

import pytz

from catalyst_audit_lib import is_recall_hit, token_overlap
from validation_telemetry import audit_as_of_for_trade_date, resolve_audit_trade_date

NY = pytz.timezone("America/New_York")


class CatalystAuditLibTests(unittest.TestCase):
    def test_token_overlap_hit(self):
        headline = "Company reports strong quarterly earnings beat"
        self.assertGreaterEqual(token_overlap(headline, headline), 0.35)

    def test_recall_miss_on_empty_pipeline(self):
        self.assertFalse(is_recall_hit("", "Some headline", ["Some headline"]))

    def test_resolve_trade_date_before_featured_window(self):
        now = NY.localize(datetime(2026, 10, 23, 9, 0))
        self.assertEqual(resolve_audit_trade_date(now), date(2026, 10, 22))

    def test_audit_as_of_capped_before_recall_time(self):
        trade = date(2026, 10, 23)
        now = NY.localize(datetime(2026, 10, 23, 9, 0))
        self.assertEqual(audit_as_of_for_trade_date(trade, now), now)


if __name__ == "__main__":
    unittest.main()
