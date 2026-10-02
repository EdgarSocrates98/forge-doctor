"""Databricks first-class model: workspace, jobs, clusters, SQL warehouses,
Unity Catalog, external locations, storage credentials, volumes, pipelines
(Lakeflow), and asset bundles.

Sources: Terraform ``databricks_*`` resources, ``databricks.yml`` asset
bundles (minimal YAML), and Python evidence (``databricks.sdk`` imports,
``dbutils`` calls, ``DeltaTable`` APIs, notebook magics).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forge_doctor.analyzers.index import project_index
from forge_doctor.analyzers.terraform_model import terraform_model
from forge_doctor.core.context import ProjectContext


@dataclass(frozen=True)
class DatabricksJob:
    name: str
    file: Path
    line: int
    task_count: int = 0
    uses_job_cluster: bool = False
    uses_existing_cluster: bool = False
    has_notebook_task: bool = False
    has_pipeline_task: bool = False
    schedule: str = ""


@dataclass(frozen=True)
class DatabricksCluster:
    name: str
    dbr_version: str
    node_type: str
    file: Path
    line: int
    is_job_cluster: bool = False
    autoscale: bool = False
    num_workers: int = 0
    spot: bool = False
    serverless: bool = False


@dataclass(frozen=True)
class DatabricksWarehouse:
    name: str
    cluster_size: str
    file: Path
    line: int
    serverless: bool = False


@dataclass(frozen=True)
class UnityCatalogObject:
    """catalog | external_location | storage_credential | volume | schema."""

    kind: str
    name: str
    file: Path
    line: int


@dataclass(frozen=True)
class DatabricksPipeline:
    name: str
    file: Path
    line: int
    continuous: bool = False
    development: bool = False
    serverless: bool = False
    target_catalog: str = ""


@dataclass
class DatabricksProjectModel:
    jobs: list[DatabricksJob] = field(default_factory=list)
    clusters: list[DatabricksCluster] = field(default_factory=list)
    warehouses: list[DatabricksWarehouse] = field(default_factory=list)
    uc_objects: list[UnityCatalogObject] = field(default_factory=list)
    pipelines: list[DatabricksPipeline] = field(default_factory=list)
    bundles: list[Path] = field(default_factory=list)
    workspaces: int = 0
    dbutils_calls: list[str] = field(default_factory=list)
    notebook_files: list[Path] = field(default_factory=list)
    sdk_imports: list[str] = field(default_factory=list)
    has_databricks: bool = False

    def dbr_versions(self) -> list[str]:
        return sorted({c.dbr_version for c in self.clusters if c.dbr_version})


_DBX_RESOURCES = {
    "databricks_job",
    "databricks_cluster",
    "databricks_instance_pool",
    "databricks_sql_warehouse",
    "databricks_sql_endpoint",
    "databricks_catalog",
    "databricks_schema",
    "databricks_external_location",
    "databricks_storage_credential",
    "databricks_volume",
    "databricks_pipeline",
    "databricks_notebook",
    "databricks_repo",
    "databricks_workspace_conf",
    "databricks_mws_workspaces",
    "databricks_grants",
    "databricks_entitlements",
    "databricks_group",
    "databricks_user",
    "databricks_service_principal",
}
_UC_KINDS = {
    "databricks_catalog": "catalog",
    "databricks_schema": "schema",
    "databricks_external_location": "external_location",
    "databricks_storage_credential": "storage_credential",
    "databricks_volume": "volume",
    "databricks_registered_model": "registered_model",
}


def _sub_attrs(body: str, block: str) -> list[dict[str, Any]]:
    from forge_doctor.analyzers.hcl_lite import _brace_block, _flat_attrs

    out: list[dict[str, Any]] = []
    for m in re.finditer(rf"\b{block}\s*\{{", body):
        sub, _ = _brace_block(body, m.end() - 1)
        out.append(_flat_attrs(sub, 0))
    return out


def _spark_version(attrs: dict[str, Any]) -> str:
    return str(attrs.get("spark_version") or "")


def _tf_job(b: Any) -> DatabricksJob:
    attrs, body = b.attrs, b.body
    tasks = _sub_attrs(body, "task") + _sub_attrs(body, "for_each_task")
    job_clusters = _sub_attrs(body, "job_cluster")
    existing = "existing_cluster_id" in body
    return DatabricksJob(
        name=str(attrs.get("name") or (b.labels[-1] if b.labels else "")),
        file=Path(b.file),
        line=b.line,
        task_count=len(tasks),
        uses_job_cluster=bool(job_clusters),
        uses_existing_cluster=existing,
        has_notebook_task="notebook_task" in body,
        has_pipeline_task="pipeline_task" in body,
        schedule=str(attrs.get("schedule") or ""),
    )


def _tf_cluster(b: Any, job_cluster: bool = False) -> DatabricksCluster:
    attrs, body = b.attrs, b.body
    autoscale = bool(_sub_attrs(body, "autoscale")) or bool(re.search(r"autoscale\s*[{=]", body))
    spot = bool(
        re.search(r"spot_(instances|price)|aws_attributes.*spot", body, re.IGNORECASE)
        or attrs.get("spot_instances")
    )
    return DatabricksCluster(
        name=str(
            attrs.get("name") or attrs.get("cluster_name") or (b.labels[-1] if b.labels else "")
        ),
        dbr_version=_spark_version(attrs),
        node_type=str(attrs.get("node_type_id") or attrs.get("node_type") or ""),
        file=Path(b.file),
        line=b.line,
        is_job_cluster=job_cluster,
        autoscale=autoscale,
        num_workers=int(attrs.get("num_workers") or 0)
        if str(attrs.get("num_workers") or "0").isdigit()
        else 0,
        spot=spot,
        serverless=bool(attrs.get("serverless")),
    )


def _tf_pipeline(b: Any) -> DatabricksPipeline:
    attrs, body = b.attrs, b.body
    return DatabricksPipeline(
        name=str(attrs.get("name") or (b.labels[-1] if b.labels else "")),
        file=Path(b.file),
        line=b.line,
        continuous=str(attrs.get("continuous") or "").lower() == "true",
        development=str(attrs.get("development") or "").lower() == "true",
        serverless="serverless" in body.lower(),
        target_catalog=str(attrs.get("catalog") or attrs.get("target") or ""),
    )


def _bundle_fields(text: str) -> dict[str, Any]:
    """Minimal databricks.yml field extraction (stdlib, regex-level)."""
    out: dict[str, Any] = {"resources": []}
    m = re.search(r"^bundle:\s*\n\s+name:\s*(.+)$", text, re.MULTILINE)
    if m:
        out["name"] = m.group(1).strip().strip("\"'")
    out["targets"] = re.findall(
        r"^  ([a-zA-Z_][\w-]*):\s*$",
        text[text.find("targets:") :] if "targets:" in text else "",
        re.MULTILINE,
    )
    res_sec = text[text.find("resources:") :] if "resources:" in text else ""
    out["resources"] = re.findall(r"^    ([a-zA-Z_][\w-]*):\s*$", res_sec, re.MULTILINE)
    return out


def databricks_model(ctx: ProjectContext) -> DatabricksProjectModel:
    """Fuse Databricks evidence (TF / bundles / code) into one model."""
    model = DatabricksProjectModel()
    tf = terraform_model(ctx)

    for b in tf.resources:
        rtype = b.labels[0] if b.labels else ""
        if rtype not in _DBX_RESOURCES and rtype not in _UC_KINDS:
            continue
        file, line = Path(b.file), b.line
        if rtype == "databricks_job":
            model.jobs.append(_tf_job(b))
            for nc in _sub_attrs(b.body, "new_cluster"):
                model.clusters.append(
                    DatabricksCluster(
                        name=f"{b.labels[-1]}#new_cluster" if b.labels else "new_cluster",
                        dbr_version=str(nc.get("spark_version") or ""),
                        node_type=str(nc.get("node_type_id") or ""),
                        file=file,
                        line=line,
                        is_job_cluster=True,
                        autoscale=bool(_sub_attrs(str(nc), "autoscale")) or "autoscale" in str(nc),
                        num_workers=int(nc.get("num_workers") or 0)
                        if str(nc.get("num_workers") or "0").isdigit()
                        else 0,
                        spot="spot" in str(nc).lower(),
                    )
                )
        elif rtype == "databricks_cluster":
            model.clusters.append(_tf_cluster(b))
        elif rtype in ("databricks_sql_warehouse", "databricks_sql_endpoint"):
            model.warehouses.append(
                DatabricksWarehouse(
                    name=str(b.attrs.get("name") or (b.labels[-1] if b.labels else "")),
                    cluster_size=str(b.attrs.get("cluster_size") or ""),
                    file=file,
                    line=line,
                    serverless="serverless" in b.body.lower()
                    or str(b.attrs.get("warehouse_type") or "") == "SERVERLESS",
                )
            )
        elif rtype == "databricks_pipeline":
            model.pipelines.append(_tf_pipeline(b))
        elif rtype in _UC_KINDS:
            model.uc_objects.append(
                UnityCatalogObject(
                    kind=_UC_KINDS[rtype],
                    name=str(b.attrs.get("name") or (b.labels[-1] if b.labels else "")),
                    file=file,
                    line=line,
                )
            )
        elif rtype == "databricks_mws_workspaces":
            model.workspaces += 1

    # Asset bundles: databricks.yml / bundle configs.
    for relative in sorted(ctx.files):
        if relative.name in ("databricks.yml", "databricks.yaml"):
            model.bundles.append(relative)

    # Python evidence: sdk imports, dbutils calls, notebook magics, delta APIs.
    index = project_index(ctx)
    dbutils: set[str] = set()
    sdk: set[str] = set()
    for module in index.modules.values():
        for imp in module.imports:
            if imp.module.startswith("databricks"):
                sdk.add(imp.module)
        for call in module.calls:
            dotted = call.dotted
            if dotted.startswith("dbutils.") or (call.receiver or "") == "dbutils":
                dbutils.add(call.name)
        text = ctx.read_text(module.file) or ""
        if any(magic in text for magic in ("%sql", "%python", "%run", "%md")):
            model.notebook_files.append(module.file)

    model.dbutils_calls = sorted(dbutils)
    model.sdk_imports = sorted(sdk)
    model.has_databricks = bool(
        model.jobs
        or model.clusters
        or model.warehouses
        or model.uc_objects
        or model.pipelines
        or model.bundles
        or model.workspaces
        or model.sdk_imports
        or model.dbutils_calls
    )
    return model
