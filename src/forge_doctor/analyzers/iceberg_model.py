"""Iceberg project model - evidence fused from AST index, SqlIndex, IaC, configs.

Follows V11: one model per scan, every ICE### check and the ``iceberg``
command group query it - no per-check parsing. Evidence kinds:

- ``format``   - `USING iceberg` / `format("iceberg")` markers
- ``catalog``  - `spark.sql.catalog.*` config, catalog-prefixed refs, IaC catalogs
- ``table``    - iceberg-marked table references
- ``operation``- merge/append/overwrite/insert/update/delete/read on iceberg tables
- ``property`` - table properties (`format-version`, partitioning, ...)
- ``maintenance``- `CALL <cat>.system.<proc>` ops (expire_snapshots, ...)
- ``runtime``  - IaC runtime pins (glue_version)
- ``config``   - other `spark.sql.*` / extension settings
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext

_CACHE_ATTR = "_forge_doctor_iceberg_model"

_CONF_SET_RE = "spark.sql."
_CALL_RE = re.compile(r"CALL\s+([\w.]+)\.system\.(\w+)\s*\(", re.IGNORECASE)
_TBLPROPS_RE = re.compile(r"TBLPROPERTIES\s*\(([^)]*)\)", re.IGNORECASE | re.DOTALL)
_PROP_PAIR_RE = re.compile(r"['\"]?([\w.-]+)['\"]?\s*=\s*['\"]([^'\"]*)['\"]")
_USING_RE = re.compile(r"\bUSING\s+(\w+)", re.IGNORECASE)
_OVERWRITE_RE = re.compile(r"\bOVERWRITE\b", re.IGNORECASE)
_PART_BY_RE = re.compile(r"\bPARTITIONED\s+BY\s*\(", re.IGNORECASE)
_TRANSFORMS = {
    "bucket",
    "truncate",
    "year",
    "years",
    "month",
    "months",
    "day",
    "days",
    "hour",
    "hours",
}
_CATALOG_IMPLS = ("glue", "rest", "hadoop", "hive", "nessie", "unity", "jdbc")
_LEGACY_WRITES = {
    "insertinto",
    "saveastable",
    "save",
}
_V2_WRITES = {"writeto", "createorreplace"}
_MAINTENANCE_PROCS = (
    "expire_snapshots",
    "rewrite_data_files",
    "rewrite_manifests",
    "remove_orphan_files",
    "rewrite_position_delete_files",
)
_WRITE_OPS = {"append", "overwrite", "overwritepartitions", "createorreplace"}


@dataclass(frozen=True)
class IcebergEvidence:
    """One observed Iceberg fact, with provenance."""

    kind: str  # format|catalog|table|operation|property|maintenance|runtime|config
    name: str  # table/catalog/op/property name
    value: str  # property value, or detail text
    file: Path
    line: int
    source: str  # python|sql|config|terraform|cloudformation


@dataclass
class IcebergProjectModel:
    """Everything the project evidences about Iceberg, in sorted order."""

    evidence: list[IcebergEvidence] = field(default_factory=list)

    def by_kind(self, kind: str) -> list[IcebergEvidence]:
        return [e for e in self.evidence if e.kind == kind]

    @property
    def has_iceberg(self) -> bool:
        return any(e.kind in {"format", "catalog", "table", "operation"} for e in self.evidence)

    @property
    def catalog_names(self) -> set[str]:
        return {e.name for e in self.by_kind("catalog")}

    @property
    def tables(self) -> dict[str, IcebergEvidence]:
        """First-seen evidence per table name, deterministic."""
        found: dict[str, IcebergEvidence] = {}
        for e in self.by_kind("table"):
            found.setdefault(e.name, e)
        return found

    @property
    def operations(self) -> list[IcebergEvidence]:
        return self.by_kind("operation")

    @property
    def operation_names(self) -> set[str]:
        return {e.name for e in self.operations}

    @property
    def properties(self) -> dict[str, tuple[str, IcebergEvidence]]:
        """property name -> (value, first-seen evidence)."""
        found: dict[str, tuple[str, IcebergEvidence]] = {}
        for e in self.by_kind("property"):
            found.setdefault(e.name, (e.value, e))
        return found

    @property
    def maintenance_names(self) -> set[str]:
        return {e.name for e in self.by_kind("maintenance")}

    @property
    def configs(self) -> dict[str, IcebergEvidence]:
        found: dict[str, IcebergEvidence] = {}
        for e in self.by_kind("config") + self.by_kind("catalog"):
            found.setdefault(e.name, e)
        return found

    @property
    def runtimes(self) -> dict[str, IcebergEvidence]:
        return {e.name: e for e in self.by_kind("runtime")}

    @property
    def has_writes(self) -> bool:
        return bool(
            self.operation_names & (_WRITE_OPS | {"insert", "merge", "update", "delete", "write"})
        )


def _catalog_of(name: str, catalog_names: set[str]) -> str | None:
    parts = name.split(".")
    if len(parts) >= 2 and parts[0] in catalog_names:
        return parts[0]
    return None


def _py_static_evidence(index: object) -> tuple[list[IcebergEvidence], set[Path]]:
    """Pass A: catalogs, configs and direct `iceberg` markers from call sites.

    Returns evidence plus the set of files carrying a format marker -
    writeTo-chain ops in those files count as iceberg operations.
    """
    from forge_doctor.analyzers.index import ProjectIndex

    evidence: list[IcebergEvidence] = []
    iceberg_files: set[Path] = set()
    assert isinstance(index, ProjectIndex)
    for relative, module in sorted(index.modules.items(), key=lambda kv: kv[0].as_posix()):
        for site in module.calls:
            args = site.args
            if site.dotted.endswith("conf.set") and args and args[0].startswith(_CONF_SET_RE):
                key = args[0]
                value = args[1] if len(args) > 1 else ""
                if key.startswith("spark.sql.catalog."):
                    name = key[len("spark.sql.catalog.") :].split(".")[0]
                    evidence.append(
                        IcebergEvidence("catalog", name, value, relative, site.line, "python")
                    )
                else:
                    evidence.append(
                        IcebergEvidence("config", key, value, relative, site.line, "python")
                    )
            if any("iceberg" in a.lower() for a in (*args, *(v for _, v in site.kwargs))):
                evidence.append(
                    IcebergEvidence("format", "iceberg", site.dotted, relative, site.line, "python")
                )
                iceberg_files.add(relative)
    return evidence, iceberg_files


def _call_name(site: object) -> str:
    """Terminal op name; chained calls arrive inside-out in ``site.dotted``."""
    name: str = getattr(site, "name", "")
    if name.isidentifier():
        return name
    dotted: str = getattr(site, "dotted", "")
    return dotted.split("(", 1)[0].rsplit(".", 1)[-1].lower()


def _py_table_ops(
    index: object, catalog_names: set[str], iceberg_files: set[Path]
) -> list[IcebergEvidence]:
    """Pass B: table references resolvable to a configured catalog."""
    from forge_doctor.analyzers.index import ProjectIndex

    assert isinstance(index, ProjectIndex)
    evidence: list[IcebergEvidence] = []
    for relative, module in sorted(index.modules.items(), key=lambda kv: kv[0].as_posix()):
        names = [_call_name(site) for site in module.calls]
        has_write = any(name in {"writeto", "insertinto", "saveastable", "save"} for name in names)
        for site, name in zip(module.calls, names, strict=True):
            if name in _LEGACY_WRITES or name in _V2_WRITES:
                target = site.args[0] if site.args else ""
                cataloged = bool(target) and _catalog_of(target, catalog_names)
                if cataloged or relative in iceberg_files:
                    api = "legacy" if name in _LEGACY_WRITES else "v2"
                    if cataloged and api == "legacy":
                        api = "legacy_catalog"
                    evidence.append(
                        IcebergEvidence(
                            "write_api", api, site.dotted, relative, site.line, "python"
                        )
                    )
            if name in {"repartition", "coalesce"} and has_write:
                evidence.append(
                    IcebergEvidence(
                        "write_pattern", name, site.dotted, relative, site.line, "python"
                    )
                )
            if name not in {"writeto", "table"} or not site.args:
                continue
            target = site.args[0]
            if not _catalog_of(target, catalog_names):
                continue
            evidence.append(IcebergEvidence("table", target, name, relative, site.line, "python"))
            op = "write" if name == "writeto" else "read"
            evidence.append(IcebergEvidence("operation", op, target, relative, site.line, "python"))
    return evidence


def _partition_cols(text: str) -> list[tuple[str, str]]:
    """Parse ``PARTITIONED BY (...)`` into ``(column, transform)`` pairs."""
    m = _PART_BY_RE.search(text)
    if not m:
        return []
    depth, i = 0, m.end() - 1
    for j in range(i, len(text)):
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                i = j
                break
    body = text[m.end() : i]
    items, depth, cur = [], 0, ""
    for ch in body + ",":
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            items.append(cur.strip())
            cur = ""
        else:
            cur += ch
    cols = []
    for item in items:
        inner = re.match(r"(\w+)\s*\((.*)\)\s*$", item, re.DOTALL)
        if inner and inner.group(1).lower() in _TRANSFORMS:
            # bucket(16, col) / truncate(N, col): column is the last arg.
            cols.append((inner.group(2).split(",")[-1].strip().lower(), inner.group(1).lower()))
        elif item:
            cols.append((item.lower(), "identity"))
    return cols


def _sql_evidence(sql_index: object, catalog_names: set[str]) -> list[IcebergEvidence]:
    from forge_doctor.analyzers.sql_ast import SqlIndex

    evidence: list[IcebergEvidence] = []
    assert isinstance(sql_index, SqlIndex)
    for stmt in sql_index.statements:
        text = stmt.text
        if stmt.kind == "create":
            using = _USING_RE.search(text)
            if using and using.group(1).lower() == "iceberg":
                evidence.append(
                    IcebergEvidence(
                        "format", "iceberg", "USING iceberg", stmt.file, stmt.line, "sql"
                    )
                )
                for table in stmt.tables_written:
                    evidence.append(
                        IcebergEvidence("table", table, "create", stmt.file, stmt.line, "sql")
                    )
            for match in _TBLPROPS_RE.finditer(text):
                for key, value in _PROP_PAIR_RE.findall(match.group(1)):
                    evidence.append(
                        IcebergEvidence("property", key.lower(), value, stmt.file, stmt.line, "sql")
                    )
            if re.search(r"\bPARTITIONED\s+BY\b", text, re.IGNORECASE):
                evidence.append(
                    IcebergEvidence("property", "partitioned-by", "", stmt.file, stmt.line, "sql")
                )
            for col, transform in _partition_cols(text):
                evidence.append(
                    IcebergEvidence(
                        "property",
                        f"partition.{col}",
                        transform,
                        stmt.file,
                        stmt.line,
                        "sql",
                    )
                )
        elif stmt.kind == "merge":
            target = stmt.tables_written[0] if stmt.tables_written else ""
            evidence.append(
                IcebergEvidence("operation", "merge", target, stmt.file, stmt.line, "sql")
            )
            evidence.append(
                IcebergEvidence(
                    "merge_detail", target, stmt.merge_source, stmt.file, stmt.line, "sql"
                )
            )
            for col in stmt.merge_on_cols:
                evidence.append(
                    IcebergEvidence("merge_on", target, col, stmt.file, stmt.line, "sql")
                )
            if target and _catalog_of(target, catalog_names):
                evidence.append(
                    IcebergEvidence("table", target, "merge", stmt.file, stmt.line, "sql")
                )
        elif stmt.kind == "insert":
            op = "overwrite" if _OVERWRITE_RE.search(text) else "insert"
            target = stmt.tables_written[0] if stmt.tables_written else ""
            evidence.append(IcebergEvidence("operation", op, target, stmt.file, stmt.line, "sql"))
        elif stmt.kind in {"delete", "update"}:
            target = stmt.tables_written[0] if stmt.tables_written else ""
            evidence.append(
                IcebergEvidence("operation", stmt.kind, target, stmt.file, stmt.line, "sql")
            )
        elif stmt.kind == "alter":
            for match in _TBLPROPS_RE.finditer(text):
                for key, value in _PROP_PAIR_RE.findall(match.group(1)):
                    evidence.append(
                        IcebergEvidence("property", key.lower(), value, stmt.file, stmt.line, "sql")
                    )
        for call in _CALL_RE.finditer(text):
            catalog, proc = call.group(1), call.group(2).lower()
            evidence.append(
                IcebergEvidence("maintenance", proc, catalog, stmt.file, stmt.line, "sql")
            )
        for table in (*stmt.tables_read, *stmt.tables_written):
            head = _catalog_of(table, catalog_names)
            if head:
                evidence.append(IcebergEvidence("table", table, head, stmt.file, stmt.line, "sql"))
    return evidence


def _config_evidence(ctx: ProjectContext) -> list[IcebergEvidence]:
    evidence: list[IcebergEvidence] = []
    for relative in sorted(ctx.files):
        if relative.name != "spark-defaults.conf" and relative.suffix.lower() != ".properties":
            continue
        text = ctx.read_text(relative)
        if text is None:
            continue
        for line_no, raw in enumerate(text.splitlines(), 1):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            # spark-defaults.conf style: `key value` or `key=value`
            if "=" in stripped:
                key, _, value = stripped.partition("=")
            else:
                key, _, value = stripped.partition(" ")
            key = key.strip()
            if key.startswith("spark.sql.catalog."):
                name = key[len("spark.sql.catalog.") :].split(".")[0]
                evidence.append(
                    IcebergEvidence("catalog", name, value.strip(), relative, line_no, "config")
                )
            elif key.startswith(_CONF_SET_RE):
                evidence.append(
                    IcebergEvidence("config", key, value.strip(), relative, line_no, "config")
                )
    return evidence


def _iac_evidence(ctx: ProjectContext) -> list[IcebergEvidence]:
    from forge_doctor.analyzers.hcl_lite import project_iac

    evidence: list[IcebergEvidence] = []
    for resource in project_iac(ctx.files, ctx.root):
        file = Path(resource.file)
        if resource.type in {"aws_glue_job", "AWS::Glue::Job"}:
            version = str(resource.attrs.get("glue_version", resource.attrs.get("GlueVersion", "")))
            evidence.append(
                IcebergEvidence(
                    "runtime", "glue", version or "unset", file, resource.line, resource.source
                )
            )
        if resource.type in {
            "aws_s3tables_table_bucket",
            "aws_glue_catalog_database",
            "AWS::Glue::Database",
        }:
            evidence.append(
                IcebergEvidence(
                    "catalog", resource.name, resource.type, file, resource.line, resource.source
                )
            )
    return evidence


def _operation_evidence(index: object, iceberg_files: set[Path]) -> list[IcebergEvidence]:
    """writeTo chain ops inside files carrying a direct iceberg marker."""
    from forge_doctor.analyzers.index import ProjectIndex

    assert isinstance(index, ProjectIndex)
    evidence: list[IcebergEvidence] = []
    for relative, module in sorted(index.modules.items(), key=lambda kv: kv[0].as_posix()):
        if relative not in iceberg_files:
            continue
        for site in module.calls:
            name = _call_name(site)
            if name in _WRITE_OPS:
                evidence.append(
                    IcebergEvidence("operation", name, "", relative, site.line, "python")
                )
    return evidence


def iceberg_model(ctx: ProjectContext) -> IcebergProjectModel:
    """Build (once, memoized on ctx) the project's Iceberg evidence model."""
    cached = getattr(ctx, _CACHE_ATTR, None)
    if cached is not None:
        return cast(IcebergProjectModel, cached)

    from forge_doctor.analyzers.index import project_index
    from forge_doctor.analyzers.sql_ast import analyze_sql

    index = project_index(ctx)
    evidence: list[IcebergEvidence] = []

    # Pass A: catalogs + configs + runtimes so table attribution can resolve.
    evidence.extend(_config_evidence(ctx))
    evidence.extend(_iac_evidence(ctx))
    py_ev, iceberg_files = _py_static_evidence(index)
    evidence.extend(py_ev)
    catalog_names = {e.name for e in evidence if e.kind == "catalog"}

    # Pass B: table/operation attribution now that catalog names are known.
    evidence.extend(_py_table_ops(index, catalog_names, iceberg_files))
    evidence.extend(_sql_evidence(analyze_sql(ctx), catalog_names))
    evidence.extend(_operation_evidence(index, iceberg_files))

    # Catalog impl classification across every source (py conf.set, conf
    # files, IaC) so checks/CLI can ask "which engine backs this catalog".
    for e in [x for x in evidence if x.kind == "catalog"]:
        impl = e.value.lower()
        for marker in _CATALOG_IMPLS:
            if marker in impl:
                evidence.append(
                    IcebergEvidence("catalog_type", e.name, marker, e.file, e.line, e.source)
                )
                break

    evidence.sort(key=lambda e: (e.file.as_posix(), e.line, e.kind, e.name, e.value))
    model = IcebergProjectModel(evidence=evidence)
    setattr(ctx, _CACHE_ATTR, model)
    return model
