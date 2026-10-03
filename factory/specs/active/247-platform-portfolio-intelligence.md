---
id: 247
title: Platform Portfolio Intelligence — cross-repo estate view
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k "portfolio or fleet" -x -q
  - python -m pytest tests/ -x -q
  - ruff check src tests && ruff format --check src tests && mypy
---

# Program Q — Phase 7: Platform Portfolio Intelligence (prompt_evo_step9 §PHASE 7, §27–32, §65–66, §73–75, §82, §96)

Portfolio-wide view combining fleet + ontology + cost + reliability +
history. Not a CMDB / financial tool — facts, not scores.

## Acceptance Criteria

- `PlatformPortfolio` (platforms, workloads, logical_datasets,
  physical_representations, teams, environments, cost_drivers,
  reliability_signals, lifecycle, criticality).
- Answers (§7.2): engines serving same workload; logical datasets on
  multiple platforms; platforms carrying critical workloads; deprecated
  technologies; concentrated cross-cloud dependencies; complexity
  accumulation; repeatedly-regressing workloads; teams owning most
  cross-platform dependencies.
- `TechnologyInstance` (platform, version, lifecycle_status, owner,
  workloads, criticality, replacement) using capability lifecycle /
  knowledge packs for EOL/deprecation.
- `PortfolioDuplicationSignal` — same logical dataset on N warehouses,
  same workload under multiple orchestrators, data replicated across
  clouds — classified OPPORTUNITY, not error.
- `PortfolioComplexitySignal` (engines, orchestrators,
  governance_planes, copies, cross_cloud_edges, owners) — facts only.
- Technical-debt trend from history (deprecated runtimes / unknown
  ownership / cross-cloud movement / duplicate materializations
  increasing); `RecurringPattern` for org learning (e.g. N repos with
  same workaround → candidate shared platform capability).
- Fleet-level regression aggregation (same regression family across
  workloads after a platform upgrade); portfolio change/drift answers
  from snapshots (runtime/ownership/SLO/lifecycle drift); criticality +
  blast radius + regression combined without magic score; migration
  blocker workloads answerable.
- `ValidatedOptimizationEvidence` — project-local learning record
  closing the OptimizationCandidate → ExperimentPlan →
  ExperimentResult → Accepted/Rejected loop (§24–26); results never
  generalize globally.
- CLI: `fleet portfolio`, `fleet regressions`.
- Lab: `labs/portfolio/` multi-repo duplication scenario.
- Evidence Bundle v2 option with compact summaries + token economy;
  MCP tools (get_execution_baseline, get_regressions,
  get_runtime_correlations, get_incident_explanation,
  get_critical_path, get_capacity_signals, get_portfolio_summary) only
  where contracts are stable.

## Constraints

- Deterministic ordering of all portfolio rows; "platform health
  score = 74" forbidden — show facts.
