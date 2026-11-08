import unittest
from datetime import date, datetime

import pytz

from scripts.catalyst_audit_common import (
    NY,
    resolve_audit_trade_date,
    token_overlap,
)

NY_TZ = pytz.timezone("America/New_York")


class CatalystAuditCommonTests(unittest.TestCase):
    def test_token_overlap_hit(self):
        a = "Acme wins FDA approval for new drug"
        b = "FDA approves Acme's new drug candidate"
        self.assertGreaterEqual(token_overlap(a, b), 0.35)

    def test_resolve_trade_date_before_postmarket(self):
        # Monday 9am ET -> prior Friday session
        now = NY_TZ.localize(datetime(2026, 10, 26, 9, 0))
        self.assertEqual(resolve_audit_trade_date(now), date(2026, 10, 23))

    def test_resolve_trade_date_after_postmarket(self):
        now = NY_TZ.localize(datetime(2026, 10, 26, 18, 0))
        self.assertEqual(resolve_audit_trade_date(now), date(2026, 10, 26))


if __name__ == "__main__":
    unittest.main()
