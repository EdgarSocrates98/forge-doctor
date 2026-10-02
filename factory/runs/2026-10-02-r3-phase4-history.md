# R3 Phase 4 - Historical Intelligence (spec 205)

## Built
- `core/history.py` — `record_snapshot` writes
  `.forge-doctor/history/<utc>.json` (format `forge-doctor/history@1`):
  severity summary, findings keyed by existing `result_key` fingerprint,
  entity ids + census (kind/domain), capability states (registry
  evaluated per observed domain), ARCH drift keys. `list/load/resolve
  (name|index)/diff_snapshots/trend/prune`.
- `cli/history.py` — `history` list, `history diff <a> <b>` or
  `--last`, `history trend` (per-category series + debt trajectory;
  category columns capped at busiest 8). Text + JSON.
- `scan --record [--keep N]` — the *only* path that writes history;
  `--keep` prunes oldest deterministically.

## Verified
- Two recorded snapshots on the gov fixture: list, `diff --last`,
  `trend` all render; JSON shapes stable.
- Bug found by tests: same-second collision suffix `-N` sorts BEFORE
  `.json` (ASCII `-`=45 < `.`=46) so snapshot order inverted — switched
  to `_N` (`_`=95 > `.`). Ordering now correct oldest→newest.
- 7 unit tests incl. "normal scans never write history" invariant.

## Open
- Snapshot size: entity ids + findings are the only bulky fields; fine
  at repo scale, revisit at fleet scale (fleet history deferred —
  history is per-repo by design; estate series could wrap it later).
