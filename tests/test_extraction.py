"""Offline regressions for exhibit selection and SEC release layouts."""
import unittest
import json
from unittest.mock import patch

import season


class ExtractionTests(unittest.TestCase):
    def test_filing_type_wins_over_a_presentation_filename(self):
        rel = {"base": "https://www.sec.gov/Archives/edgar/data/886982/000088698226000529",
               "accession": "0000886982-26-000529"}
        index = '<tr><td><a href="/Archives/edgar/data/886982/000088698226000529/a3q26gsearningsresults.htm">Release</a></td><td>EX-99.1</td></tr>'
        directory = '{"directory":{"item":[{"name":"a3q26gsearningsresultspr.htm"},{"name":"a3q26gsearningsresults.htm"}]}}'
        with patch.object(season, "fetch", side_effect=lambda url: directory if url.endswith("index.json") else index):
            self.assertEqual(season.ex99_url(rel), rel["base"] + "/a3q26gsearningsresults.htm")

    def test_revenue_in_separate_year_tables(self):
        # NEE Q3 2026 EX-99: company totals are the last column, not FPL's first column.
        current = "Operating Revenues | $ | 5,421 | $ | 2,516 | $ | 153 | $ | 8,090"
        prior = "Operating Revenues | $ | 5,285 | $ | 2,566 | $ | 115 | $ | 7,966"
        doc = "Three Months Ended September 30, 2026\n" + current + "\nThree Months Ended September 30, 2025\n" + prior
        field = {"label": "Operating Revenues", "current": "8,090", "prior": "7,966",
                 "current_row": current, "prior_row": prior}
        pair, status = season.verified_pair(doc, field, 1_000_000)
        self.assertEqual(status, "ok")
        self.assertEqual((pair["current"], pair["prior"]), (8_090_000_000, 7_966_000_000))
        self.assertIsNone(season.verified_pair(doc, {**field, "prior": "8,090"})[0])

    def test_consolidated_narrative_values(self):
        quote = "Revenues of $32.3 billion, up 5.3% year over year from $30.7 billion"
        field = {"label": "Revenues", "row": quote, "current": "32.3", "prior": "30.7"}
        pair, status = season.verified_pair(quote, field, 1_000_000_000)
        self.assertEqual(status, "ok")
        self.assertEqual(pair["current"], 32_300_000_000)

    def test_an_invented_row_is_not_rescued_by_nearby_numbers(self):
        field = {"label": "Revenue", "row": "Revenue 1,234 1,100", "current": "1,234", "prior": "1,100"}
        self.assertIsNone(season.verified_pair("Revenue: segment A 1,234; expenses 1,100", field)[0])

    def test_sign_must_match_the_quote(self):
        field = {"label": "Diluted EPS", "row": "Diluted EPS | (1.20) | 1.10", "current": "1.20", "prior": "1.10"}
        self.assertIsNone(season.verified_pair(field["row"], field)[0])

    def test_recast_comparison_is_checked_but_not_mixed_into_acceleration(self):
        observations = [
            {"start": "2025-07-01", "end": "2025-09-30", "val": 10_408_000_000, "filed": "2025-10-23", "accn": "original"},
            {"start": "2025-07-01", "end": "2025-09-30", "val": 4_929_000_000, "filed": "2026-10-22", "accn": "recast"},
            {"start": "2025-04-01", "end": "2025-06-30", "val": 9_000_000_000},
            {"start": "2026-04-01", "end": "2026-06-30", "val": 9_500_000_000},
        ]
        facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": observations}}}}}
        with patch.object(season, "fetch", return_value=json.dumps(facts)):
            result = season.xbrl_context("0000773840", "2026-09-30", 4_929_000_000)
        self.assertEqual(result["tie_err"], 0)
        self.assertEqual(result["original_prior"], 10_408_000_000)
        self.assertEqual(result["comparison_accession"], "recast")
        self.assertTrue(result["comparison_revised"])
        self.assertIsNone(result["prev_q_growth"])

    def test_utility_total_revenue_concept_is_supported(self):
        facts = {"facts": {"us-gaap": {"RegulatedAndUnregulatedOperatingRevenue": {"units": {"USD": [
            {"start": "2025-07-01", "end": "2025-09-30", "val": 7_966_000_000, "filed": "2025-10-28"}
        ]}}}}}
        with patch.object(season, "fetch", return_value=json.dumps(facts)):
            result = season.xbrl_context("0000753308", "2026-09-30", 7_966_000_000)
        self.assertEqual(result["tie_err"], 0)

    def test_cached_values_need_exact_evidence_and_unit(self):
        field = {"row": "Net sales | 4,994 | 4,929", "label": "Net sales", "current": 4_994_000_000, "prior": 4_929_000_000}
        self.assertEqual(season.recheck_cached(field["row"], field, [1e6])["current"], 4_994_000_000)
        self.assertIsNone(season.recheck_cached(field["row"], field, [1e9]))
        self.assertIsNone(season.recheck_cached("Net sales | 5,000 | 4,929", field, [1e6]))

    def test_unchanged_filing_rechecks_fresh_xbrl_without_a_model_call(self):
        rel = {"accession": "same", "filed": "2026-10-22", "base": "https://example.test/filing"}
        old = {"ticker": "HON", "accession": "same", "source_url": rel["base"] + "/release.htm", "period_end": "2026-09-30",
               "revenue": {"label": "Net sales", "row": "Net sales | 4,994 | 4,929", "current": 4_994_000_000, "prior": 4_929_000_000},
               "eps": {"label": "Diluted EPS", "row": "Diluted EPS | 1.03 | 1.15", "current": 1.03, "prior": 1.15}}
        doc = "In millions\nNet sales | 4,994 | 4,929\nDiluted EPS | 1.03 | 1.15"
        with patch.object(season, "releases", return_value=[rel]), patch.object(season, "ex99_url", return_value=old["source_url"]), \
             patch.object(season, "fetch", return_value=doc), patch.object(season, "market", return_value={}), \
             patch.object(season, "xbrl_context", return_value={"tie_err": 0, "comparison_revised": True}) as xbrl, \
             patch.object(season, "gemini", side_effect=AssertionError("unnecessary model call")):
            result = season.company("HON", {"HON": ("0000773840", "Honeywell")}, old)
        xbrl.assert_called_once_with("0000773840", "2026-09-30", 4_929_000_000)
        self.assertEqual(result["xbrl"]["tie_err"], 0)


if __name__ == "__main__":
    unittest.main()
