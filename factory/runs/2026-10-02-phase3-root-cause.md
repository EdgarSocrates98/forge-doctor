---
spec: 184-finding-promotion-root-cause
phase: prompt_evo_next_step phase 3 (Finding Promotion + Root Cause)
date: 2026-10-02
agent: devin
---

# Phase 3 — Finding Promotion + Root-Cause Clustering

## What was built

- `core/diagnosis.py`:
  - `PromotionLevel` — POSSIBLE < STRONGLY_SUPPORTED < CONFIRMED.
  - `FindingPromotion` — `base_fingerprint` (original never mutated),
    deterministic `promotion_id` (sha256 over base + sorted evidence),
    `confirming_evidence`, `resulting_confidence`/`resulting_severity`,
    `level`, `explanation`.
  - `FindingCluster` + `CausalEdge` — root_causes, symptoms,
    related_findings, affected_entities, evidence, confidence,
    causal_edges.
  - `promote_findings(results, models)` — 4 deterministic rules:
    `single_task_bottleneck` (SPARK003/ICE024/PARQ010 × spark_eventlog
    task_count==1), `skew_confirmed` (SPARK009/SPARK010/PARQ042 ×
    skew/spill facts), `backlog_observed` (STREAM###/PLAT007 ×
    input>output rate), `retries_observed` (PLAT001 × retries>0).
    CONFIRMED additionally requires exact identity overlap between the
    artifact's exported identifiers and the finding anchor; otherwise
    STRONGLY_SUPPORTED. A generic `domain_errors` tier maps check-id
    prefixes to same-domain runtime sources and caps at POSSIBLE.
  - `cluster_findings(results, models)` — two required chains:
    `RC_STREAM_COMMITS` (micro-batch writer → commit amplification →
    small files → consumer overhead) and `RC_SPARK_SKEW` (join/shuffle
    key → skew → spill → long stage). Node probes collect evidence from
    findings and runtime facts; confidence = CONFIRMED iff all nodes
    evidenced and ≥1 backed by runtime, STRONGLY_SUPPORTED at all-but-one,
    POSSIBLE at the 2-node floor.
- Spark adapter: per-stage `task_count` metric and `duration_ms`
  (Submission→Completion Time) — the signals the promotion rules need.
- `cli/rootcause.py` — `forge-doctor-data root-cause . [--runtime artifact ...]`
  (`--json`).

## Evidence policy

Identity joins use only exported identifiers (app/stream/job/query/
execution ids, metric scopes) matched verbatim against the finding's
file/message/evidence/symbol. Absence of evidence yields no promotion and
no cluster — never a negative claim. Promotion never mutates the
original finding.

## Verification

- `pytest tests/unit/test_diagnosis.py tests/unit/adversarial/test_diagnosis.py`
  — 20 passed (confirmation tiers, fingerprint preservation,
  determinism across orderings, spoofed-identity cap, single-node
  rejection, all-static cap).
- `pytest -x -q` — 1016 passed; `mypy src` clean (115 files);
  `ruff`/`ruff format --check` clean.
- CLI smoke: `repartition(1)` project + crafted event log → SPARK003 and
  PARQ010 promoted to STRONGLY_SUPPORTED with "stage-2 ran a single task
  for 61000ms" evidence; RC_STREAM_COMMITS emitted at POSSIBLE from
  static-only nodes.

## Open questions

None. Chain templates are intentionally small and declarative; adding a
chain = one `_Chain` + probe functions.
