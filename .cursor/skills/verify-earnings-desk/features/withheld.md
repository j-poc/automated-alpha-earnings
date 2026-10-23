# Review withheld results

## Sub-features

See unresolved results, their exact reason, and the SEC source without treating a pipeline failure as an issuer error.

## How to get to it (user POV)

Scroll to Withheld and click a company row.

## Driving it with IAB

Read `#held-table tbody tr`. Click a row that exists in the current snapshot and check `#d-title`, `#detail`, and its source link. Confirm the displayed reason matches `data/dashboard.json` for that ticker.

## Gotchas

The list changes as source data arrives and extraction improves. An empty list is valid only when the saved snapshot confirms zero withheld rows; do not hardcode a count.
