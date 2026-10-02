"""Vendor-neutral cloud abstraction layer (spec 223).

A *view over* existing evidence — never a replacement. Terraform
``azurerm_*``/``google_*``/``aws_*`` data-platform resources and
vendor-attributed graph entities fold into six abstractions:

- ``object_storage`` ← s3 | adls_gen2/storage_account | gcs
- ``stream`` ← kinesis | eventhubs | pubsub | msk | kafka/confluent
- ``compute_engine`` ← emr | synapse | databricks | dataproc | glue
- ``catalog`` ← glue | purview | unity | datacatalog
- ``operational_store`` ← dynamodb | cosmosdb | bigtable
- ``warehouse`` ← redshift | synapse_sql | bigquery | snowflake | fabric
- ``orchestrator`` ← adf | composer (spec 233)

Attribute normalization is deliberately shallow (spec open question):
``name``, ``region``/``location``, ``encryption``, ``public``
plus a bounded vendor-attrs passthrough.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext

_CACHE_ATTR = "_forge_doctor_abstractions_model"

ABSTRACTIONS = (
    "object_storage",
    "stream",
    "compute_engine",
    "catalog",
    "operational_store",
    "warehouse",
    "orchestrator",
)


@dataclass(frozen=True)
class AbstractedService:
    """One vendor service folded into an abstraction."""

    abstraction: str
    cloud: str  # aws | azure | gcp | snowflake | databricks | vendor
    service: str  # s3 | eventhubs | pubsub | redshift | ...
    name: str
    file: Path
    region: str = ""
    encrypted: str = ""  # "yes"|"no"|"" (unknown)
    public: str = ""  # "yes"|"no"|"" (unknown)
    linked: bool = False  # replication/migration link declared
    vendor_attrs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class UnmappedService:
    """A platform-domain entity with no abstraction mapping."""

    domain: str
    kind: str
    name: str
    file: Path | None = None


@dataclass
class CloudAbstractionModel:
    """The vendor-neutral view over detected cloud services."""

    services: list[AbstractedService] = field(default_factory=list)
    unmapped: list[UnmappedService] = field(default_factory=list)

    @property
    def has_evidence(self) -> bool:
        return bool(self.services or self.unmapped)

    def clouds(self) -> set[str]:
        return {s.cloud for s in self.services}

    def kinds(self) -> set[str]:
        return {s.abstraction for s in self.services}

    def kind_clouds(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for s in self.services:
            out.setdefault(s.abstraction, set()).add(s.cloud)
        return out


# ---------------------------------------------------------------------------
# Terraform resource map — (abstraction, cloud, service)


_TF_RESOURCE_MAP: dict[str, tuple[str, str, str]] = {
    # object_storage
    "aws_s3_bucket": ("object_storage", "aws", "s3"),
    "azurerm_storage_account": ("object_storage", "azure", "storage_account"),
    "azurerm_storage_data_lake_gen2_filesystem": (
        "object_storage",
        "azure",
        "adls_gen2",
    ),
    "google_storage_bucket": ("object_storage", "gcp", "gcs"),
    # stream
    "aws_kinesis_stream": ("stream", "aws", "kinesis"),
    "aws_msk_cluster": ("stream", "aws", "msk"),
    "azurerm_eventhub": ("stream", "azure", "eventhubs"),
    "azurerm_eventhub_namespace": ("stream", "azure", "eventhubs"),
    "google_pubsub_topic": ("stream", "gcp", "pubsub"),
    "google_pubsub_subscription": ("stream", "gcp", "pubsub"),
    # compute_engine
    "aws_emr_cluster": ("compute_engine", "aws", "emr"),
    "aws_emrserverless_application": ("compute_engine", "aws", "emr"),
    "aws_glue_job": ("compute_engine", "aws", "glue"),
    "azurerm_synapse_workspace": ("compute_engine", "azure", "synapse"),
    "azurerm_databricks_workspace": ("compute_engine", "azure", "databricks"),
    "azurerm_fabric_capacity": ("compute_engine", "azure", "fabric"),
    "azurerm_function_app": ("compute_engine", "azure", "functions"),
    "azurerm_linux_function_app": ("compute_engine", "azure", "functions"),
    "azurerm_windows_function_app": ("compute_engine", "azure", "functions"),
    "google_dataproc_cluster": ("compute_engine", "gcp", "dataproc"),
    "google_dataflow_job": ("compute_engine", "gcp", "dataflow"),
    "google_dataflow_flex_template_job": ("compute_engine", "gcp", "dataflow"),
    "google_cloudfunctions_function": ("compute_engine", "gcp", "functions"),
    "google_cloudfunctions2_function": ("compute_engine", "gcp", "functions"),
    "databricks_workspace": ("compute_engine", "databricks", "databricks"),
    # catalog
    "aws_glue_catalog_database": ("catalog", "aws", "glue"),
    "aws_glue_catalog_table": ("catalog", "aws", "glue"),
    "azurerm_purview_account": ("catalog", "azure", "purview"),
    "google_data_catalog_entry_group": ("catalog", "gcp", "datacatalog"),
    "google_dataplex_lake": ("catalog", "gcp", "dataplex"),
    "google_dataplex_zone": ("catalog", "gcp", "dataplex"),
    # orchestrator
    "azurerm_data_factory": ("orchestrator", "azure", "adf"),
    "azurerm_data_factory_pipeline": ("orchestrator", "azure", "adf"),
    "google_composer_environment": ("orchestrator", "gcp", "composer"),
    # operational_store
    "aws_dynamodb_table": ("operational_store", "aws", "dynamodb"),
    "azurerm_cosmosdb_account": ("operational_store", "azure", "cosmosdb"),
    "google_bigtable_instance": ("operational_store", "gcp", "bigtable"),
    # warehouse
    "aws_redshift_cluster": ("warehouse", "aws", "redshift"),
    "aws_redshiftserverless_workgroup": ("warehouse", "aws", "redshift"),
    "google_bigquery_dataset": ("warehouse", "gcp", "bigquery"),
    "azurerm_synapse_sql_pool": ("warehouse", "azure", "synapse_sql"),
    "snowflake_database": ("warehouse", "snowflake", "snowflake"),
    "snowflake_warehouse": ("warehouse", "snowflake", "snowflake"),
}

# attr keys that signal replication/migration linkage
_LINK_RE = re.compile(r"replicat|mirror|cross.?region|\bdr\b|failover", re.I)
_ENCRYPT_RE = re.compile(r"encrypt|kms|cmk|sse_", re.I)
_PUBLIC_RE = re.compile(r"public|internet.?facing|allow_public", re.I)
_REGION_KEYS = ("region", "location", "availability_zone")

_MAX_VENDOR_ATTRS = 8


def _flag(attrs: dict[str, Any], pat: re.Pattern[str]) -> str:
    for k, v in attrs.items():
        if not pat.search(str(k)):
            continue
        s = str(v).strip().lower()
        return "no" if s in ("false", "0", "disabled", "none", "") else "yes"
    return ""


def _linked(attrs: dict[str, Any], body: str) -> bool:
    return any(_LINK_RE.search(str(k)) for k in attrs) or bool(_LINK_RE.search(body))


def _scan_terraform_blocks(ctx: ProjectContext, model: CloudAbstractionModel) -> None:
    from forge_doctor.analyzers.terraform_model import terraform_model

    tf = terraform_model(ctx)
    for block in tf.blocks:
        if block.kind != "resource" or len(block.labels) < 2:
            continue
        rtype, rname = block.labels[0], block.labels[1]
        mapped = _TF_RESOURCE_MAP.get(rtype)
        if mapped is None:
            continue
        abstraction, cloud, service = mapped
        attrs = block.attrs or {}
        passthrough = tuple(
            sorted(
                (str(k), str(v))
                for k, v in attrs.items()
                if k in _REGION_KEYS
                or _LINK_RE.search(str(k))
                or _ENCRYPT_RE.search(str(k))
                or _PUBLIC_RE.search(str(k))
            )
        )[:_MAX_VENDOR_ATTRS]
        region = ""
        for key in _REGION_KEYS:
            if isinstance(attrs.get(key), str):
                region = str(attrs[key])
                break
        name = str(attrs.get("name") or attrs.get("bucket") or rname)
        model.services.append(
            AbstractedService(
                abstraction=abstraction,
                cloud=cloud,
                service=service,
                name=name,
                file=block.file,
                region=region,
                encrypted=_flag(attrs, _ENCRYPT_RE),
                public=_flag(attrs, _PUBLIC_RE),
                linked=_linked(attrs, block.body),
                vendor_attrs=passthrough,
            )
        )


# ---------------------------------------------------------------------------
# Graph-entity fold (vendor-attributed entities not seen via Terraform)


_WAREHOUSE_PLATFORM_CLOUD = {
    "snowflake": "snowflake",
    "bigquery": "gcp",
    "redshift": "aws",
    "databricks": "databricks",
}

# graph domain -> (abstraction, cloud, service); warehouse resolves via platform
_DOMAIN_MAP: dict[str, tuple[str, str, str]] = {
    "kinesis": ("stream", "aws", "kinesis"),
    "kafka": ("stream", "vendor", "kafka"),
    "dynamodb": ("operational_store", "aws", "dynamodb"),
    "glue": ("catalog", "aws", "glue"),
    "lakeformation": ("catalog", "aws", "lakeformation"),
}

# data-bearing kinds whose domain should resolve to an abstraction; platform
# domains outside the map surface in `unmapped` (CLOUD001 blind spots)
_UNMAPPED_DATA_KINDS = {"table", "dataset", "stream", "warehouse", "catalog"}
_PLATFORM_DOMAINS = {
    "warehouse",
    "kinesis",
    "kafka",
    "dynamodb",
    "glue",
    "lakeformation",
    "neptune",
    "trino",
    "analytical",
    "search",
    "metadata",
}


# warehouse-domain entity kinds -> abstraction (vendor objects fold too)
_WAREHOUSE_KIND_ABSTRACTION = {
    "warehouse": "warehouse",
    "table": "warehouse",
    "view": "warehouse",
    "dataset": "warehouse",
    "storage_location": "object_storage",
    "stream": "stream",
    "task": "compute_engine",
    "compute_job": "compute_engine",
    "infrastructure_resource": "compute_engine",
}


def _scan_graph(ctx: ProjectContext, model: CloudAbstractionModel) -> None:
    from forge_doctor.analyzers.platform_graph_builder import build_platform_graph

    try:
        g = build_platform_graph(ctx)
    except Exception:
        return
    tf_names = {(s.service, s.name.lower()) for s in model.services}
    for e in g.entities():
        attrs = dict(e.attrs)
        if e.domain == "warehouse":
            platform = str(attrs.get("platform") or "")
            if not platform:
                continue  # vendor-neutral row — no service claim
            if platform not in _WAREHOUSE_PLATFORM_CLOUD:
                model.unmapped.append(
                    UnmappedService(
                        domain=e.domain,
                        kind=f"warehouse:{platform}",
                        name=e.identifier,
                        file=e.file,
                    )
                )
                continue
            abstraction = _WAREHOUSE_KIND_ABSTRACTION.get(e.kind.value)
            if abstraction is None:
                continue  # principals/schemas — not migration services
            svc = AbstractedService(
                abstraction=abstraction,
                cloud=_WAREHOUSE_PLATFORM_CLOUD[platform],
                service=platform,
                name=e.identifier,
                file=e.file or Path("."),
            )
            if (svc.service, svc.name.lower()) not in tf_names:
                model.services.append(svc)
            continue
        mapped = _DOMAIN_MAP.get(e.domain)
        if mapped:
            abstraction, cloud, service = mapped
            if (service, e.identifier.lower()) in tf_names:
                continue
            model.services.append(
                AbstractedService(
                    abstraction=abstraction,
                    cloud=cloud,
                    service=service,
                    name=e.identifier,
                    file=e.file or Path("."),
                )
            )
            continue
        if e.kind.value in _UNMAPPED_DATA_KINDS and e.domain in _PLATFORM_DOMAINS:
            model.unmapped.append(
                UnmappedService(
                    domain=e.domain,
                    kind=e.kind.value,
                    name=e.identifier,
                    file=e.file,
                )
            )


# ---------------------------------------------------------------------------
# Entry point


def abstractions_model(ctx: ProjectContext) -> CloudAbstractionModel:
    """Memoized vendor-neutral abstraction view over ctx evidence."""
    cached = getattr(ctx, _CACHE_ATTR, None)
    if cached is not None:
        return cast(CloudAbstractionModel, cached)
    model = CloudAbstractionModel()
    _scan_terraform_blocks(ctx, model)
    _scan_graph(ctx, model)
    setattr(ctx, _CACHE_ATTR, model)
    return model
