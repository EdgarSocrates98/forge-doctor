# Run: spec 114 — Lake Formation pack + diagnose correlation

## What shipped

- `knowledge/lakeformation/errors.json` — new domain pack (schema_version 2)
  covering the reviewer list: GetTemporaryCredentialsForTable(V2), credential
  vending, FGAC, resource links, RAM/cross-account, hybrid access,
  IAMAllowedPrincipals, LF-tags, DATA_LOCATION_ACCESS, register/S3-path
  requirements (LAKE-E001..E010, each with patterns + causes + fixes).
  Supersedes `knowledge/errors/lakeformation.json` (removed — ids preserved).
- `core/diagnose.py` — `ErrorSignature` gained `fixes`/`families`;
  `load_signatures()` merges `errors/<domain>.json` + `<domain>/errors.json`
  with (domain,id) dedup. New `project_correlations(ctx, diagnoses)`:
  vending-family diagnosis + Glue >=5.x (IaC glue_version) + LF/FGAC marker in
  IaC/config files + a write op (iceberg ops / py call-sites / SQL writes,
  sqlglot-guarded) -> the vending/write-path conflict line. Missing any leg
  -> no claim.
- `cli/misc.py` — `diagnose` gained `--path` (project root, default `.`);
  correlations evaluated only when a lakeformation-domain signature matched;
  text output prints `fix:` hints + `correlation:` line; JSON gains
  `fixes` per finding + top-level `correlations`.
- `checks/lakeformation.py` — `category = "lakeformation"`: LF000 usage
  anchor, LF001 resource-link/cross-account target without RAM share
  (DERIVED, MEDIUM), LF002 IAMAllowedPrincipals alongside FGAC/LF-tag
  evidence (DERIVED, MEDIUM). Registered in `checks/__init__.py`.
- CHANGELOG entry under Added (0.8.0).

## Verification

- `pytest tests/unit/test_diagnose.py tests/unit/checks/test_lakeformation.py -q` — 17 passed
- `pytest -x -q` — 721 passed (+13)
- `mypy src` — 93 files clean
- `ruff check src tests` — clean
- E2E smoke: `forge-doctor diagnose job.log --path <fixture>` prints LAKE-E004
  with fix hints + the correlation line.

## Open questions / notes

- Correlation wording stays "possible ... conflict" per the evidence-gated
  constraint; legs are IaC/config-file + call-site facts only — a Glue job
  pinned >=5.x via `--glue-version` in DefaultArguments maps is visible only
  through file-text markers, not flat attrs (documented limitation).
- LF002's project-level conjunction is the honest read of "alongside"; a
  per-resource join would need RAM cross-references hcl_lite doesn't model.
