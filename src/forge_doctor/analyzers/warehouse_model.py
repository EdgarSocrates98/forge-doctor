"""Vendor-neutral warehouse project model (spec 212).

``WarehouseProjectModel`` is the shared semantic surface the vendor
adapters (specs 213-215) populate. Evidence sources today:

- Terraform resources — declarative ``snowflake_*`` /
  ``google_bigquery_*`` / ``aws_redshift*`` kinds normalize to compute,
  databases, schemas, tables, and workload-management entries.
- SQL statements — ``CREATE TABLE/VIEW/SCHEMA/DATABASE`` shapes from the
  sqlglot index (optional ``[sql]`` extra) become tables, views,
  materialized views, external tables; other statements become queries.

Vendor specifics belong in the vendor adapters — a field that only
makes sense for one vendor lives in ``attrs``, never a top-level field.
The model is empty when no warehouse evidence exists, so non-warehouse
projects produce no graph entities and no findings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext

_CACHE_ATTR = "_forge_doctor_warehouse_model"


@dataclass(frozen=True)
class WarehouseCompute:
    """An execution resource: warehouse, cluster, workgroup, reservation."""

    name: str
    platform: str  # vendor id: snowflake|bigquery|redshift|<dialect>
    file: Path | None = None
    line: int | None = None
    attrs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class WarehouseNamespace:
    """A database or schema container (``kind`` distinguishes them)."""

    name: str
    platform: str
    kind: str  # "database" | "schema"
    file: Path | None = None
    line: int | None = None
    attrs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class WarehouseTable:
    """A table; ``external`` flags externally-managed storage."""

    name: str
    platform: str
    external: bool = False
    file: Path | None = None
    line: int | None = None
    attrs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class WarehouseView:
    """A view; ``materialized`` flags physical caching."""

    name: str
    platform: str
    materialized: bool = False
    tables_read: tuple[str, ...] = ()
    file: Path | None = None
    line: int | None = None
    attrs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class WarehouseQuery:
    """A non-DDL statement observed or authored against the warehouse."""

    name: str  # file:line
    platform: str
    tables_read: tuple[str, ...] = ()
    tables_written: tuple[str, ...] = ()
    file: Path | None = None
    line: int | None = None
    attrs: tuple[tuple[str, str], ...] = ()


@dataclass
class WarehouseProjectModel:
    """All warehouse evidence for a project, normalized vendor-neutral.

    ``workload_management``/``security``/``sharing``/``costs`` are part
    of the shape (spec 212) but only vendor-neutral rows populate them
    today — richer rows come from the vendor adapters.
    """

    platforms: tuple[str, ...] = ()
    compute: list[WarehouseCompute] = field(default_factory=list)
    namespaces: list[WarehouseNamespace] = field(default_factory=list)
    tables: list[WarehouseTable] = field(default_factory=list)
    views: list[WarehouseView] = field(default_factory=list)
    queries: list[WarehouseQuery] = field(default_factory=list)
    workload_management: list[dict[str, Any]] = field(default_factory=list)
    security: list[dict[str, Any]] = field(default_factory=list)
    sharing: list[dict[str, Any]] = field(default_factory=list)
    costs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_evidence(self) -> bool:
        return bool(
            self.compute
            or self.namespaces
            or self.tables
            or self.views
            or self.queries
            or self.workload_management
        )

    @property
    def databases(self) -> list[WarehouseNamespace]:
        return [n for n in self.namespaces if n.kind == "database"]

    @property
    def schemas(self) -> list[WarehouseNamespace]:
        return [n for n in self.namespaces if n.kind == "schema"]

    @property
    def materialized_views(self) -> list[WarehouseView]:
        return [v for v in self.views if v.materialized]

    @property
    def external_tables(self) -> list[WarehouseTable]:
        return [t for t in self.tables if t.external]

    def table_names(self) -> set[str]:
        return {t.name for t in self.tables}


# ---------------------------------------------------------------------------
# Terraform evidence — declarative resource-kind normalization.
# Maps Terraform resource type -> (platform, concept). Vendor-specific
# *semantics* stay in the adapters; this is literal kind mapping only.

_TF_KINDS: dict[str, tuple[str, str]] = {
    # snowflake
    "snowflake_warehouse": ("snowflake", "compute"),
    "snowflake_database": ("snowflake", "database"),
    "snowflake_schema": ("snowflake", "schema"),
    "snowflake_table": ("snowflake", "table"),
    "snowflake_external_table": ("snowflake", "external_table"),
    "snowflake_view": ("snowflake", "view"),
    "snowflake_materialized_view": ("snowflake", "materialized_view"),
    "snowflake_role": ("snowflake", "security"),
    "snowflake_grant_privileges_to_role": ("snowflake", "security"),
    "snowflake_grant_account_role": ("snowflake", "security"),
    # bigquery (datasets are the schema-level container)
    "google_bigquery_dataset": ("bigquery", "schema"),
    "google_bigquery_table": ("bigquery", "table"),
    "google_bigquery_reservation": ("bigquery", "workload"),
    # redshift (cluster carries the database)
    "aws_redshift_cluster": ("redshift", "compute"),
    "aws_redshiftserverless_workgroup": ("redshift", "compute"),
    "aws_redshiftserverless_namespace": ("redshift", "database"),
}

_WAREHOUSE_DIALECTS = {"snowflake", "bigquery", "redshift", "databricks"}


def _str_attrs(attrs: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """Flatten string/int/float attrs; skip complex values (honest, not lossy)."""
    return tuple(
        sorted(
            (k, str(v))
            for k, v in attrs.items()
            if isinstance(v, (str, int, float)) and not k.startswith("_")
        )
    )


def _from_terraform(model: WarehouseProjectModel, ctx: ProjectContext) -> None:
    from forge_doctor.analyzers.terraform_model import terraform_model

    platforms: set[str] = set()
    for res in terraform_model(ctx).resources:
        if not res.labels:
            continue
        mapping = _TF_KINDS.get(res.labels[0])
        if mapping is None:
            continue
        platform, concept = mapping
        platforms.add(platform)
        # The declared object name beats the Terraform logical name.
        attr_name = res.attrs.get("name")
        name = (
            str(attr_name)
            if isinstance(attr_name, str) and attr_name
            else (res.labels[1] if len(res.labels) > 1 else res.address)
        )
        attrs = _str_attrs(res.attrs)
        if concept == "compute":
            model.compute.append(WarehouseCompute(name, platform, res.file, res.line, attrs))
            # aws_redshift_cluster carries its database inline
            db = res.attrs.get("database_name")
            if isinstance(db, str) and db:
                model.namespaces.append(
                    WarehouseNamespace(db, platform, "database", res.file, res.line)
                )
        elif concept in {"database", "schema"}:
            model.namespaces.append(
                WarehouseNamespace(name, platform, concept, res.file, res.line, attrs)
            )
        elif concept == "table":
            model.tables.append(WarehouseTable(name, platform, False, res.file, res.line, attrs))
        elif concept == "external_table":
            model.tables.append(WarehouseTable(name, platform, True, res.file, res.line, attrs))
        elif concept == "view":
            model.views.append(WarehouseView(name, platform, False, (), res.file, res.line, attrs))
        elif concept == "materialized_view":
            model.views.append(WarehouseView(name, platform, True, (), res.file, res.line, attrs))
        elif concept == "workload":
            model.workload_management.append(
                {
                    "kind": "reservation",
                    "name": name,
                    "platform": platform,
                    "file": res.file.as_posix(),
                    "line": res.line,
                }
            )
        elif concept == "security":
            model.security.append(
                {
                    "kind": res.labels[0],
                    "name": name,
                    "platform": platform,
                    "file": res.file.as_posix(),
                    "line": res.line,
                }
            )
    model.platforms = tuple(sorted(set(model.platforms) | platforms))


# ---------------------------------------------------------------------------
# SQL evidence — DDL shapes and queries from the sqlglot index.

_KIND_BY_PREFIX = (
    ("materialized", "materialized_view"),
    ("external", "external_table"),
    ("view", "view"),
    ("table", "table"),
    ("schema", "schema"),
    ("database", "database"),
)


def _create_kind(text: str) -> str | None:
    """Classify a ``CREATE`` statement's object kind from its head."""
    head = " ".join(text.split()[:6]).lower()
    if not head.startswith("create"):
        return None
    for marker, kind in _KIND_BY_PREFIX:
        if f" {marker} " in f"{head} ":
            return kind
    return None


def _from_sql(model: WarehouseProjectModel, ctx: ProjectContext) -> None:
    from forge_doctor.analyzers.sql_ast import SQLGLOT_AVAILABLE, analyze_sql

    if not SQLGLOT_AVAILABLE:
        return
    index = analyze_sql(ctx)
    platforms: set[str] = set()
    for stmt in index.statements:
        # Generic-dialect statements stay in the `sql` domain (the `_sql`
        # graph adapter owns them); only a positive warehouse dialect
        # attributes a statement to the vendor-neutral warehouse model.
        platform = stmt.dialect if stmt.dialect in _WAREHOUSE_DIALECTS else "sql"
        if stmt.kind == "create":
            target = stmt.tables_written[0] if stmt.tables_written else ""
            kind = _create_kind(stmt.text)
            if platform != "sql" and kind is not None:
                platforms.add(platform)
            if kind in {"table", "external_table"} and target:
                model.tables.append(
                    WarehouseTable(target, platform, kind == "external_table", stmt.file, stmt.line)
                )
            elif kind in {"view", "materialized_view"} and target:
                model.views.append(
                    WarehouseView(
                        target,
                        platform,
                        kind == "materialized_view",
                        stmt.tables_read,
                        stmt.file,
                        stmt.line,
                    )
                )
            elif kind in {"schema", "database"} and target:
                model.namespaces.append(
                    WarehouseNamespace(target, platform, kind, stmt.file, stmt.line)
                )
        elif stmt.tables_read or stmt.tables_written:
            model.queries.append(
                WarehouseQuery(
                    f"{stmt.file.as_posix()}:{stmt.line}",
                    platform,
                    stmt.tables_read,
                    stmt.tables_written,
                    stmt.file,
                    stmt.line,
                )
            )
    model.platforms = tuple(sorted(set(model.platforms) | platforms))


def warehouse_model(ctx: ProjectContext) -> WarehouseProjectModel:
    """Memoized vendor-neutral warehouse model over ctx evidence."""
    cached = getattr(ctx, _CACHE_ATTR, None)
    if cached is not None:
        return cast(WarehouseProjectModel, cached)
    model = WarehouseProjectModel()
    _from_terraform(model, ctx)
    _from_sql(model, ctx)
    setattr(ctx, _CACHE_ATTR, model)
    return model
