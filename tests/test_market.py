"""A partial daily candle is not a completed earnings reaction."""
import unittest
from datetime import datetime
from unittest.mock import patch

import yahoo


class MarketTests(unittest.TestCase):
    def result_at(self, hour, session_end_hour=16):
        now = datetime(2026, 10, 23, hour, tzinfo=yahoo.ET)
        stamp = lambda day: int(datetime(2026, 10, day, 12, tzinfo=yahoo.ET).timestamp())
        qs = {"earnings": {"earningsChart": {"quarterly": [
            {"actual": {"raw": 1}, "reportedDate": {"raw": stamp(22)}, "surprisePct": 5}
        ]}}}
        chart = {"timestamp": [stamp(21), stamp(22), stamp(23)],
                 "indicators": {"quote": [{"close": [100, 102, 105]}]},
                 "meta": {"currentTradingPeriod": {"regular": {
                     "end": int(now.replace(hour=session_end_hour).timestamp())}}}}
        with patch.object(yahoo, "datetime", wraps=datetime) as clock:
            clock.now.return_value = now
            return yahoo.reaction(qs, chart)

    def test_second_session_stays_unavailable_during_market_hours(self):
        self.assertEqual(self.result_at(13)["quarters"], [])

    def test_second_session_is_available_after_the_close(self):
        self.assertEqual(self.result_at(17)["quarters"][0]["r2"], 5.0)

    def test_exchange_supplied_early_close_is_respected(self):
        self.assertEqual(self.result_at(14, session_end_hour=13)["quarters"][0]["r2"], 5.0)


if __name__ == "__main__":
    unittest.main()
