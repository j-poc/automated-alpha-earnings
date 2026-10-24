# Automated Alpha: Q3 2026 earnings

Live: https://automatedalpha-earnings.vercel.app

`python3 season.py build` pulls each 8-K press release from SEC EDGAR, extracts revenue and EPS with Gemini, ties last year's revenue to available XBRL history (0.5%) and this quarter's to Finnhub (5%), and renders `out/season.html`. It checks the filing's exhibit type before filename guesses, accepts exact quotes from separate year tables or narrative, and refreshes XBRL data every 30 minutes. A revised historical comparison suppresses the acceleration calculation until its bases are reconciled. Results that fail checks remain withheld with a reason; that is a pipeline limitation, not evidence of a company error. Consensus comes from Finnhub; implied moves and price reactions come from Yahoo (`yahoo.py`).

`python3 test_season.py` is the verification command: offline extraction regressions plus checks on the saved real snapshot. See `.cursor/skills/verify-earnings-desk/SKILL.md` for the browser walkthrough and evidence capture.

Needs `FINNHUB_API_KEY` and `GEMINI_API_KEY`. Stock reactions use completed daily candles only; a partial second trading session is not a two-day result. The dashboard displays its snapshot time in New York time.

The saved Meta Q3 comparison has a source-bound reconciliation: Finnhub's revenue actual is advertising revenue, so the total-revenue surprise uses the release's consolidated total instead. Its EPS surprise uses the release's adjusted EPS and is labelled accordingly. Raw provider actuals remain in the snapshot. The review applies only to the same source and observed actuals; changed inputs suppress the affected comparisons for review.

`.github/workflows/refresh.yml` schedules three weekday refreshes. Each run builds and verifies, deploys, then commits the deployed snapshot. GitHub needs a durable `VERCEL_TOKEN` scoped to the Evoa workspace and earnings project. A short-lived CLI OAuth access token is unsuitable for scheduled jobs. Project and workspace IDs are fixed in the workflow; `VERCEL_PROJECT_ID` and `VERCEL_ORG_ID` can override the CLI defaults.
