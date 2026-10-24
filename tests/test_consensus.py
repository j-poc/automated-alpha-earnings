"""A segment actual must not silently become a consolidated revenue surprise."""
import copy
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import season


class ConsensusTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = json.loads((season.DATA / "season.json").read_text())
        self.meta = next(r for r in self.snapshot["companies"] if r["ticker"] == "META")

    def export_row(self, row):
        with tempfile.TemporaryDirectory() as target, patch.object(season, "DATA", Path(target)), patch.object(season, "logo", return_value=None):
            result = season.export({"generated_at": self.snapshot["generated_at"], "companies": [row]})
        return result["rows"][0]

    def test_export_uses_total_revenue_and_labels_adjusted_eps(self):
        row = self.export_row(copy.deepcopy(self.meta))
        expected = float((Decimal("64601000000") - Decimal("64635778699")) / Decimal("64635778699"))
        self.assertAlmostEqual(row["rev_surprise"], expected, places=14)
        self.assertAlmostEqual(row["eps_surprise"], float(Decimal("7.40") / Decimal("5.7889") - 1), places=14)
        self.assertEqual(row["eps_basis"], "adjusted")
        self.assertEqual(row["eps"], 3.83)  # GAAP EPS itself is not replaced by adjusted EPS.
        self.assertEqual(self.meta["market"]["rev_act"], 63061000000)  # Preserve the observed provider field.

    def test_changed_source_or_actual_cannot_reuse_review(self):
        for change in ("source_url", "provider_actual", "reported_revenue", "eps_actual"):
            with self.subTest(change=change):
                row = copy.deepcopy(self.meta)
                if change == "source_url":
                    row["source_url"] += "?different-filing"
                elif change == "provider_actual":
                    row["market"]["rev_act"] += 1
                elif change == "reported_revenue":
                    row["revenue"]["current"] += 1
                else:
                    row["market"]["eps_act"] = 8
                published = self.export_row(row)
                self.assertIsNone(published["rev_surprise"])
                self.assertIsNone(published["eps_surprise"])
                self.assertEqual(published["eps_basis"], "needs review")

    def test_no_estimate_produces_no_comparison(self):
        row = copy.deepcopy(self.meta)
        row["market"].update(rev_est=0, eps_est=None)
        published = self.export_row(row)
        self.assertIsNone(published["rev_surprise"])
        self.assertIsNone(published["eps_surprise"])


if __name__ == "__main__":
    unittest.main()
