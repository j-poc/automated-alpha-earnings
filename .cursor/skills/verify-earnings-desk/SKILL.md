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

Select `#rep-table tbody tr[data-key="META"]` in the saved October 23 snapshot. Revenue surprise must be -0.05%, and EPS surprise must explicitly say adjusted (+27.8%). Its basis note must distinguish total revenue from advertising revenue and adjusted EPS from reported diluted EPS. The offline suite also checks that changed source/actual inputs cannot reuse that review.

## Evidence

Save command output and a screenshot of the selected detail panel under `post/output/verification/`. Capture the click and resulting title/detail, plus the generated file and command exit codes. Run the offline parser regressions against quoted real layouts; the browser proof uses the real saved snapshot, not mocked rows. Keep unresolved HOLD entries visible in the evidence.

## Cleanup

Stop only the HTTP server session started for this verification. Close its temporary browser tab. Keep `post/output/verification/` and confirm its files still exist after cleanup. Never kill a process by name.

## Helpers

`python3 test_season.py` is the single verification command. It runs the extraction regression suite and saved-snapshot checks. `python3 -m unittest discover -s tests -v` isolates the offline regressions.
