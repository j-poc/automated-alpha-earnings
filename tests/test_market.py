"""Observable return windows, including after-close, weekends and unfinished candles."""
import unittest
from datetime import datetime
from unittest.mock import patch
import yahoo


class MarketTests(unittest.TestCase):
    def result(self, report_day=22, report_hour=8, now_hour=17, session_end=16):
        stamp = lambda d, h: int(datetime(2026, 10, d, h, tzinfo=yahoo.ET).timestamp())
        qs = {'earnings': {'earningsChart': {'quarterly': [
            {'actual': {'raw': 1}, 'reportedDate': {'raw': stamp(report_day, report_hour)}}]}}}
        chart = {'timestamp': [stamp(d, 12) for d in (21, 22, 23, 26, 27, 28, 29, 30)],
                 'indicators': {'quote': [{'close': [100, 102, 105, 110, 115, 120, 125, 130]}]},
                 'meta': {'currentTradingPeriod': {'regular': {'end': stamp(23, session_end)}}}}
        now = datetime(2026, 10, 23, now_hour, tzinfo=yahoo.ET)
        with patch.object(yahoo, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = now
            return yahoo.reaction(qs, chart)

    def test_before_open_uses_previous_close_and_two_sessions(self):
        r = self.result()['quarters'][0]
        self.assertEqual((r['baseline_date'], r['end_date'], r['r2']), ('2026-10-21', '2026-10-23', 5.0))

    def test_after_close_waits_for_next_two_sessions(self):
        self.assertEqual(self.result(report_hour=16)['quarters'], [])

    def test_partial_session_not_a_close(self):
        self.assertEqual(self.result(now_hour=13)['quarters'], [])
        self.assertEqual(self.result(now_hour=14, session_end=13)['quarters'][0]['r2'], 5.0)

    def test_date_only_and_during_market_are_not_assumed_before_open(self):
        for hour in (0, 12): self.assertEqual(self.result(report_hour=hour)['quarters'], [])

    def test_after_close_friday_skips_weekend_and_five_sessions_are_five(self):
        stamp = lambda d, h: int(datetime(2026, 10, d, h, tzinfo=yahoo.ET).timestamp())
        q = {'earnings': {'earningsChart': {'quarterly': [{'actual': {'raw': 1}, 'reportedDate': {'raw': stamp(23, 16)}}]}}}
        c = {'timestamp': [stamp(d, 12) for d in (22, 23, 26, 27, 28, 29, 30)], 'indicators': {'quote': [{'close': [90, 100, 102, 105, 110, 115, 120]}]}}
        with patch.object(yahoo, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 10, 31, 12, tzinfo=yahoo.ET)
            r = yahoo.reaction(q, c)['quarters'][0]
        self.assertEqual((r['baseline_date'], r['end_date'], r['r2'], r['r5_end_date'], r['r5']),
                         ('2026-10-23', '2026-10-27', 5.0, '2026-10-30', 20.0))

    def test_midnight_utc_date_only_is_not_previous_day_after_close(self):
        q = {'earnings': {'earningsChart': {'quarterly': [{'actual': {'raw':1}, 'reportedDate': {'raw':1792627200}}]}}}
        self.assertEqual(yahoo.reaction(q, {'timestamp':[], 'indicators':{'quote':[{'close':[]}]}})['quarters'], [])

if __name__ == '__main__': unittest.main()
