---
id: 101-action-source-install
title: Action installs its own checkout (not PyPI) + robust exit capture
agent: devin
risk: low
grill: completed
verification:
  - python -c "import yaml; yaml.safe_load(open('action.yml'))"
---

# Context
action.yml ran `pipx install forge-doctor` — fails (not on PyPI) and can drift:
`uses: forge-doctor@v0.7.0` could install `forge-doctor v0.9`. Also the scan
step relies on `; echo $?` which is fragile under bash -e fail-fast.

# Acceptance Criteria
- Install step defaults to `pipx install "$GITHUB_ACTION_PATH"` — the action
  always runs the code at its own ref
- `install` input remains as explicit override
- Scan step wraps execution in `set +e` / `set -e`, captures `$?` into
  forge_doctor_exit, always `exit 0` so SARIF upload runs
- `version` input removed or deprecated (superseded by action ref pinning)
- dogfood job in ci.yml drops the `install: .` override (GITHUB_ACTION_PATH
  covers it) or keeps it harmlessly

# Constraints
- Composite action stays zero-dep beyond pipx/python.
