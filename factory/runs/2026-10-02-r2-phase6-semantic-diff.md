# Roadmap-2 Phase 6 run — Semantic Diff / PR Intelligence

- Spec: `factory/specs/active/197-semantic-diff.md`
- Commit message: `feat(diff): add semantic entity diff and PR risk report`

## Implemented

- `core/semantic_diff.py`:
  - `diff_graphs(base, head, changed_files)` → `SemanticDiff`:
    `EntityChange`s classified `added`/`removed`/`modified`/`touched`
    (file changed, attrs identical), with `via_files`, `attr_diffs`,
    and per-entity `impacted` blast radius.
  - `blast_radius(graph, id)` — review-oriented traversal: inbound
    DEPENDS_ON/READS/CONSUMES/STORED_IN/INVOKES/TRIGGERS (callers are
    dependents) + outbound DEFINES/GOVERNS/INVOKES/TRIGGERS/WRITES/
    PRODUCES.
  - `risk` — HIGH on removed-with-dependents or modified structural
    entity with dependents; MEDIUM otherwise when dependents exist or
    entities are removed; LOW otherwise. Every classification emits a
    `reason`.
- `cli/diff.py` — `diff <base>...<head> --semantic`: risk panel,
  reasons, entity table, blast radius, unmapped file count; exit 1 on
  new findings or HIGH risk.
- `cli/common.py` — `_scan_git_ref` factored onto a shared
  `_ref_worktree` contextmanager (reused by workspace diff + semantic).
- `platform_graph_builder` — Terraform typed entities propagate
  `glue_version`/`runtime`/`engine_version`/`release_label`/
  `format_version`/`spark_version` into attrs (version bumps →
  `modified`, not just `touched`).
- `platform_graph.add_entity` — duplicate-id entities now *merge*
  (first-wins per field, missing file/line/attrs filled) instead of
  discarding later producers; identity preserved when nothing new.

## Verified

- Fixture A: delete `aws_glue_job` under an airflow caller →
  `removed` infra + `compute_job`, blast = task+workflow, HIGH, exit 1.
- Fixture B: `glue_version 4.0→5.0` → `modified compute_job
  (glue_version)` + `touched` infra, HIGH, exit 1.
- `pytest tests/unit/test_semantic_diff.py
  tests/unit/adversarial/test_semantic_diff.py` → 12 passed.
- `lab run` 10/10, `golden run` 8/8 — no snapshot drift from the
  `add_entity` merge or version-attr propagation.

## Open questions

- Blast radius intentionally over-approximates callers (a `task→job`
  INVOKES means the task depends on the job); if a narrower signal is
  wanted for impact-only flows, `impact_reachable` remains available.
- `unmapped_files` flags changes no entity claims — could later feed a
  "coverage gap" metric rather than just printing.
