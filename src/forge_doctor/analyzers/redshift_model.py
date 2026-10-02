"""Redshift vendor model — populates warehouse evidence for `redshift`
(spec 215).

Evidence planes:

- **Terraform** ``aws_redshift*`` resources — provisioned clusters and
  Serverless workgroups/namespaces, subnet/parameter groups, snapshot
  schedules, datashares. ``publicly_accessible``/``encrypted``/
  ``database_name`` ride along as attrs; nested ``parameter`` blocks are
  mined from the resource body (WLM config, ``auto_analyze``).
- **SQL DDL** — Redshift-only statement shapes from the shared sqlglot
  index: ``CREATE TABLE ... DISTSTYLE|DISTKEY|SORTKEY|ENCODE``,
  ``CREATE EXTERNAL SCHEMA|TABLE`` (Spectrum), ``CREATE MATERIALIZED
  VIEW``, ``CREATE DATASHARE``, ``UNLOAD TO``, ``COPY ... IAM_ROLE``.
- **Observed metadata exports** — ``SVV_*``/``STL_*``/``STV_*``-style
  JSON/CSV rows under ``redshift/``, ``.forge-doctor/evidence/`` (claimed
  only with a positive Redshift field signal), or ``svv_*``/``stl_*``
  filenames. Unknown shapes land in ``unparsed`` — never dropped.

Attribution: a ``.sql`` file counts as Redshift only when it carries a
vendor-exclusive marker (``DISTSTYLE``/``DISTKEY``/``SORTKEY``/
``INTERLEAVED``, ``ENCODE``, ``SVV_/STL_/STV_/SVL_`` refs, ``CREATE
EXTERNAL SCHEMA|TABLE``, ``DATASHARE``, ``UNLOAD TO``, ``IAM_ROLE``).
``VACUUM``/``ANALYZE`` alone are NOT markers — Postgres shares them, and
the adversarial lab pins Postgres-clean behavior.
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
    from forge_doctor.core.context import ProjectContext

_CACHE_ATTR = "_forge_doctor_redshift_model"

PLATFORM = "redshift"

# ---------------------------------------------------------------------------
# Model rows


@dataclass(frozen=True)
class RedshiftObject:
    """A vendor object: cluster|workgroup|database|schema|table|view|..."""

    kind: str
    name: str
    file: Path | None = None
    line: int | None = None
    source: str = "sql"  # sql | terraform | observed
    attrs: tuple[tuple[str, str], ...] = ()

    def attr(self, key: str, default: str = "") -> str:
        return dict(self.attrs).get(key, default)


@dataclass(frozen=True)
class RedshiftQuery:
    """An authored maintenance/DML statement in a Redshift-attributed file."""

    kind: str  # vacuum|analyze|copy|unload|select|insert|merge|other
    file: Path
    line: int | None
    text: str
    tables_read: tuple[str, ...] = ()
    tables_written: tuple[str, ...] = ()
    source: str = "file"  # file | job (observed STL_QUERY)


@dataclass(frozen=True)
class ObservedRow:
    """One row from an exported metadata artifact (SVV_*/STL_*)."""

    shape: str  # tables|queries|wlm|columns|other
    file: Path
    fields: tuple[tuple[str, str], ...]

    def get(self, *keys: str) -> str:
        d = {k.lower(): v for k, v in self.fields}
        for key in keys:
            if key.lower() in d:
                return d[key.lower()]
        return ""


@dataclass
class RedshiftProjectModel:
    """All Redshift evidence for a project."""

    compute: list[RedshiftObject] = field(default_factory=list)  # cluster/workgroup
    namespaces: list[RedshiftObject] = field(default_factory=list)  # database/schema
    tables: list[RedshiftObject] = field(default_factory=list)
    views: list[RedshiftObject] = field(default_factory=list)  # + materialized
    objects: list[RedshiftObject] = field(default_factory=list)  # datashare/...
    queries: list[RedshiftQuery] = field(default_factory=list)
    observed: list[ObservedRow] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)
    sql_files: set[Path] = field(default_factory=set)  # redshift-attributed

    @property
    def has_evidence(self) -> bool:
        return bool(
            self.compute
            or self.namespaces
            or self.tables
            or self.views
            or self.objects
            or self.queries
            or self.observed
        )

    def by_kind(self, kind: str) -> list[RedshiftObject]:
        return [o for o in self.objects if o.kind == kind]

    def observed_tables(self) -> list[ObservedRow]:
        return [r for r in self.observed if r.shape == "tables"]

    def unsorted_tables(self) -> set[str]:
        """Declared tables without a SORTKEY."""
        return {t.name for t in self.tables if not t.attr("sortkey") and not t.attr("sort_keys")}

    def ato_available(self) -> bool:
        """Auto table optimization is eligible on ra3+/dc2+/serverless compute."""
        for c in self.compute:
            node = c.attr("node_type").lower()
            if c.kind == "workgroup" or re.match(r"(ra3|dc2|ds2)\.", node):
                return True
        return False


# ---------------------------------------------------------------------------
# SQL evidence — vendor markers + statement classification.

_VENDOR_MARKERS = re.compile(
    r"(diststyle|distkey|sortkey|interleaved|encode\s+\w+"
    r"|\bsvv_\w+|\bstv_\w+|\bstl_\w+|\bsvl_\w+"
    r"|create\s+(or\s+replace\s+)?external\s+(schema|table)\b"
    r"|\bdatashare\b|unload\s+to\s+'|iam_role|spectrum)",
    re.IGNORECASE,
)

_CREATE_RE = re.compile(
    r"create\s+(?:or\s+replace\s+)?"
    r"(?:(?:temp|temporary|local|global)\s+)*"
    r"(materialized\s+view|external\s+schema|external\s+table|schema|table|"
    r"view|database|datashare|procedure|function)"
    r"\s+(?:if\s+not\s+exists\s+)?([\w.$\"`-]+)",
    re.IGNORECASE,
)
_KV_RE = re.compile(r"(\w+)\s*=\s*'([^']*)'|(\w+)\s*=\s*(\w[\w.]*)")
_DISTSTYLE_RE = re.compile(r"diststyle\s+(even|all|key|auto)", re.IGNORECASE)
_DISTKEY_RE = re.compile(r"distkey\s*\(\s*(\w+)\s*\)", re.IGNORECASE)
_SORTKEY_RE = re.compile(r"(compound|interleaved)?\s*sortkey\s*\(([^)]*)\)", re.IGNORECASE)
_ENCODE_RE = re.compile(r"\bencode\s+(\w+)", re.IGNORECASE)
_MAINT_RE = re.compile(r"^\s*(vacuum|analyze|copy|unload)\b", re.IGNORECASE)


def _ddl_attrs(text: str) -> tuple[tuple[str, str], ...]:
    """Redshift layout attrs: diststyle/distkey/sortkey/encode."""
    out: dict[str, str] = {}
    for m in _KV_RE.finditer(text):
        key = m.group(1) or m.group(3)
        val = m.group(2) if m.group(2) is not None else m.group(4)
        if key:
            out[key.lower()] = val or ""
    ds = _DISTSTYLE_RE.search(text)
    if ds:
        out["diststyle"] = ds.group(1).lower()
    dk = _DISTKEY_RE.search(text)
    if dk:
        out["distkey"] = dk.group(1)
    sk = _SORTKEY_RE.search(text)
    if sk:
        style = (sk.group(1) or "compound").lower()
        out["sortkey"] = sk.group(2).strip()
        out["sortkey_style"] = style
    enc = _ENCODE_RE.search(text)
    if enc:
        out["encode"] = enc.group(1).lower()
    return tuple(sorted(out.items()))


_OBJECT_KIND = {
    "database": "database",
    "schema": "schema",
    "external schema": "external_schema",
    "table": "table",
    "external table": "external_table",
    "view": "view",
    "materialized view": "materialized_view",
    "datashare": "datashare",
    "procedure": "routine",
    "function": "routine",
}


def _place(model: RedshiftProjectModel, obj: RedshiftObject) -> None:
    if obj.kind in {"cluster", "workgroup"}:
        model.compute.append(obj)
    elif obj.kind in {"database", "schema", "external_schema"}:
        model.namespaces.append(obj)
    elif obj.kind in {"table", "external_table"}:
        model.tables.append(obj)
    elif obj.kind in {"view", "materialized_view"}:
        model.views.append(obj)
    else:
        model.objects.append(obj)


def _from_sql(model: RedshiftProjectModel, ctx: ProjectContext) -> None:
    from forge_doctor.analyzers.sql_ast import SQLGLOT_AVAILABLE, analyze_sql

    if not SQLGLOT_AVAILABLE:
        return
    index = analyze_sql(ctx)
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
            name = m.group(2).strip('"`')
            attrs = _ddl_attrs(text)
            reads = stmt.tables_read
            if reads:
                attrs += (("tables_read", ",".join(reads)),)
            _place(
                model,
                RedshiftObject(
                    _OBJECT_KIND.get(kind, kind), name, stmt.file, stmt.line, "sql", attrs
                ),
            )
        else:
            maint = _MAINT_RE.match(text)
            if maint:
                kind = maint.group(1).lower()
                target = ""
                if kind == "copy":
                    tm = re.search(r"copy\s+([\w.\"`]+)", text, re.IGNORECASE)
                    target = tm.group(1).strip('"`') if tm else ""
                model.queries.append(
                    RedshiftQuery(
                        kind,
                        stmt.file,
                        stmt.line,
                        text,
                        stmt.tables_read,
                        stmt.tables_written or ((target,) if target else ()),
                        "file",
                    )
                )
            elif stmt.kind in {"select", "merge", "insert", "update", "delete"}:
                model.queries.append(
                    RedshiftQuery(
                        stmt.kind,
                        stmt.file,
                        stmt.line,
                        text,
                        stmt.tables_read,
                        stmt.tables_written,
                        "file",
                    )
                )


# ---------------------------------------------------------------------------
# Terraform evidence — aws_redshift* resources.

_TF_REDSHIFT: dict[str, str] = {
    "aws_redshift_cluster": "cluster",
    "aws_redshiftserverless_workgroup": "workgroup",
    "aws_redshiftserverless_namespace": "database",
    "aws_redshift_subnet_group": "subnet_group",
    "aws_redshift_cluster_subnet_group": "subnet_group",
    "aws_redshift_parameter_group": "parameter_group",
    "aws_redshift_snapshot_schedule": "snapshot_schedule",
    "aws_redshift_snapshot_schedule_association": "snapshot_association",
    "aws_redshift_scheduled_action": "scheduled_action",
    "aws_redshift_data_share_authorization": "datashare_authz",
    "aws_redshift_data_share_consumer_association": "datashare_consumer",
    "aws_redshift_endpoint_access": "endpoint_access",
    "aws_redshift_usage_limit": "usage_limit",
    "aws_redshift_authentication_profile": "auth_profile",
    "aws_redshift_hsm_client_certificate": "hsm_certificate",
    "aws_redshift_logging": "logging",
    "aws_redshift_cluster_iam_roles": "iam_roles",
}


def _tf_attrs(res: Any) -> tuple[tuple[str, str], ...]:
    out: dict[str, str] = {
        k.lower(): str(v)
        for k, v in res.attrs.items()
        if isinstance(v, (str, int, float, bool)) and not k.startswith("_")
    }
    body = getattr(res, "body", "") or ""
    # Nested parameter{} blocks carry WLM/auto_analyze config.
    for pm in re.finditer(
        r"parameter\s*\{[^}]*name\s*=\s*\"([^\"]+)\"[^}]*value\s*=\s*\"([^\"]*)\"",
        body,
        re.DOTALL,
    ):
        out[f"parameter.{pm.group(1).lower()}"] = pm.group(2)
    if re.search(r"\bparameter\s*\{[^}]*wlm", body, re.DOTALL | re.IGNORECASE):
        out["wlm_config"] = "parameterized"
    if "automatic_table_optimization" in body:
        m = re.search(r"automatic_table_optimization\s*=\s*\"?(\w+)\"?", body)
        if m:
            out["automatic_table_optimization"] = m.group(1).lower()
    return tuple(sorted(out.items()))


def _from_terraform(model: RedshiftProjectModel, ctx: ProjectContext) -> None:
    from forge_doctor.analyzers.terraform_model import terraform_model

    for res in terraform_model(ctx).resources:
        if not res.labels:
            continue
        kind = _TF_REDSHIFT.get(res.labels[0])
        if kind is None:
            continue
        attr_name = (
            res.attrs.get("cluster_identifier")
            or res.attrs.get("workgroup_name")
            or res.attrs.get("namespace_name")
            or res.attrs.get("name")
            or res.attrs.get("data_share_identifier")
        )
        name = (
            str(attr_name)
            if isinstance(attr_name, str) and attr_name
            else (res.labels[1] if len(res.labels) > 1 else res.address)
        )
        _place(
            model,
            RedshiftObject(kind, name, res.file, res.line, "terraform", _tf_attrs(res)),
        )
        # The cluster carries its database inline (database_name attr).
        if kind == "cluster":
            db = res.attrs.get("database_name")
            if isinstance(db, str) and db:
                model.namespaces.append(
                    RedshiftObject("database", db, res.file, res.line, "terraform", ())
                )


# ---------------------------------------------------------------------------
# Observed metadata exports — tolerant loader. Claim rules: a file under
# redshift/ is always Redshift; under .forge-doctor/evidence/ it needs a
# Redshift-exclusive field signal so generic rows stay unclaimed.

_EXPORT_DIR_RE = re.compile(r"(^|/)(redshift|\.forge-doctor/evidence)(/|$)", re.IGNORECASE)
_EXPORT_NAMES = re.compile(r"\b(svv_|stl_|stv_|svl_|redshift_)", re.IGNORECASE)
_RS_FIELD_SIGNAL = re.compile(
    r"skew_rows|diststyle|sortkey|tbl_rows|unsorted|pct_used|"
    r"service_class|queue_time|total_exec_time|querytxt|wlm_|"
    r"workload_name|elapsed_time|label|datashare",
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


def _shape_of(fields: dict[str, str], path: Path) -> str:
    keys = {k.lower() for k in fields}
    stem = path.stem.lower()
    if keys & {"skew_rows", "diststyle", "sortkey1", "tbl_rows", "unsorted", "size"}:
        return "tables"
    if keys & {"service_class", "queue_time", "wlm_queue"} or "wlm" in stem:
        return "wlm"
    if keys & {"querytxt", "query_text", "total_exec_time", "label"} or stem.startswith(
        ("stl_query", "svl_query")
    ):
        return "queries"
    if "column" in keys or "column_name" in keys:
        return "columns"
    if keys & {"table", "tablename", "table_name"}:
        return "tables"
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
        for key in ("rows", "data", "results", "tables", "queries"):
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


def _claimed_by_redshift(rel: Path, rows: list[dict[str, str]]) -> bool:
    """Positive vendor signal required for shared evidence dirs."""
    posix = rel.as_posix()
    if re.search(r"(^|/)redshift(/|$)", posix, re.IGNORECASE):
        return True
    if _EXPORT_NAMES.search(rel.name):
        return True
    return any(any(_RS_FIELD_SIGNAL.search(k) for k in row) for row in rows[:5])


def _from_exports(model: RedshiftProjectModel, ctx: ProjectContext) -> None:
    for rel in _candidate_exports(ctx):
        text = ctx.read_text(rel)
        if text is None:
            continue
        rows, recognized = _rows_from(rel, text)
        if not recognized or not rows:
            model.unparsed.append(rel.as_posix())
            continue
        if not _claimed_by_redshift(rel, rows):
            continue  # e.g. a snowflake/bigquery export in a shared dir
        for row in rows:
            model.observed.append(ObservedRow(_shape_of(row, rel), rel, tuple(sorted(row.items()))))


def redshift_model(ctx: ProjectContext) -> RedshiftProjectModel:
    """Memoized Redshift model over ctx evidence."""
    cached = getattr(ctx, _CACHE_ATTR, None)
    if cached is not None:
        return cast(RedshiftProjectModel, cached)
    model = RedshiftProjectModel()
    _from_terraform(model, ctx)
    _from_sql(model, ctx)
    _from_exports(model, ctx)
    # Observed STL_QUERY rows double as query evidence (RS001/RS002).
    for row in model.observed:
        if row.shape != "queries":
            continue
        text = row.get("querytxt", "query_text", "query", "statement")
        if text:
            model.queries.append(RedshiftQuery("select", row.file, None, text, (), (), "job"))
    setattr(ctx, _CACHE_ATTR, model)
    return model
