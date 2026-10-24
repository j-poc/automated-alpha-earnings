# Build and validate results

## Sub-features

Find the authoritative release, copy source-grounded values, reconcile XBRL, and render the season snapshot.

## How to get to it (user POV)

Run `python3 season.py build`, then open `out/season.html`.

## Driving it with CLI

Set the two API keys without printing them. Record the start time, run build, and record its exit code. Then run `python3 test_season.py`. Verify that `generated_at` advanced in both `data/season.json` and `data/dashboard.json` and that `out/season.html` was regenerated. Read every remaining HOLD reason and any `finnhub_error` or `yahoo_error` fields.

## Gotchas

Build calls external providers and updates the snapshot. Old article screenshots describe their historical date; a fresh build will differ. Never deploy as part of verification.

## Automatic refresh credentials

The GitHub `refresh` workflow checks the exact Evoa workspace/project before building. Account-scoped tokens must also match `evoa3`; project-only tokens may return 403/404 for the account profile and still pass the exact workspace/project check. `scripts/check_vercel_access.py` must pass with the Actions `VERCEL_TOKEN`; a local CLI login does not establish scheduled access. After a secret change, dispatch the workflow once, confirm its access check, deployment and snapshot commit succeed, then check the live dashboard timestamp against that commit. Keep all credential values out of output and proof files.
