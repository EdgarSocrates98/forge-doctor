---
id: 116-controlm-model
title: Control-M Intelligence stage 1 — ControlMModel, CTM000-070 checks, `controlm inspect`
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/test_controlm_model.py tests/unit/checks/test_controlm.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_control-m.md` opens a six-stage Control-M cycle, explicitly ahead
of remaining Iceberg/LF/Spark work. Stage 1 = the semantic model +
detection + first structural checks + `controlm inspect`. Stages 2-6 are
specs 117-121.

Control-M Automation API defines workflows-as-code as JSON: top-level folder
keys (`"Type": "Folder"|"SmartFolder"|"SubFolder"`), job entries
(`"Type": "Job"` or plugin types `"Job:<kind>"`), optional `"Defaults"`,
events (`EventsToWaitFor`/`WaitForEvents`, `EventsToAdd`/`AddEvents`,
`PrereqConditions`), scheduling (`When`, `RuleBasedCalendars`, `FromTime`,
`ToTime`, `Cyclic`), site standards (`"Type": "SiteStandard"`), and deploy
descriptors. Indirect signals: `ctm ` CLI calls in shell/CI, Automation API
URLs in code.

# Acceptance Criteria

## Model (`analyzers/controlm_model.py`) — stdlib only, no new deps
- `CtmJob` frozen record: name, folder, job_type, file, line (line resolved
  by locating `"<name>"` in file text; 0 when unresolvable), plus effective
  props merged `Defaults.Job` → folder → job for: application,
  subapplication, owner, runas, host, hostgroup; `wait_events`,
  `add_events`, `calendars` (referenced names), `has_schedule` (any
  `When`/`WeekDays`/`Months`/`Calendar`/`RuleBasedCalendars`/`RBC`/
  `FromTime`/`ToTime`/`Cyclic`/`Schedule`-ish key present), `cyclic`.
- `ControlMModel` (memoized on ctx): `files`, `folders`, `jobs`,
  `calendars` (defined names: Type matching /calendar|rbc/i or entries under
  a `Calendars`/`RuleBasedCalendars` top key), `site_standards`,
  `events_produced`, `events_consumed` (name→evidence), `cli_refs`,
  `api_refs` (files containing `ctm ` or `/automation-api/`),
  `deploy_descriptors` (filename contains `deploy`), `has_controlm`.
- Detection: `*.json` files parsed ONLY when text contains `"Type"` and one
  of `"Job"`, `"Folder"`, `"SmartFolder"`, `"SiteStandard"`, `"Defaults"`;
  malformed JSON skipped silently. Never read secret VALUES (see CTM070).
- Deterministic ordering throughout.

## Checks (`checks/controlm.py`, `category = "controlm"`, unconditional)
- `CTM000` surface anchor (PASS none / INFO counts).
- `CTM002` job without execution target — no Host and no HostGroup in
  effective props → WARNING.
- `CTM003` duplicate job or folder name across definitions → WARNING.
- `CTM004` job can never fire — no schedule AND no wait events → INFO.
- `CTM009` event consumed but never produced anywhere → WARNING.
- `CTM010` event produced but never consumed → INFO.
- `CTM028` calendar referenced but not defined → WARNING.
- `CTM051` missing required metadata — effective props missing any of
  Owner/RunAs/Application/SubApplication → WARNING, message names the
  missing fields (one finding per job).
- `CTM070` credential literal — property name matching
  password|secret|token|credential|apikey|api_key (case-insensitive) with a
  non-empty literal value → WARNING, message names the property only;
  value NEVER in output (V3).

## Command (`cli/controlm.py`, `controlm` typer group)
- `forge-doctor-data controlm inspect [path]` — Rich summary: definition files,
  folders, jobs by type, events produced/consumed/unmatched, calendars,
  site standards, CLI/API refs, deploy descriptors, risks (runs CTM checks).
- Bare `controlm` prints usage hint, exit 2. Works on empty repos.

## Knowledge packs
- `knowledge/controlm/objects.json` (schema_version 2 + sources): job types
  seen (`Job`, `Job:Command`, `Job:Script`, `Job:*` plugin prefix rule),
  event field synonyms, scheduling field names, required-metadata field
  list — checks read these, don't hardcode.
- `knowledge/errors/controlm.json` — seed error families (agent
  unavailable, OSCOMPSTAT, deploy validation, SLA delay) for `diagnose` to
  pick up via the existing error-pack mechanism.

## Docs/tests
- checks.md `## Control-M` section; README row + command; CHANGELOG; SPEC
  T34 + `controlm` in the interface list.
- Tests: model facts per source; each check positive+negative; duplicate-
  name cross-file; event produce/consume cross-file; deterministic order;
  `controlm inspect` CliRunner; a non-Control-M repo stays silent.

# Constraints
- Offline only; no `ctm` subprocess, no API calls (reviewer's invariant).
- json parse guarded; huge/malformed files skipped.
- Secrets: only property NAMES reported.

# Review Notes
- Deferred to 117-121: dependency graph + scheduling engine (`controlm
  schedule`), site-standard rule evaluation + governance, Automation API /
  CI-CD checks (CTM080+), SLA doctor + diagnose depth, migrate + project
  graph integration.
