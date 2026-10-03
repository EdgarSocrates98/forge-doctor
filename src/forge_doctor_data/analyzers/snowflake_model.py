"""Snowflake vendor model — populates warehouse evidence for `snowflake`
(spec 213).

Evidence planes:

- **Terraform** ``snowflake_*`` resources — compute/namespace/table/view
  facts with vendor attrs (``warehouse_size``, ``auto_suspend``,
  ``auto_resume``, ``database``, ``schema``).
- **SQL DDL** — Snowflake-only statement shapes from the shared sqlglot
  index: ``CREATE WAREHOUSE|DATABASE|SCHEMA|TABLE|STAGE|PIPE|STREAM|
  TASK`` (parsed as ``command``), ``CREATE VIEW/MATERIALIZED VIEW``
  (``create``), ``COPY INTO ... @stage`` (``copy``).
- **Observed metadata exports** — ``SHOW``/``INFORMATION_SCHEMA``-style
  JSON/CSV rows under ``snowflake/``, ``.forge-doctor-data/evidence/``, or
  ``information_schema*`` filenames. Tolerant loader: recognized shapes
  become rows; unknown shapes land in ``unparsed`` — never dropped
  silently.

Attribution: a ``.sql`` file counts as Snowflake only when it contains
a vendor-exclusive marker (``CREATE WAREHOUSE|STAGE|PIPE|STREAM|TASK``,
``AUTO_SUSPEND``, ``WAREHOUSE_SIZE``, ``COPY INTO ... @stage``,
``CLUSTER BY``). Non-Snowflake SQL must not trip SNOW checks (the lab
adversarial case pins this).
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

_CACHE_ATTR = "_forge_doctor_data_snowflake_model"

PLATFORM = "snowflake"

# ---------------------------------------------------------------------------
# Model rows


@dataclass(frozen=True)
class SnowflakeWarehouse:
    """A declared virtual warehouse (DDL or Terraform)."""

    name: str
    file: Path | None = None
    line: int | None = None
    source: str = "sql"  # sql | terraform
    attrs: tuple[tuple[str, str], ...] = ()

    def attr(self, key: str, default: str = "") -> str:
        return dict(self.attrs).get(key, default)


@dataclass(frozen=True)
class SnowflakeObject:
    """A vendor object: stage|pipe|stream|task|role|grant|database|..."""

    kind: str
    name: str
    file: Path | None = None
    line: int | None = None
    source: str = "sql"
    attrs: tuple[tuple[str, str], ...] = ()

    def attr(self, key: str, default: str = "") -> str:
        return dict(self.attrs).get(key, default)


@dataclass(frozen=True)
class CopyInto:
    """A ``COPY INTO <target> FROM @stage`` data-movement statement."""

    target: str
    stage_ref: str  # "@stage", "@stage/path", "@%", "@~" or a raw URI
    file: Path | None = None
    line: int | None = None


@dataclass(frozen=True)
class ObservedRow:
    """One row from an exported metadata artifact (SHOW/INFORMATION_SCHEMA)."""

    shape: str  # warehouses|tables|query_history|columns|other
    file: Path
    fields: tuple[tuple[str, str], ...]

    def get(self, *keys: str) -> str:
        d = {k.lower(): v for k, v in self.fields}
        for key in keys:
            if key.lower() in d:
                return d[key.lower()]
        return ""


@dataclass
class SnowflakeProjectModel:
    """All Snowflake evidence for a project."""

    warehouses: list[SnowflakeWarehouse] = field(default_factory=list)
    objects: list[SnowflakeObject] = field(default_factory=list)  # stage/pipe/...
    tables: list[SnowflakeObject] = field(default_factory=list)
    views: list[SnowflakeObject] = field(default_factory=list)
    namespaces: list[SnowflakeObject] = field(default_factory=list)  # db/schema
    copies: list[CopyInto] = field(default_factory=list)
    observed: list[ObservedRow] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)
    sql_files: set[Path] = field(default_factory=set)  # snowflake-attributed

    @property
    def has_evidence(self) -> bool:
        return bool(
            self.warehouses
            or self.objects
            or self.tables
            or self.views
            or self.namespaces
            or self.copies
            or self.observed
        )

    def by_kind(self, kind: str) -> list[SnowflakeObject]:
        return [o for o in self.objects if o.kind == kind]

    def observed_tables(self) -> list[ObservedRow]:
        return [r for r in self.observed if r.shape == "tables"]


# ---------------------------------------------------------------------------
# SQL evidence — vendor markers + statement classification.

_VENDOR_MARKERS = re.compile(
    r"\b(create\s+(or\s+replace\s+)?(warehouse|stage|pipe|stream|task)\b"
    r"|auto_suspend|auto_resume|warehouse_size|cluster\s+by|snowflake\.)",
    re.IGNORECASE,
)
_STAGE_REF = re.compile(r"@[\w.%~/]+")

_CREATE_RE = re.compile(
    r"create\s+(?:or\s+replace\s+)?"
    r"(?:(?:transient|temporary|temp|local|global|secure|recursive)\s+)*"
    r"(materialized\s+view|external\s+table|warehouse|database|schema|table|"
    r"stage|pipe|stream|task|view|role)"
    r"\s+(?:if\s+not\s+exists\s+)?([\w.$]+)",
    re.IGNORECASE,
)
_KV_RE = re.compile(r"(\w+)\s*=\s*'([^']*)'|(\w+)\s*=\s*(\w[\w.]*)")


def _ddl_attrs(text: str) -> tuple[tuple[str, str], ...]:
    """Property assignments inside a DDL statement (``KEY = value``)."""
    out: dict[str, str] = {}
    for m in _KV_RE.finditer(text):
        key = m.group(1) or m.group(3)
        val = m.group(2) if m.group(2) is not None else m.group(4)
        if key:
            out[key.lower()] = val or ""
    return tuple(sorted(out.items()))


def _from_sql(model: SnowflakeProjectModel, ctx: ProjectContext) -> None:
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
        head = " ".join(text.split()[:8]).lower()
        if stmt.kind == "command":
            m = _CREATE_RE.search(text)
            if m:
                kind = " ".join(m.group(1).lower().split())
                name = m.group(2)
                attrs = _ddl_attrs(text)
                # `ON TABLE t` (streams) and `COPY INTO t` (pipes) are not
                # key=value pairs - extract them explicitly for edges.
                on_table = re.search(r"on\s+table\s+([\w.$]+)", text, re.IGNORECASE)
                if on_table:
                    attrs += (("on_table", on_table.group(1)),)
                obj = SnowflakeObject(
                    _OBJECT_KIND.get(kind, kind),
                    name,
                    stmt.file,
                    stmt.line,
                    "sql",
                    attrs,
                )
                _place(model, obj)
            # Embedded COPY INTO (e.g. inside CREATE PIPE ... AS COPY INTO)
            cm = re.search(
                r"copy\s+into\s+([\w.$]+)\s+from\s+(@[\w.%~/]+|'[^']*'|\w+)",
                text,
                re.IGNORECASE,
            )
            if cm:
                model.copies.append(CopyInto(cm.group(1), cm.group(2), stmt.file, stmt.line))
        elif stmt.kind == "copy":
            stage = _STAGE_REF.search(text)
            target = stmt.tables_written[0] if stmt.tables_written else ""
            model.copies.append(
                CopyInto(target, stage.group(0) if stage else "", stmt.file, stmt.line)
            )
        elif stmt.kind == "create":
            # sqlglot parses VIEW/TABLE/DATABASE/SCHEMA DDL structurally;
            # classify by the head tokens (CREATE is always first).
            tokens = head.split()
            target = stmt.tables_written[0] if stmt.tables_written else ""
            if "view" in tokens:
                model.views.append(
                    SnowflakeObject(
                        "materialized_view" if "materialized" in tokens else "view",
                        target,
                        stmt.file,
                        stmt.line,
                        "sql",
                        (
                            *_ddl_attrs(text),
                            ("tables_read", ",".join(stmt.tables_read)),
                        ),
                    )
                )
            elif "table" in tokens:
                model.tables.append(
                    SnowflakeObject(
                        "external_table" if "external" in tokens else "table",
                        target,
                        stmt.file,
                        stmt.line,
                        "sql",
                        _ddl_attrs(text),
                    )
                )
            elif "database" in tokens or "schema" in tokens:
                model.namespaces.append(
                    SnowflakeObject(
                        "database" if "database" in tokens else "schema",
                        target,
                        stmt.file,
                        stmt.line,
                        "sql",
                        _ddl_attrs(text),
                    )
                )


_OBJECT_KIND = {
    "warehouse": "warehouse",
    "database": "database",
    "schema": "schema",
    "table": "table",
    "external table": "external_table",
    "stage": "stage",
    "pipe": "pipe",
    "stream": "stream",
    "task": "task",
    "view": "view",
    "materialized view": "materialized_view",
    "role": "role",
}


def _place(model: SnowflakeProjectModel, obj: SnowflakeObject) -> None:
    if obj.kind == "warehouse":
        model.warehouses.append(
            SnowflakeWarehouse(obj.name, obj.file, obj.line, obj.source, obj.attrs)
        )
    elif obj.kind in {"database", "schema"}:
        model.namespaces.append(obj)
    elif obj.kind in {"table", "external_table"}:
        model.tables.append(obj)
    elif obj.kind in {"view", "materialized_view"}:
        model.views.append(obj)
    else:
        model.objects.append(obj)


# ---------------------------------------------------------------------------
# Terraform evidence — snowflake_* resources with vendor attrs kept.

_TF_SNOWFLAKE: dict[str, str] = {
    "snowflake_warehouse": "warehouse",
    "snowflake_database": "database",
    "snowflake_schema": "schema",
    "snowflake_table": "table",
    "snowflake_external_table": "external_table",
    "snowflake_view": "view",
    "snowflake_materialized_view": "materialized_view",
    "snowflake_stage": "stage",
    "snowflake_pipe": "pipe",
    "snowflake_stream": "stream",
    "snowflake_task": "task",
    "snowflake_role": "role",
    "snowflake_grant_privileges_to_role": "grant",
    "snowflake_grant_account_role": "grant",
}


def _tf_attrs(attrs: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            (k.lower(), str(v))
            for k, v in attrs.items()
            if isinstance(v, (str, int, float, bool)) and not k.startswith("_")
        )
    )


def _from_terraform(model: SnowflakeProjectModel, ctx: ProjectContext) -> None:
    from forge_doctor_data.analyzers.terraform_model import terraform_model

    for res in terraform_model(ctx).resources:
        if not res.labels:
            continue
        kind = _TF_SNOWFLAKE.get(res.labels[0])
        if kind is None:
            continue
        attr_name = res.attrs.get("name")
        name = (
            str(attr_name)
            if isinstance(attr_name, str) and attr_name
            else (res.labels[1] if len(res.labels) > 1 else res.address)
        )
        _place(
            model,
            SnowflakeObject(kind, name, res.file, res.line, "terraform", _tf_attrs(res.attrs)),
        )


# ---------------------------------------------------------------------------
# Observed metadata exports — tolerant loader.

_EXPORT_DIR_RE = re.compile(r"(^|/)(snowflake|\.forge-doctor-data/evidence)(/|$)", re.IGNORECASE)
_EXPORT_NAMES = re.compile(r"information_schema|show_|query_history", re.IGNORECASE)


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
    if keys & {"query_id", "queryid"} or "query_text" in keys or "execution_time" in keys:
        return "query_history"
    if {"table_name", "table_schema"} <= keys or (
        "table_name" in keys and (keys & {"row_count", "rowcount", "bytes", "clustering_key"})
    ):
        return "tables"
    if keys & {"warehouse_name", "warehouses", "size"} and "type" not in keys:
        return "warehouses"
    if "column_name" in keys:
        return "columns"
    if keys & {"name", "kind", "database_name"}:
        return "objects"
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
        for key in ("rows", "data", "results", "warehouses", "tables"):
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


def _from_exports(model: SnowflakeProjectModel, ctx: ProjectContext) -> None:
    for rel in _candidate_exports(ctx):
        text = ctx.read_text(rel)
        if text is None:
            continue
        rows, recognized = _rows_from(rel, text)
        if not recognized or not rows:
            model.unparsed.append(rel.as_posix())
            continue
        for row in rows:
            model.observed.append(ObservedRow(_shape_of(row), rel, tuple(sorted(row.items()))))


def snowflake_model(ctx: ProjectContext) -> SnowflakeProjectModel:
    """Memoized Snowflake model over ctx evidence."""
    cached = getattr(ctx, _CACHE_ATTR, None)
    if cached is not None:
        return cast(SnowflakeProjectModel, cached)
    model = SnowflakeProjectModel()
    _from_terraform(model, ctx)
    _from_sql(model, ctx)
    _from_exports(model, ctx)
    setattr(ctx, _CACHE_ATTR, model)
    return model
