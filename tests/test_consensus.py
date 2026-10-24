"""A source must match the release date and reporting basis before comparison."""
import unittest
import consensus

HTML = '''<table id="earnings-history"><tbody><tr><td>10/21/2026</td><td>Q3 2026</td>
<td>$6.54</td><td>$7.40</td><td>+$0.86</td><td>$3.83</td><td>$63.34B</td><td>$64.60B</td>
<td><a href="/earnings/reports/meta/">Details</a></td></tr></tbody></table>'''

class ConsensusTests(unittest.TestCase):
    def setUp(self):
        self.record = consensus.parse(HTML, 'https://www.marketbeat.com/stocks/NASDAQ/META/earnings/', '2026-10-24')[0]
        self.row = dict(filed='2026-10-21', period_end='2026-09-30', fiscal_label='third quarter 2026', source_url='https://issuer/release', revenue={'current':64601000000}, eps={'current':3.83},
                        consensus_eps_observation=dict(source_url='https://issuer/release',period_end='2026-09-30',current_actual=7.4,basis='adjusted',source_quote='Adjusted EPS $7.40'))

    def test_total_revenue_and_adjusted_eps_not_advertising_or_gaap_mix(self):
        r = consensus.compare(self.row, self.record)
        self.assertAlmostEqual(r['rev_surprise'] * 100, 1.990843069, places=6)
        self.assertAlmostEqual(r['eps_surprise'] * 100, 13.149847095, places=6)
        self.assertEqual(r['eps_basis'], 'adjusted')

    def test_wrong_date_and_different_basis_cannot_publish_surprise(self):
        self.assertEqual(consensus.compare({**self.row, 'filed':'2026-10-20'}, self.record), {})
        self.assertEqual(consensus.compare(self.row, {**self.record, 'quarter':'Q1 2026'}), {})
        r = consensus.compare({**self.row, 'revenue': {'current':63061000000}, 'eps': {'current':7.4}}, self.record)
        self.assertNotIn('rev_surprise', r)
        self.assertNotIn('eps_surprise', consensus.compare(self.row, {**self.record, 'eps_act':999}))

    def test_rounded_estimate_is_inline_and_missing_estimate_is_missing(self):
        # Actual must also reconcile to source actual; use a fully consistent rounded observation.
        rec = {**self.record, 'rev_act':63340000000}
        r = consensus.compare({**self.row, 'revenue': {'current':63342000000}}, rec)
        self.assertEqual((r['rev_surprise'],r['rev_result']), (0.0,'inline'))
        r = consensus.compare(self.row, {**self.record, 'rev_est':None, 'eps_est':0})
        self.assertNotIn('rev_surprise', r); self.assertNotIn('eps_surprise', r)

if __name__ == '__main__': unittest.main()
