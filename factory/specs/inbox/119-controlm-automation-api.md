---
id: 119-controlm-automation-api
title: Control-M stage 4 — Automation API + CI/CD checks (build-before-deploy, TLS, artifact match)
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
Stage 4 of `prompt_evo_control-m.md`. Detect Automation API usage (`ctm
build|deploy|run|config|provision`, `/automation-api/` REST calls in
python/shell) and cross it with CI workflows.

# Acceptance Criteria
- `cli_refs`/`api_refs` from 116's model deepened into call records
  (subcommand, file, line).
- Checks: CTM080 deploy without prior build/test in the same pipeline file;
  CTM084 TLS verification disabled; CTM085 API call without timeout
  (requests/httpx/urllib literals); CTM086 deploy descriptor missing;
  CTM094 build deploys a DIFFERENT artifact than the one validated.
- CTM090-093 CI cross-checks when a workflow references ctm commands.
- Offline only — flag patterns statically, never call `ctm`.

# Constraints
- Works on generic shell/YAML/python text scanning; keep evidence as
  file:line.
