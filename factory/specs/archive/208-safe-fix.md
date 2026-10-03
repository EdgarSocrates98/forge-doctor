---
id: 208
title: Safe Fix Intelligence
agent: claude
risk: high
verification:
  - python -m pytest tests/unit/ -k fix -x -q
---

# Roadmap-3 Phase 7 - Safe Fix Intelligence

## Context

Diagnostics are proven; the doc now allows *controlled* autofix with a
strict safety taxonomy: SAFE_FIX / REVIEW_REQUIRED / MANUAL_ONLY.
Dangerous classes (table migration, partitioning, LF, IAM) are
MANUAL_ONLY forever — never auto-applied.

## Acceptance Criteria

- `core/fixes.py` — `FixAction` model: `{check_id, file, transform,
  class}` where `class in {safe, review, manual}`; a registry mapping
  check ids to deterministic transforms.
- `forge-doctor-data fix <path>` — **dry-run by default** (prints diffs);
  `--apply` writes only `safe` transforms; `--apply --class review`
  also applies review-tier; `manual` never applies (prints guidance).
- Ship 3+ real safe transforms, e.g.: add `requires-python` to
  pyproject (PY002), remove a `.env` line from `.gitignore`-missing
  case → append `.env` (GIT001-family), normalize `requirements.txt`
  duplicate lines (DEP-family). Each transform is pure text, bounded,
  idempotent, and covered by before/after tests.
- Fix findings carry `fixable` already — `fix` output links plan to
  finding ids; `--apply` records an audit line (what changed, where) to
  stdout + JSON.
- Guardrail test: transforms asserting on MANUAL_ONLY-class checks
  cannot execute (no code path).

## Constraints

- Never applies: table migrations, partition changes, LF/IAM edits,
  anything touching IaC resource semantics — hard-coded exclusion.
- `--apply` writes only inside the scanned root; no git ops; backs up
  nothing (version control is the rollback).

## Open questions

- Fix patches vs unified-diff output for review tier — start with
  `--diff` printing unified diffs; patch-file export deferred.
