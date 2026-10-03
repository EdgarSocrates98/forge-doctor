# Program Q — Wave 5: SLO & Critical Path Intelligence (spec 245)

## Scope

Whole-path analysis over `DataPlatformGraph` — extends the phase-238
local reliability/SLA surface to end-to-end budgets.

## What landed

- `core/critical_path.py`
  - `CriticalPath` — source/destination/entities/executions/
    latency_segments/total_latency/freshness/bottleneck/
    unknown_segments + `coverage()` ("N known / M unknown").
  - Path discovery: bounded DFS (depth 16) over data-flow edges only —
    PRODUCES/CONSUMES/READS/WRITES/TRIGGERS/INVOKES/DEPENDS_ON/
    READS_FROM/WRITES_TO. Consumer-side edges (CONSUMES, READS,
    READS_FROM, DEPENDS_ON) are traversed in flow direction — they are
    drawn consumer→source, so reversing recovers the direction data
    moves. DEFINES/GOVERNS/etc. never form paths.
  - Segment latency from executions keyed by job/query identity, or
    explicit `latency_ms`/`duration_ms` attrs; freshness from
    `freshness_s`/`lag_s`/`lag_ms`. Missing → unknown, never inferred.
  - `SLOBudget` — per objective×path: total/consumed/remaining/
    violating_segments/coverage. Unit honesty: freshness objectives
    consume freshness segments, latency objectives consume ms segments;
    never mixed.
  - `SLOFinding` + `slo_findings` — SLO001 (e2e freshness violation),
    SLO002 (latency budget exhausted, top consumers listed), SLO003
    (unknown critical segment), SLO004 (RPO declared, no failover
    evidence), SLO005 (RTO declared, no failover evidence), SLO006
    (critical dependency under SLO scope without failover attrs).
- `cli/reliability.py` — new top-level group: `reliability path`,
  `reliability slo` (text + `--format json`).
- `analyzers/platform_graph_builder.py` — datacontract
  `servicelevels`/`slaProperties` now land as `sla_*`/`rpo`/`rto` attrs
  on governed entities — the missing emission path noted in the
  phase-238 run record; objectives are now reachable from real files.
- `core/lab.py` — truth keys `expected_slo_findings`, `expected_paths`.
- Lab: `labs/slo/kafka-spark-serving` — kafka→spark-ss→delta→serve
  chain; sla_latency 50ms on the governed table, ss segment measured at
  900ms via job-named executions → SLO002 + SLO003.
- `docs/checks.md` — SLO001-006 section; README command line.

## Validation

- `pytest tests/unit/ -k "slo or critical_path or budget" -x -q` —
  23 passed
- `tests/unit/test_critical_path.py` — 16 tests (flow-direction
  reversal, bounded cycles, median latency from executions, budget
  consumed/remaining, freshness segments, scope filtering, SLO001-006
  firing + failover suppression)
- lab run on `labs/slo` — 1/1 PASS
- `ruff check src tests`, `ruff format --check`, `mypy src` — clean
  (238 files)
- Full suite: **2095 passed** in 262.81s
