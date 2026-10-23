# Automated Alpha: Q3 2026 earnings

Live: https://automatedalpha-earnings.vercel.app

`python3 season.py build` pulls each 8-K press release from SEC EDGAR, extracts revenue and EPS with Gemini, ties last year's revenue to the original XBRL filing (0.5%) and this quarter's to Finnhub (5%), withholds anything that misses with the reason, and renders `out/season.html`. Consensus comes from Finnhub; implied moves and price reactions come from Yahoo (`yahoo.py`). `python3 test_season.py` is the done condition.

Needs `FINNHUB_API_KEY` and `GEMINI_API_KEY`. `.github/workflows/refresh.yml` rebuilds and redeploys three times every weekday.
