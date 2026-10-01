---
id: 115-spark-perf-v2
title: Spark Performance Doctor v2 — cross-file repeated actions, hint checks
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/checks/test_spark.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
Priority #4 from `prompt_evo_novas_evolucoes.md`, and the first consumer of
the newly-hardened machinery: the Semantic Index propagates df-ness
cross-file and the cache now invalidates on semantic change — so
"two actions on the same propagated DataFrame in different files" is now
detectable honestly.

# Acceptance Criteria
- `SPARK012` repeated actions on the same DataFrame-probable binding:
  ≥2 action calls (`.collect/.count/.show/.take/.head/.foreach/.write` /
  `.toPandas`) on the same resolved name, where the name is df-probable via
  index `df_names`/`propagated_names` (not just naming heuristic) → WARNING
  suggesting `.cache()`/single-pass. Cross-file: producer in A, actions in
  B and C — reported once on the binding, confidence HIGH.
- `SPARK013` `dropDuplicates()`/`distinct()` with no column subset on a
  df-probable receiver → WARNING (full-frame dedup).
- `SPARK014` join between a known-small source (broadcast-eligible literal/
  `spark.read` of a file named `*dim*`|`*lookup*`) and a df-probable frame
  without `broadcast()` hint → INFO.
- `SPARK015` `.repartition()`/`.coalesce()` immediately feeding `.write`
  chain on the same expression → INFO (check shuffle.partitions intent).
- `SPARK016` `spark.sql.shuffle.partitions` never configured anywhere in a
  project with ≥3 pyspark files → INFO.
- `SPARK017` AQE explicitly disabled (`spark.sql.adaptive.enabled=false`)
  in config → INFO explaining when that's intentional vs accidental.
- Every new check: why/when_ok/fix, tags, evidence-bearing file/line.
- Tests incl. the cross-file repeated-action case through the cache
  (cold + warm scan identical findings — dep_sigs regression surface).
- Docs: checks.md, README count, CHANGELOG.

# Constraints
- Confidence discipline: name-heuristic-only receivers stay MEDIUM and the
  message says "probable"; index-proven → HIGH. Mirror SPARK001-011 style.
- No new analyzer passes — facts come from the existing index + spark
  buckets.

# Review Notes
- Deliberately NOT included (non-goal): health-score aggregation; severity
  reweighting.
