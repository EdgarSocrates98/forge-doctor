---
id: 197
title: Semantic Diff / PR Intelligence
agent: claude
risk: low
verification:
  - python -m pytest tests/unit/test_semantic_diff.py tests/unit/adversarial/test_semantic_diff.py -x -q
  - python -m forge_doctor_data diff <base>...<head> --semantic --path <repo>
  - python -m forge_doctor_data lab run
  - python -m forge_doctor_data golden run
---

# Roadmap-2 Phase 6 - Semantic Diff / PR Intelligence

## Context

`forge-doctor-data diff` compares *findings*. PR review needs the semantic
layer: which platform entities changed, what depends on them, and a
deterministic risk classification — no LLM required.

## Acceptance Criteria

- `core/semantic_diff.py`:
  - `diff_graphs(base, head, changed_files)` → `SemanticDiff` with
    `EntityChange`s (`added` | `removed` | `modified` | `touched`),
    per-entity `via_files`, `attr_diffs`, and `impacted` blast radius.
  - `blast_radius(graph, id)` — PR-review traversal: inbound
    dependency edges (DEPENDS_ON/READS/CONSUMES/STORED_IN/INVOKES/
    TRIGGERS) + outbound containment/dataflow (DEFINES/GOVERNS/INVOKES/
    TRIGGERS/WRITES/PRODUCES).
  - Deterministic `risk`: removed-with-dependents or modified
    structural entity with dependents → HIGH; removed-without /
    modified-with / touched-with dependents → MEDIUM; else LOW,
    with human-readable `reasons`.
- `forge-doctor-data diff <base>...<head> --semantic` renders risk panel,
  reasons, entity-change table, blast radius, unmapped-file count;
  exit 1 on added findings or HIGH risk. Report-JSON sides keep the
  classic findings-only output.
- Shared `_ref_worktree` contextmanager backs both `diff` and
  `workspace diff` worktree mechanics.
- Terraform typed entities propagate version attrs (`glue_version`,
  `runtime`, `engine_version`, `release_label`, `format_version`,
  `spark_version`) so version bumps register as `modified`.
- `add_entity` merges duplicate-id entities (first-wins per field,
  missing file/line/attrs filled) so a TF `compute_job` and an airflow
  `INVOKES` target union their facts.
- Tests: removed/modified/touched/added classification, blast-radius
  direction, risk monotonicity, determinism, unmapped files, empties.

## Constraints

- Semantic mode requires real git refs (needs file trees, not just
  saved findings).
- Blast radius may over-approximate callers — acceptable for review
  signal; documented in `blast_radius`.
- No LLM, no network — pure graph diff.

## Review Notes

- `add_entity` merge changes duplicate-id handling globally; verified
  via `lab run` + `golden run` — zero snapshot drift.
