---
id: 161-streaming-checkpoint-state
title: Streaming stage 3 - Checkpoint compat fingerprint, watermark/state doctors
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/checks/test_streaming.py tests/unit/test_streaming_model.py tests/unit/test_streaming_compat.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_streaming.md` sub-cycle 3 of 10. Checkpoint = offsets +
commits + state + query metadata; a checkpoint is only valid for the
query shape that produced it.

# Acceptance Criteria
- `StreamingQueryFingerprint` (source, stateful ops, output mode, state
  schema evidence, watermarks, sink, partitioning) serializable to JSON;
  `forge-doctor-data streaming checkpoint-compat old.json new.json` diffs two
  fingerprints and reports compatibility findings (STREAM016 WARNING
  when stateful shape changed under the same checkpoint).
- `WatermarkModel` facts on the query record: column, delay threshold,
  downstream stateful ops.
- New checks: STREAM021 watermark column unused by any stateful op
  (INFO), STREAM022 suspiciously aggressive watermark (heuristic floor
  from pack; INFO), STREAM023 extremely long watermark retention (INFO),
  STREAM024 different watermarks across joined streams (INFO),
  STREAM025 processing-time evidence where event-time intent declared
  (INFO), STREAM030 unbounded state risk — stateful ops with no
  watermark/timeout evidence (WARNING), STREAM033 multiple stateful
  stages in one query (INFO), STREAM034 state retention evidence absent
  for long-lived stream (INFO).
- knowledge pack additions: `knowledge/streaming/spark/checkpoints.json`
  + `watermarks.json` (floors, semantics, sources).
- Tests incl. fingerprint-diff fixtures; docs + CHANGELOG.

# Constraints
- Fingerprint diff is shape-vs-shape, not runtime state — messages say
  "potentially incompatible", never "will corrupt".
- Watermark severity stays INFO unless paired with absent-checkpoint
  evidence.
- Depends on 159/160.
