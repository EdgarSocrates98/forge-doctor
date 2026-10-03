# Run: Program O wave 4 — Deep multi-cloud intelligence (spec 233)

- **Commit**: `19d55ba`
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/233-deep-multicloud-intelligence.md`

## Scope

Azure + GCP service models, surface checks, graph integration,
capability packs, offline runtime-evidence adapters, and labs —
detection only from committed artifacts, hermetic per §13.

## Files changed

- `src/forge_doctor_data/analyzers/azure_model.py` — **new**: ADLS account/
  filesystem (HNS, `storage_account_id` ownership), Event Hubs
  namespace+hub, Synapse workspace, ADF factory, Purview, Fabric
  capacity, Function App — file/line provenance throughout.
- `src/forge_doctor_data/analyzers/gcp_model.py` — **new**: GCS bucket,
  Dataflow job, Dataproc cluster, Pub/Sub topic+subscription,
  Composer env, Dataplex lake (zone→lake refs), Cloud Function.
- `src/forge_doctor_data/checks/azure.py` / `checks/gcp.py` — **new**:
  `AZ000`/`GCP000` INFO surface anchors + WARNING checks (HNS missing,
  short hub retention, bucket force-destroy, Dataflow cancel, Pub/Sub
  no-DLQ).
- `src/forge_doctor_data/analyzers/platform_graph_builder.py` —
  `_terraform` derives cloud domain from provider prefix (was
  hardcoded `aws`; AWS entity IDs unchanged); `_TF_TYPED` gained
  azurerm/google types; `_azure`/`_gcp` adapters emit CONTAINS +
  Pub/Sub→Dataflow `TRIGGERS`, Dataflow→BigQuery `WRITES`,
  ref-form attribute resolution via `resource` labels.
- `src/forge_doctor_data/analyzers/abstractions.py` — canonical tuple
  gained `orchestrator`; azurerm/google resource mappings.
- `src/forge_doctor_data/analyzers/runtime_evidence.py` — five offline
  export adapters (Dataflow metrics, Pub/Sub backlog, Synapse query
  export, Event Hubs metrics, Fabric pipeline export), appended after
  existing adapters to preserve match priority.
- `knowledge/capabilities/` — nine sourced packs: `adls`,
  `azure_fabric`, `synapse`, `eventhubs`, `purview`, `dataflow`,
  `dataproc`, `pubsub`, `dataplex`.
- `labs/azure/{governed,hns-missing,hub-short-retention}`,
  `labs/gcp/{bucket-force-destroy,dataflow-cancel,governed,
  pubsub-no-dlq}` — all expectations pass.
- `tests/unit/test_azure_model.py`, `test_gcp_model.py`,
  `test_multicloud_graph.py` — **new** (33 tests).

## Found & fixed en route

- Nested TF blocks absent from `block.attrs` — nested config parsed
  from `block.body` via conservative regexes.
- `google_pubsub_topic.events.id`-style refs unresolvable — `resource`
  labels added to referenced models.
- BigQuery datasets already land under `table:warehouse:` IDs — a
  duplicate typed entity was removed instead of forking IDs.
- `dataplex zones` annotated `list[str]` while holding tuples —
  corrected.

## Validation

- `pytest tests/unit/ -k "azure or gcp or cloud"` — 56 passed.
- All 11 lab scenarios green; no error-severity findings → no new
  suppressions needed.
- Full suite + ruff + mypy green (typing finalized in `ebf5945`).
