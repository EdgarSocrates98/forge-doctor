---
id: 184
title: Finding promotion + root-cause clustering
agent: devin
risk: medium
verification: pytest tests/unit/test_diagnosis.py tests/unit/adversarial/test_diagnosis.py
program: prompt_evo_next_step phase 3
---

# Context

Phase 2 introduced the offline `RuntimeEvidenceModel`. Phase 3 correlates
static/config findings with runtime facts: promote findings whose risk is
confirmed, and cluster evidence into causal chains (root cause -> symptom).

# Acceptance criteria

- `core/diagnosis.py` provides `FindingPromotion` (original finding identity
  preserved via `base_fingerprint`; deterministic `promotion_id`; confirming
  evidence; resulting confidence/severity; explanation) and `FindingCluster`
  (root_causes, symptoms, related_findings, affected_entities, evidence,
  confidence, causal_edges).
- Promotion levels distinguish CONFIRMED / STRONGLY_SUPPORTED / POSSIBLE.
  CONFIRMED requires an exact identity join; domain-only correlation caps at
  STRONGLY_SUPPORTED (targeted rules) or POSSIBLE (shared-domain errors).
- Promotion never mutates the original finding or its fingerprint.
- Required chains: micro-batch -> commit amplification -> small files ->
  consumer overhead; join/shuffle key -> skew -> spill -> long stage.
- CLI: `forge-doctor-data root-cause .` and `forge-doctor-data root-cause --runtime
  artifact.json .` (repeatable option, `--json` supported).
- Deterministic: same inputs -> same promotions, clusters, ids regardless of
  input ordering.

# Constraints

- Offline only; no cloud/API calls.
- A cluster requires >=2 evidenced nodes; single-node evidence is not a chain.
- Missing evidence must not be treated as negative proof.

# Review notes

Spark adapter gained `task_count` metrics and stage `duration_ms` (Submission
Time -> Completion Time) to support the promotion signals.
