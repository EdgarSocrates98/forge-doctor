# Run — Control-M Intelligence, stage 1 (spec 116)

**Prompt:** `prompt_evo_control-m.md` — a six-stage Control-M cycle, explicitly
prioritized ahead of remaining Lake Formation / Spark-perf work. Stage 1 =
semantic model + detection + structural checks + `controlm inspect`.

## Specs written

| spec | scope | state |
|---|---|---|
| 116-controlm-model | ControlMModel, CTM000-070, `controlm inspect`, packs | **active — built** |
| 117-controlm-graph-scheduling | dep graph, cycles, effective schedule, `controlm schedule|graph` | inbox |
| 118-controlm-governance | site-standard rules, naming, agents/host groups, profiles | inbox |
| 119-controlm-automation-api | `ctm build/deploy` + REST patterns, TLS/timeout, CTM094 artifact mismatch | inbox |
| 120-controlm-sla-diagnose | SLA critical paths + error-pack depth | inbox |
| 121-controlm-migrate-graph | `controlm migrate` + project-graph integration | inbox |

## Shipped (116)

- `analyzers/controlm_model.py` — `ControlMModel` + `CtmJob`/`CtmFolder`.
  `*.json` parsed only when a Control-M `"Type"` marker exists; recursive
  walk handles Folder/SmartFolder/SubFolder nesting, `Defaults` blocks
  (order-independent two-pass merge — sorted iteration put `DAILY_LOAD`
  before `Defaults`, caught by test), event fields at any depth (so
  `OnDo→Do→AddEvents` counts), calendar refs/defs, site standards,
  deploy descriptors, `ctm`/API refs in scripts/CI/code. Credential-shaped
  props recorded by NAME only (V3).
- `checks/controlm.py` — CTM000 anchor, CTM002 no Host/HostGroup, CTM003
  duplicate names (cross-file), CTM004 never-fires, CTM009 event-never-
  produced, CTM010 event-never-consumed, CTM028 undefined calendar,
  CTM051 missing required metadata (field list from objects pack),
  CTM070 credential literal (no evidence line — would leak the value).
- `cli/controlm.py` — `forge-doctor-data controlm inspect` (definitions,
  folders, jobs-by-type + per-job target/trigger, events incl. unmatched,
  calendars, site standards, refs, severity-sorted risks).
- `knowledge/controlm/objects.json` + `knowledge/errors/controlm.json`
  (6 error families, wired into `diagnose` via ERROR_DOMAINS).
- Registration: `checks/__init__` (unconditional — stdlib only),
  `cli/__init__`, `core/diagnose.py`. Docs: SPEC T34 + §I, README row +
  command, checks.md section, CHANGELOG.

## E2E evidence

`controlm inspect` on a fixture produces the reviewer's showcase: DAILY_LOAD
folder, 2 jobs, `ORDER_READY` produced+consumed, `CUSTOMER_READY` unmatched
(never produced) → CTM009 WARNING at jobs.json:11, CTM002 WARNING for the
hostless job. `diagnose` on a ctm log maps "Agent Ping failed" → CTM-E001,
"OSCOMPSTAT" → CTM-E002. Full `scan` renders the `Controlm` category
worst-first with severity badge via the new renderer.

## Latent quirks found

- Automation API `Defaults` must merge before sibling walk — sorted key
  order breaks "defaults first"; two-pass per level.
- `ctm` inside JSON defs is text-scanned too — harmless, and CI/deploy.sh
  refs are the intended targets.
- `.pytest_tmp/` is wiped between pytest runs — use `$TEMP` for manual e2e.

## Verification

493 passed · ruff check clean · ruff format clean · mypy 74 files clean.

**Parked at review gate.** Spec stays in `active/` until accepted.
Next ready spec: 117 (dependency graph + `controlm schedule`).
