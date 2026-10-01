---
id: 006-cli-modularization
title: Split cli.py into cli/ package
agent: devin
risk: low
grill: completed
verification:
  - python -m pytest tests -q
---

# Acceptance Criteria
- forge_doctor/cli/ package: app.py, common.py, scan.py, diff.py, workspace.py, compatibility.py, plugins.py, misc.py (init/info/explain/checks/version)
- forge_doctor/cli.py remains shim exporting app (entry point unchanged)
- zero behavior change; all tests green
