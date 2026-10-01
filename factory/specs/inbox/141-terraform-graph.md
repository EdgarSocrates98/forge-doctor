---
id: 141-terraform-graph
title: Terraform stage 8 - cross-domain graph edges into platform model
agent: devin
risk: high
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_terraform.md` stages 8+28+34: feed the Project Intelligence
Graph. `aws_glue_job.x.command.script_location` -> the .py file -> its
Spark/Iceberg evidence.

# Acceptance Criteria
- Edges: resource attrs referencing repo paths (script_location,
  bootstrap path) resolve to files; provider/assume_role edges; resources
  -> catalog/LF nodes.
- At least one cross-domain check: Terraform Glue version vs code-level
  Spark/Iceberg requirements (e.g. IaC says Glue 4.0 while code uses
  format-version=2 merge - WARNING).
- `terraform explain <addr>` command rendering provider/depends-on/
  references/detected-domains for one resource.
