---
id: 157-serverless-graph
title: Serverless stage 8 - cross-domain graph + `aws-serverless inspect|graph`
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_lambda_step_athena.md` sub-cycle 8 (sections 36-39):
EventBridge -> SFN -> Lambda -> Athena -> S3/Iceberg edges.

# Acceptance Criteria
- `AwsServerlessGraph`: edges from SFN resource ARNs to lambda functions/
  athena queries; lambda env/trigger wiring; failure-path list per machine
  (retry x attempts -> catch -> notifier chain).
- `forge-doctor aws-serverless inspect|graph` - the reviewer's shape.
- SFNIAM-style check: SFN role lacks invoke permission on a referenced
  Lambda (IaC evidence-gated).
