---
id: 108-ci-extras-release
title: CI extras-smoke job + release hygiene quick wins
agent: devin
risk: low
grill: completed
verification:
  - python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
  - python -m pytest -x -q
---

# Context
CI never installs optional extras — watch/lsp/schemas paths are untested.
Version lives in both pyproject.toml and __init__.py (drift risk).
Changelog dates 0.7.0 ahead of publish.

# Acceptance Criteria
- New `extras-smoke` job: pip install '.[watch,lsp,schemas]' then minimal
  import/boot checks (import watchfiles path in watch loop, pygls server
  constructs, yaml schema parse works).
- `forge_doctor_data.__version__` = single-source via
  importlib.metadata.version("forge-doctor-data") with fallback for
  src-tree-without-install.
- CHANGELOG: 0.7.0 entry stays under a future-date heading corrected to
  [Unreleased] style until real publish (match repo convention).
- Docs updated where behavior changed (cache location, trusted vs allow,
  mcp --root, lsp workspace).

# Constraints
- extras job must not make the matrix slower than the quality job.
