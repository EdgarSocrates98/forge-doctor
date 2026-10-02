# R3 Phase 2 — Enterprise Governance (spec 203)

## Built
- `Suppression.approved_by` (pyproject `approved_by = "..."`) flows into
  `SuppressionRecord` + `suppressions` audit (Approved column/JSON key).
- Policy pack layering: `extends: <pack-name|path>` merges parent rules
  into the child; child rule wins on id collision; unresolved refs and
  cycles emit `POLICY010` errors. `require_approval: true` on a pack
  makes unapproved suppressions emit `POLICY011`.
- `forge-doctor policy report [--format json]` — packs (extends/
  approval flags), violations by rule, suppression status counts and
  the unapproved list; exit 1 on violations/errors.
- Per-branch baselines: `--baseline main` / `--save-baseline main`
  resolve to `.forge-doctor/baselines/<name>.json` when the arg is a
  bare name (no dir part, no `.json` suffix, no existing file).
  `save_baseline` now creates parent dirs.
- `scan --evidence-out <dir>` writes `evidence-<UTC ts>/` with
  `report.json` (full scan report), `suppressions.json` (audit trail
  incl. approver), `packs.json` (packs + rules + errors in effect).

## Verified
- gov fixture: org-base + repo-rules(extends, require_approval) ->
  layered ORG001 (parent error + child override), POLICY011 on the
  unapproved suppression, suppressions report shows approved_by.
- Named baseline round-trips; evidence bundle contains all artifacts.
- 35 focused tests green (packs, governance CLI, baselines, policy).

## Open
- Where org packs physically live for multi-repo orgs (git URL? shared
  path?) — `policy_packs` config paths cover repo-local checkout;
  remote pack fetching is deliberately out of scope (offline-first).
