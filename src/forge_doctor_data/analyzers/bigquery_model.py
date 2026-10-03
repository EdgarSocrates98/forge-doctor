"""BigQuery vendor model — populates warehouse evidence for `bigquery`
(spec 214).

Evidence planes:

- **Terraform** ``google_bigquery_*`` / ``google_biglake_*`` resources —
  datasets, tables (partitioning/clustering live in nested blocks, mined
  from the block body), views, reservations/capacity (slots), dataset
  access grants (authorized views), connections (BigLake).
- **SQL DDL** — BigQuery-only statement shapes from the shared sqlglot
  index: ``CREATE SCHEMA|TABLE|VIEW|MATERIALIZED VIEW|EXTERNAL TABLE|
  RESERVATION|CAPACITY`` with ``PARTITION BY``/``CLUSTER BY``/``OPTIONS``.
  Most of these arrive as ``command`` statements under the generic parse;
  ``CREATE MATERIALIZED VIEW`` parses structurally (``create``).
- **Observed metadata exports** — ``INFORMATION_SCHEMA``-style JSON/CSV
  rows under ``bigquery/`` or ``.forge-doctor-data/evidence/`` (claimed only
  with a positive BigQuery field signal), plus ``information_schema*`` /
  ``jobs*`` filenames. Tolerant loader: recognized shapes become rows;
  unknown shapes land in ``unparsed`` — never dropped silently.

Attribution: a ``.sql`` file counts as BigQuery only when it contains a
vendor-exclusive marker (``PARTITION BY``, ``OPTIONS(...)``, backtick-
qualified `` `project.dataset.table` `` identifiers, ``_PARTITIONTIME`` /
``_PARTITIONDATE``, ``CREATE RESERVATION|CAPACITY``, ``WITH CONNECTION``,
``NOT ENFORCED`` constraints). ``CLUSTER BY`` alone is *not* a marker —
Snowflake uses it too; bare-cluster files stay unclaimed (honest
unknown). Non-BigQuery SQL must not trip BQ checks (the lab adversarial
case pins this).
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from forge_doctor_data.core.context import ProjectContext

_CACHE_ATTR = "_forge_doctor_data_bigquery_model"

PLATFORM = "bigquery"

# ---------------------------------------------------------------------------
# Model rows


@dataclass(frozen=True)
class BigQueryObject:
    """A vendor object: dataset|table|view|external_table|reservation|..."""

    kind: str
    name: str
    file: Path | None = None
    line: int | None = None
    source: str = "sql"  # sql | terraform | observed
    attrs: tuple[tuple[str, str], ...] = ()

    def attr(self, key: str, default: str = "") -> str:
        return dict(self.attrs).get(key, default)


@dataclass(frozen=True)
class BigQueryQuery:
    """An authored query in a BigQuery-attributed file."""

    file: Path
    line: int | None
    text: str
    tables_read: tuple[str, ...] = ()
    tables_written: tuple[str, ...] = ()
    source: str = "file"  # file | job (observed job history)


@dataclass(frozen=True)
class ObservedRow:
    """One row from an exported metadata artifact (INFORMATION_SCHEMA)."""

    shape: str  # tables|partitions|jobs|columns|datasets|other
    file: Path
    fields: tuple[tuple[str, str], ...]

    def get(self, *keys: str) -> str:
        d = {k.lower(): v for k, v in self.fields}
        for key in keys:
            if key.lower() in d:
                return d[key.lower()]
        return ""


@dataclass
class BigQueryProjectModel:
    """All BigQuery evidence for a project."""

    datasets: list[BigQueryObject] = field(default_factory=list)
    tables: list[BigQueryObject] = field(default_factory=list)
    views: list[BigQueryObject] = field(default_factory=list)  # + materialized
    objects: list[BigQueryObject] = field(default_factory=list)  # reservation/...
    queries: list[BigQueryQuery] = field(default_factory=list)
    observed: list[ObservedRow] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)
    sql_files: set[Path] = field(default_factory=set)  # bigquery-attributed

    @property
    def has_evidence(self) -> bool:
        return bool(
            self.datasets
            or self.tables
            or self.views
            or self.objects
            or self.queries
            or self.observed
        )

    def by_kind(self, kind: str) -> list[BigQueryObject]:
        return [o for o in self.objects if o.kind == kind]

    def observed_tables(self) -> list[ObservedRow]:
        return [r for r in self.observed if r.shape == "tables"]

    def partitioned_tables(self) -> set[str]:
        """Names of tables with partitioning evidence (declared or observed)."""
        out = {t.name for t in self.tables if t.attr("partition_by") or t.attr("partitioning")}
        for row in self.observed:
            partitioned_row = row.shape == "partitions" or (
                row.shape == "tables"
                and row.get("partition_type", "partitioning_type", "type").upper()
                not in {"", "NONE"}
            )
            if partitioned_row:
                name = row.get("table_name", "name")
                if name:
                    out.add(name)
        return out


# ---------------------------------------------------------------------------
# SQL evidence — vendor markers + statement classification.

_VENDOR_MARKERS = re.compile(
    r"(partition\s+by|options\s*\(|`[^`\s]+\.[^`]+`|_partition(time|date)"
    r"|create\s+(?:or\s+replace\s+)?(reservation|capacity|assignment)\b"
    r"|with\s+connection|biglake|not\s+enforced)",
    re.IGNORECASE,
)
_BACKTICK_REF = re.compile(r"`([^`]+)`")

_CREATE_RE = re.compile(
    r"create\s+(?:or\s+replace\s+)?"
    r"(?:(?:temp|temporary|transient)\s+)*"
    r"(materialized\s+view|external\s+table|schema|table|view|"
    r"reservation|capacity|assignment|routine|function|procedure)"
    r"\s+(?:if\s+not\s+exists\s+)?([\w.$`-]+)",
    re.IGNORECASE,
)
_KV_RE = re.compile(r"(\w+)\s*=\s*'([^']*)'|(\w+)\s*=\s*(\w[\w.]*)")
_OPTIONS_RE = re.compile(r"options\s*\(([^)]*)\)", re.IGNORECASE | re.DOTALL)
_PARTITION_RE = re.compile(
    r"partition\s+by\s+(.+?)(?:\bcluster\s+by\b|\boptions\b|\bas\b\s+select|$)",
    re.IGNORECASE | re.DOTALL,
)
_CLUSTER_RE = re.compile(
    r"cluster\s+by\s+(.+?)(?:\boptions\b|\bas\b\s+select|$)",
    re.IGNORECASE | re.DOTALL,
)


def _ddl_attrs(text: str) -> tuple[tuple[str, str], ...]:
    """Property assignments: ``OPTIONS(k=v)`` plus partition/cluster keys."""
    out: dict[str, str] = {}
    for m in _OPTIONS_RE.finditer(text):
        for kv in _KV_RE.finditer(m.group(1)):
            key = kv.group(1) or kv.group(3)
            val = kv.group(2) if kv.group(2) is not None else kv.group(4)
            if key:
                out[key.lower()] = val or ""
    pm = _PARTITION_RE.search(text)
    if pm:
        out["partition_by"] = " ".join(pm.group(1).split())
    cm = _CLUSTER_RE.search(text)
    if cm:
        out["cluster_by"] = " ".join(cm.group(1).split())
    return tuple(sorted(out.items()))


_OBJECT_KIND = {
    "schema": "dataset",
    "table": "table",
    "external table": "external_table",
    "view": "view",
    "materialized view": "materialized_view",
    "reservation": "reservation",
    "capacity": "capacity",
    "assignment": "assignment",
    "routine": "routine",
    "function": "routine",
    "procedure": "routine",
}


def _tables_read_refs(text: str) -> tuple[str, ...]:
    """`proj.ds.tbl` backtick refs read in a statement (best-effort)."""
    return tuple(sorted(set(_BACKTICK_REF.findall(text))))


def _place(model: BigQueryProjectModel, obj: BigQueryObject) -> None:
    if obj.kind == "dataset":
        model.datasets.append(obj)
    elif obj.kind in {"table", "external_table"}:
        model.tables.append(obj)
    elif obj.kind in {"view", "materialized_view"}:
        model.views.append(obj)
    else:
        model.objects.append(obj)


def _from_sql(model: BigQueryProjectModel, ctx: ProjectContext) -> None:
    from forge_doctor_data.analyzers.sql_ast import SQLGLOT_AVAILABLE, analyze_sql

    if not SQLGLOT_AVAILABLE:
        return
    index = analyze_sql(ctx)
    # First pass: attribute files containing a vendor-exclusive marker.
    for stmt in index.statements:
        if _VENDOR_MARKERS.search(stmt.text):
            model.sql_files.add(stmt.file)
    for stmt in index.statements:
        if stmt.file not in model.sql_files:
            continue
        text = stmt.text
        m = _CREATE_RE.search(text)
        if stmt.kind in {"command", "create"} and m:
            kind = " ".join(m.group(1).lower().split())
            # `external` sits outside the kind capture in the regex tail.
            if kind == "table" and re.search(
                r"create\s+(or\s+replace\s+)?external\s+table", text, re.IGNORECASE
            ):
                kind = "external table"
            name = m.group(2).strip("`")
            attrs = _ddl_attrs(text)
            reads = stmt.tables_read or _tables_read_refs(text)
            if reads:
                attrs += (("tables_read", ",".join(reads)),)
            obj = BigQueryObject(
                _OBJECT_KIND.get(kind, kind), name, stmt.file, stmt.line, "sql", attrs
            )
            _place(model, obj)
        elif stmt.kind in {"select", "merge", "insert", "update", "delete"}:
            reads = stmt.tables_read or _tables_read_refs(text)
            model.queries.append(
                BigQueryQuery(stmt.file, stmt.line, text, reads, stmt.tables_written, "file")
            )


# ---------------------------------------------------------------------------
# Terraform evidence — google_bigquery_* / google_biglake_* resources.

_TF_BIGQUERY: dict[str, str] = {
    "google_bigquery_dataset": "dataset",
    "google_bigquery_table": "table",
    "google_bigquery_routine": "routine",
    "google_bigquery_job": "job",
    "google_bigquery_reservation": "reservation",
    "google_bigquery_reservation_assignment": "assignment",
    "google_bigquery_capacity_commitment": "capacity",
    "google_bigquery_bi_reservation": "bi_reservation",
    "google_bigquery_data_transfer_config": "transfer",
    "google_bigquery_connection": "connection",
    "google_bigquery_dataset_access": "dataset_access",
    "google_bigquery_analytics_hub_data_exchange": "data_exchange",
    "google_bigquery_analytics_hub_listing": "listing",
    "google_biglake_catalog": "biglake_catalog",
    "google_biglake_database": "biglake_database",
    "google_biglake_table": "external_table",
}


def _tf_attrs(res: Any) -> tuple[tuple[str, str], ...]:
    out: dict[str, str] = {
        k.lower(): str(v)
        for k, v in res.attrs.items()
        if isinstance(v, (str, int, float, bool)) and not k.startswith("_")
    }
    body = getattr(res, "body", "") or ""
    # Nested HCL blocks carry the semantics the scalar attrs don't.
    for marker, attr in (
        ("time_partitioning", "partitioning"),
        ("range_partitioning", "partitioning"),
        ("hive_partitioning_options", "partitioning"),
        ("clustering", "clustering"),
    ):
        if re.search(rf"\b{marker}\b", body):
            out[attr] = marker
    pm = re.search(r"(?:time|range|hive)_partitioning[^{]*\{[^}]*field\s*=\s*\"(\w+)\"", body)
    if pm:
        out["partition_by"] = pm.group(1)
    cm = re.search(r"clustering\s*=\s*\[([^\]]*)\]", body)
    if cm:
        out["cluster_by"] = cm.group(1).strip()
    if re.search(r"\bview\s*\{", body) and "dataset_access" in getattr(res, "address", ""):
        out["authorized_view"] = "true"
    if re.search(r"all(?:users|authorizedusers)", body, re.IGNORECASE):
        out["public_access"] = "true"
    return tuple(sorted(out.items()))


def _from_terraform(model: BigQueryProjectModel, ctx: ProjectContext) -> None:
    from forge_doctor_data.analyzers.terraform_model import terraform_model

    for res in terraform_model(ctx).resources:
        if not res.labels:
            continue
        kind = _TF_BIGQUERY.get(res.labels[0])
        if kind is None:
            continue
        attr_name = (
            res.attrs.get("name") or res.attrs.get("table_id") or res.attrs.get("dataset_id")
        )
        name = (
            str(attr_name)
            if isinstance(attr_name, str) and attr_name
            else (res.labels[1] if len(res.labels) > 1 else res.address)
        )
        _place(
            model,
            BigQueryObject(kind, name, res.file, res.line, "terraform", _tf_attrs(res)),
        )


# ---------------------------------------------------------------------------
# Observed metadata exports — tolerant loader. Claim rules: a file under
# bigquery/ is always BigQuery; under .forge-doctor-data/evidence/ it needs a
# BigQuery-exclusive field signal so generic exports stay unclaimed.

_EXPORT_DIR_RE = re.compile(r"(^|/)(bigquery|\.forge-doctor-data/evidence)(/|$)", re.IGNORECASE)
_EXPORT_NAMES = re.compile(r"information_schema|jobs_by|partitions|bq_", re.IGNORECASE)
_BQ_FIELD_SIGNAL = re.compile(
    r"project_id|dataset_id|table_catalog|size_bytes|logical_bytes|"
    r"partition_id|total_bytes_(billed|processed)|total_slot_ms|job_id|"
    r"bi_engine|statement_type|ddl|location|creation_time",
    re.IGNORECASE,
)


def _candidate_exports(ctx: ProjectContext) -> list[Path]:
    out: list[Path] = []
    for rel in sorted(ctx.files):
        if rel.suffix.lower() not in {".json", ".csv", ".jsonl", ".ndjson"}:
            continue
        posix = rel.as_posix()
        if _EXPORT_DIR_RE.search(posix) or _EXPORT_NAMES.search(rel.name):
            out.append(rel)
    return out


def _shape_of(fields: dict[str, str]) -> str:
    keys = {k.lower() for k in fields}
    if keys & {"job_id", "total_bytes_billed", "total_slot_ms", "statement_type"}:
        return "jobs"
    if "partition_id" in keys or (
        keys & {"partition_type", "partitioning_type"} and "table_name" in keys
    ):
        return "partitions"
    if {"table_name", "table_schema"} <= keys or (
        "table_name" in keys and (keys & {"row_count", "size_bytes", "logical_bytes", "ddl"})
    ):
        return "tables"
    if "column_name" in keys:
        return "columns"
    if keys & {"dataset_id", "dataset_name", "schema_name"}:
        return "datasets"
    return "other"


def _rows_from(path: Path, text: str) -> tuple[list[dict[str, str]], bool]:
    """Extract flat row dicts; ``False`` when the shape is unrecognized."""
    try:
        doc = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        doc = None
    rows: list[dict[str, Any]] = []
    if isinstance(doc, list):
        rows = [r for r in doc if isinstance(r, dict)]
    elif isinstance(doc, dict):
        for key in ("rows", "data", "results", "tables", "jobs", "partitions"):
            if isinstance(doc.get(key), list):
                rows = [r for r in doc[key] if isinstance(r, dict)]
                break
        if not rows and all(isinstance(v, (str, int, float, bool)) for v in doc.values()):
            rows = [doc]
    if rows:
        return [{str(k): str(v) for k, v in r.items()} for r in rows], True
    if path.suffix.lower() == ".csv":
        try:
            reader = csv.DictReader(io.StringIO(text))
            csv_rows = [dict(r) for r in reader]
        except csv.Error:
            return [], False
        if csv_rows and reader.fieldnames:
            return [{str(k): str(v) for k, v in r.items() if k is not None} for r in csv_rows], True
    return [], False


def _claimed_by_bigquery(rel: Path, rows: list[dict[str, str]]) -> bool:
    """Positive vendor signal required for shared evidence dirs."""
    if re.search(r"(^|/)bigquery(/|$)", rel.as_posix(), re.IGNORECASE):
        return True
    return any(any(_BQ_FIELD_SIGNAL.search(k) for k in row) for row in rows[:5])


def _from_exports(model: BigQueryProjectModel, ctx: ProjectContext) -> None:
    for rel in _candidate_exports(ctx):
        text = ctx.read_text(rel)
        if text is None:
            continue
        rows, recognized = _rows_from(rel, text)
        if not recognized or not rows:
            model.unparsed.append(rel.as_posix())
            continue
        if not _claimed_by_bigquery(rel, rows):
            continue  # e.g. a snowflake export in a shared evidence dir
        for row in rows:
            model.observed.append(ObservedRow(_shape_of(row), rel, tuple(sorted(row.items()))))


def bigquery_model(ctx: ProjectContext) -> BigQueryProjectModel:
    """Memoized BigQuery model over ctx evidence."""
    cached = getattr(ctx, _CACHE_ATTR, None)
    if cached is not None:
        return cast(BigQueryProjectModel, cached)
    model = BigQueryProjectModel()
    _from_terraform(model, ctx)
    _from_sql(model, ctx)
    _from_exports(model, ctx)
    # Observed job history doubles as query evidence (BQ002 error tier).
    for row in model.observed:
        if row.shape != "jobs":
            continue
        text = row.get("query", "query_text", "statement")
        if text:
            reads = _tables_read_refs(text)
            writes = _tables_read_refs(text.split("into", 1)[1]) if " into " in text.lower() else ()
            model.queries.append(BigQueryQuery(row.file, None, text, reads, writes, "job"))
    setattr(ctx, _CACHE_ATTR, model)
    return model
