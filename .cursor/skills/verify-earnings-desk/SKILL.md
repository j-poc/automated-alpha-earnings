---
name: verify-earnings-desk
description: Verify the Automated Alpha earnings CLI, saved SEC snapshot, and rendered browser dashboard after extraction or comparison changes.
---

## Launch

From this repo, run `python3 test_season.py` and `python3 season.py render`.
The checked-in `data/season.json` supplies real seed data, so this path needs no API keys. Missing cached logos are optional.
For a fresh data directory, run `python3 season.py build` with `GEMINI_API_KEY` and `FINNHUB_API_KEY`; it creates the snapshot before rendering. This performs live provider calls and can take minutes. Do not print credentials.

Start `python3 -m http.server 8768 --bind 127.0.0.1 --directory out` in a tracked shell session. Ready means the console says `Serving HTTP` and `/season.html` displays the seeded company rows. Do not start a second instance on the same port or run concurrent builds in this checkout.

## Doctor

Run `python3 season.py check`. It lists accepted and withheld results and requires Yahoo prices on at least 80% of the saved universe. A successful check does not mean every company passed extraction; read the HOLD lines. Confirm `out/season.html` exists after render.

## Drive

Use the supported in-app browser tools at `http://127.0.0.1:8768/season.html`.
Select a real company with `#rep-table tbody tr[data-key="HON"]` (after a fresh October 23 build). Verify `#d-title` names Honeywell, `#detail` shows the changed-comparison note, and its source link goes to SEC EDGAR. Do not set application state through evaluation. See [features](features/README.md) for other journeys.

Select `#rep-table tbody tr[data-key="META"]` in the saved October 24 snapshot. Revenue surprise must be +2.0%, adjusted EPS surprise +13.1%, and the two-session move −7.9%. The price window must show October 21 close $697.52 to October 23 close $642.62. The basis note must distinguish adjusted EPS from GAAP growth and name the dated consensus source. Netflix is inline on revenue and −2.4% on the correct October 20–22 closing-price window. Citigroup is +0.4% on revenue, +7.1% on EPS and −7.3% on its October 12–14 window.

Confirm the pulse coverage explicitly says revenue 30/31 and EPS 31/31, with one inline result. The revenue split must not count inline as a miss. UnitedHealth’s revenue surprise stays unavailable, with a reason. GE is included, and selecting it distinguishes adjusted revenue from total-revenue growth. Historical options comparisons show the actual number of sampled reports.

## Evidence

Save command output and a screenshot of the selected detail panel under `post/output/verification/`. Capture the click and resulting title/detail, plus the generated file and command exit codes. Run the offline parser regressions against quoted real layouts; the browser proof uses the real saved snapshot, not mocked rows. Keep unresolved HOLD entries visible in the evidence.

## Cleanup

Stop only the HTTP server session started for this verification. Close its temporary browser tab. Keep `post/output/verification/` and confirm its files still exist after cleanup. Never kill a process by name.

## Helpers

`python3 test_season.py` is the single verification command. It runs the extraction regression suite and saved-snapshot checks. `python3 -m unittest discover -s tests -v` isolates the offline regressions.
