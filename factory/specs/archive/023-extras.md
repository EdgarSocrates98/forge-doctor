---
id: 023-extras
title: Beyond-prompt extras — doctor, jsonl, --stats, dedupe
agent: devin
risk: low
grill: completed
verification:
  - python -m pytest tests -q
---

# Acceptance Criteria
- `forge-doctor doctor`: self-check (git present, plugins health, cache dir writable, config valid)
- --format jsonl (one finding per line) for streaming agents
- --stats: per-check durations + cache hit rate printed to stderr
- runner dedupes identical (check_id,file,line,message) results
