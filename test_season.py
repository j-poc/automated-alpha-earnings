"""Done condition: python3 test_season.py exits 0 (parsers hold, withheld rows have a reason, market data reached the rows)."""

import season
import unittest

regressions = unittest.defaultTestLoader.discover(str(season.ROOT / "tests"))
assert unittest.TextTestRunner(verbosity=1).run(regressions).wasSuccessful(), "extraction regressions failed"

assert season.in_order("Revenue | 1,234 | 1,100", "1,234", "1,100")
assert not season.in_order("Revenue | 1,100 | 1,234", "1,234", "1,100")
assert not season.in_order("Revenue | 11,234 | 1,100", "1,234", "1,100")  # whole tokens only
assert season.norm("Net revenue​ – reported ....... ") == season.norm("Net revenue - reported")
assert str(season.shift("2026-09-05", -12)) == "2025-09-05"

data = season.export()
rows = {r["ticker"]: r for r in data["rows"]}
assert all(r["status"] in {"reported", "withheld", "upcoming", "not_earnings"} for r in rows.values())
assert all(r["reason"] for r in rows.values() if r["status"] == "withheld")
assert all(r["yoy"] is not None for r in rows.values() if r["status"] == "reported")
assert season.check(), "market data (Yahoo price) missing for more than 20% of companies"
bad = {"filed": "x", "revenue": {"current": 1.1e9, "prior": 1e9}, "xbrl": {"tie_err": 0, "xbrl_prior": 1e9},
       "market": {"finnhub_rev_act": 1e9}}
assert season.hold_reason(bad) and not season.hold_reason({**bad, "market": {"finnhub_rev_act": 1.08e9}})
print("ok")

import verify_snapshot
assert verify_snapshot.verify()
