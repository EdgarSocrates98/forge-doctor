---
id: 021-ci-hardening
title: CI hardening — OS smoke matrix, dogfood action, poetry floor
agent: devin
risk: low
grill: completed
verification:
  - python -m pytest tests -q
---

# Acceptance Criteria
- quality job unchanged (ubuntu 3.11-3.13)
- smoke job: os x [ubuntu,windows,macos] x py3.12: poetry build; pip install wheel; forge-doctor-data --version; scan a fixture project
- dogfood job: uses: ./ with sarif-file disabled
- pyproject requires-poetry >=2.2
