"""Data contract model — producer schema promises (spec 217).

Evidence planes:

- **Contract files** — `datacontract.yml|yaml`, `*.datacontract.yml`,
  `*.odcs.{yml,yaml,json}`, or any YAML/JSON doc carrying a
  ``dataContractSpecification`` key or ``kind: DataContract``. Both
  datacontract-cli and ODCS shapes normalize to a minimal subset:
  id, owner, servers, schema objects + field types, SLA properties,
  quality terms. Top-level keys outside the subset land in
  ``unsupported`` — parsed-but-unchecked (honest coverage, never
  silently dropped).
- **Detected table schemas** (cross-domain, for DCTR003) — ``CREATE
  TABLE`` column defs re-parsed from the shared sql index, Terraform
  ``google_bigquery_table`` ``schema`` JSON, and observed column
  exports (Snowflake/BigQuery ``columns``-shaped rows). Keyed by
  relation name; tail-name matching links them to contract objects.

Constraints: contract files are declarative evidence only — nothing is
validated against a live schema registry, and no contract CLI runs.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from forge_doctor_data.core.context import ProjectContext

_CACHE_ATTR = "_forge_doctor_data_datacontract_model"

# ---------------------------------------------------------------------------
# Rows


@dataclass(frozen=True)
class ContractField:
    """One declared schema field."""

    name: str
    type: str  # normalized family (see _family); params kept in raw_type
    raw_type: str = ""
    required: bool = False


@dataclass(frozen=True)
class ContractObject:
    """One governed relation inside a contract's ``schema`` section."""

    name: str
    fields: tuple[ContractField, ...] = ()


@dataclass(frozen=True)
class ContractServer:
    """A server block; ``environment`` captures prod claims."""

    name: str
    type: str = ""
    environment: str = ""


@dataclass
class DataContract:
    """One contract file's normalized subset."""

    id: str
    file: Path
    format: str  # datacontract | odcs | unknown
    owner: str = ""
    objects: list[ContractObject] = field(default_factory=list)
    sla: dict[str, str] = field(default_factory=dict)  # property -> value text
    quality: list[str] = field(default_factory=list)
    servers: list[ContractServer] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)

    @property
    def schema_fields(self) -> int:
        return sum(len(o.fields) for o in self.objects)

    def claims_production(self) -> bool:
        """A server named/env-tagged production is the spec's 'prod usage'."""
        for s in self.servers:
            probe = f"{s.name} {s.environment}".lower()
            if re.search(r"\bprod(?:uction)?\b", probe):
                return True
        return False


@dataclass
class DetectedSchema:
    """A real table schema detected by another adapter (DCTR003 input)."""

    object_name: str
    fields: dict[str, str]  # field name -> normalized family
    evidence: str  # "static" | "config" | "observed_metadata"


@dataclass
class DataContractModel:
    """All contract evidence plus detected schemas for cross-checks."""

    contracts: list[DataContract] = field(default_factory=list)
    detected: dict[str, DetectedSchema] = field(default_factory=dict)
    unparsed: list[str] = field(default_factory=list)

    @property
    def has_evidence(self) -> bool:
        return bool(self.contracts)


# ---------------------------------------------------------------------------
# Type normalization — coarse families so dialect spellings don't drift.


_FAMILIES = (
    (r"decimal|numeric|number", "decimal"),
    (r"bigint", "bigint"),
    (r"smallint|tinyint|byteint", "smallint"),
    (r"\bint|integer|long", "integer"),
    (r"double|float|real", "float"),
    (r"bool", "boolean"),
    (r"timestamp|datetime", "timestamp"),
    (r"\bdate\b", "date"),
    (r"time", "time"),
    (r"struct|record|object|row\b", "struct"),
    (r"array|list", "array"),
    (r"map|dict", "map"),
    (r"binary|bytes|varbinary", "binary"),
    (r"char|string|text|json|variant|xml", "text"),
)
_FAMILY_RE = [(re.compile(pat, re.IGNORECASE), fam) for pat, fam in _FAMILIES]


def norm_family(raw: str) -> str:
    """Canonical type family; params are stripped (varchar(64) -> text)."""
    t = raw.strip().lower()
    base = re.split(r"[<(]", t, maxsplit=1)[0].strip()
    for rx, fam in _FAMILY_RE:
        if rx.search(base):
            return fam
    return base or "unknown"


# ---------------------------------------------------------------------------
# Contract discovery + normalization


_CONTRACT_NAMES = {"datacontract.yml", "datacontract.yaml"}
_CONTRACT_SUFFIX = re.compile(r"\.(?:datacontract|odcs)\.(?:yml|yaml|json)$", re.IGNORECASE)
_KNOWN_TOP = {
    "id",
    "name",
    "version",
    "info",
    "status",
    "tenant",
    "servers",
    "schema",
    "quality",
    "servicelevels",
    "slaproperties",
    "sladefaultelement",
    "datacontractspecification",
    "apiversion",
    "kind",
    "domain",
    "description",
    "models",  # dbt schema.yml reuse: treated as schema-adjacent
}


def _is_contract_file(rel: Path, doc: Any) -> bool:
    name = rel.name.lower()
    if name in _CONTRACT_NAMES or _CONTRACT_SUFFIX.search(name):
        return True
    if isinstance(doc, dict):
        keys = {str(k).lower() for k in doc}
        return "datacontractspecification" in keys or (
            "kind" in keys and str(doc.get("kind")).lower() == "datacontract"
        )
    return False


def _fields_from(raw: Any, type_key: str) -> list[ContractField]:
    """Fields arrive as a mapping (datacontract-cli) or a list (ODCS)."""
    out: list[ContractField] = []
    if isinstance(raw, dict):
        items = [
            ({"name": k, **(v if isinstance(v, dict) else {"type": v})}) for k, v in raw.items()
        ]
    elif isinstance(raw, list):
        items = [i for i in raw if isinstance(i, dict)]
    else:
        return out
    for item in items:
        name = str(item.get("name") or "")
        raw_type = str(item.get(type_key) or item.get("type") or "")
        if not name:
            continue
        out.append(
            ContractField(
                name=name,
                type=norm_family(raw_type),
                raw_type=raw_type,
                required=bool(item.get("required")),
            )
        )
    return out


def _sla_of(doc: dict[str, Any]) -> dict[str, str]:
    """servicelevels (datacontract) or slaProperties (ODCS) -> prop names."""
    sla: dict[str, str] = {}
    levels = doc.get("servicelevels") or doc.get("serviceLevels")
    if isinstance(levels, dict):
        for key, val in levels.items():
            if isinstance(val, dict):
                val = " ".join(str(v) for v in val.values())
            sla[str(key)] = "" if val is None else str(val)
    props = doc.get("slaProperties")
    if isinstance(props, list):
        for p in props:
            if isinstance(p, dict) and p.get("property"):
                sla[str(p["property"])] = str(p.get("value") or "")
    return sla


def _quality_of(doc: dict[str, Any]) -> list[str]:
    q = doc.get("quality")
    if isinstance(q, list):
        return [
            str(i.get("type") or i.get("name") or i) if isinstance(i, dict) else str(i) for i in q
        ]
    return [str(q)] if q else []


def _servers_of(doc: dict[str, Any], odcs: bool) -> list[ContractServer]:
    raw = doc.get("servers")
    out: list[ContractServer] = []
    if isinstance(raw, dict):  # datacontract-cli: {name: {type,...}}
        for name, spec in raw.items():
            spec = spec if isinstance(spec, dict) else {}
            out.append(
                ContractServer(
                    str(name),
                    str(spec.get("type") or ""),
                    str(spec.get("environment") or spec.get("env") or ""),
                )
            )
    elif isinstance(raw, list):  # ODCS: [{server|name, type, ...}]
        for spec in raw:
            if isinstance(spec, dict):
                out.append(
                    ContractServer(
                        str(spec.get("server") or spec.get("name") or ""),
                        str(spec.get("type") or ""),
                        str(spec.get("environment") or spec.get("env") or ""),
                    )
                )
    return out


def _objects_of(doc: dict[str, Any], odcs: bool) -> list[ContractObject]:
    raw = doc.get("schema")
    if raw is None and "models" in doc:  # ODCS uses `schema`; tolerate `models`
        raw = doc.get("models")
    entries = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else []
    objects: list[ContractObject] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("table") or "")
        fields_raw = entry.get("properties") if odcs else entry.get("fields")
        if fields_raw is None:
            fields_raw = entry.get("fields") or entry.get("properties")
        objects.append(
            ContractObject(
                name=name,
                fields=tuple(_fields_from(fields_raw, "logicalType" if odcs else "type")),
            )
        )
    return objects


def _contract_from(rel: Path, doc: dict[str, Any]) -> DataContract:
    keys = {str(k).lower() for k in doc}
    odcs = "apiversion" in keys or str(doc.get("kind") or "").lower() == "datacontract"
    fmt = "odcs" if odcs else ("datacontract" if "datacontractspecification" in keys else "unknown")
    info_raw = doc.get("info")
    info = info_raw if isinstance(info_raw, dict) else {}
    owner = str(info.get("owner") or doc.get("owner") or "")
    contract = DataContract(
        id=str(doc.get("id") or doc.get("name") or rel.stem),
        file=rel,
        format=fmt,
        owner=owner,
        objects=_objects_of(doc, odcs),
        sla=_sla_of(doc),
        quality=_quality_of(doc),
        servers=_servers_of(doc, odcs),
        unsupported=sorted(k for k in keys - _KNOWN_TOP),
    )
    return contract


# ---------------------------------------------------------------------------
# Detected table schemas (cross-domain evidence for DCTR003)


def _columns_from_create(text: str) -> dict[str, str]:
    """``CREATE TABLE x (col TYPE, ...)`` column -> family via sqlglot."""
    try:
        from sqlglot import exp, parse_one
        from sqlglot.errors import SqlglotError
    except ImportError:
        return {}
    for dialect in ("spark", None):
        try:
            stmt = parse_one(text, read=dialect)
        except (SqlglotError, ValueError):
            continue
        schema = stmt.find(exp.Schema)
        if schema is None:
            continue
        cols = {
            c.name: norm_family(c.kind.sql() if c.kind is not None else "")
            for c in schema.expressions
            if isinstance(c, exp.ColumnDef)
        }
        if cols:
            return cols
    return {}


def _bq_schema_fields(raw: str) -> dict[str, str]:
    """Terraform ``schema = jsonencode([{name,type,...}])`` value -> fields."""
    m = re.search(r"\[[\s\S]*\]", raw)
    if not m:
        return {}
    try:
        rows = json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return {}
    return {
        str(r["name"]): norm_family(str(r.get("type") or ""))
        for r in rows
        if isinstance(r, dict) and r.get("name")
    }


def _detected_schemas(ctx: ProjectContext) -> dict[str, DetectedSchema]:
    out: dict[str, DetectedSchema] = {}

    def merge(name: str, fields: dict[str, str], evidence: str) -> None:
        if not fields:
            return
        tail = name.rpartition(".")[2].lower() or name.lower()
        existing = out.get(tail)
        if existing is None:
            out[tail] = DetectedSchema(name, dict(fields), evidence)
        else:
            existing.fields.update(fields)

    from forge_doctor_data.analyzers.sql_ast import analyze_sql

    for stmt in analyze_sql(ctx).statements:
        if stmt.kind != "create" or not stmt.tables_written:
            continue
        merge(stmt.tables_written[0], _columns_from_create(stmt.text), "static")

    from forge_doctor_data.analyzers.terraform_model import terraform_model

    for block in terraform_model(ctx).blocks:
        if (
            block.kind != "resource"
            or not block.labels
            or block.labels[0] != "google_bigquery_table"
        ):
            continue
        raw = str(block.attrs.get("schema") or "")
        name = str(
            block.attrs.get("table_id")
            or (block.labels[1] if len(block.labels) > 1 else block.address)
        )
        merge(name, _bq_schema_fields(raw), "config")

    from forge_doctor_data.analyzers.bigquery_model import bigquery_model
    from forge_doctor_data.analyzers.snowflake_model import snowflake_model

    for vendor in (snowflake_model(ctx), bigquery_model(ctx)):
        for row in vendor.observed:
            if getattr(row, "shape", "") != "columns":
                continue
            table = row.get("table_name", "table")
            col = row.get("column_name", "column")
            typ = row.get("data_type", "type")
            if table and col and typ:
                merge(table, {col: norm_family(typ)}, "observed_metadata")
    return out


# ---------------------------------------------------------------------------
# Entry point


def _load(ctx: ProjectContext, rel: Path) -> Any:
    from forge_doctor_data.core.contract import _load_yaml

    text = ctx.read_text(rel)
    if text is None:
        return None
    if rel.suffix.lower() == ".json":
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
    doc, err = _load_yaml(text)
    return None if err else doc


def datacontract_model(ctx: ProjectContext) -> DataContractModel:
    """Memoized contract model over ctx evidence."""
    cached = getattr(ctx, _CACHE_ATTR, None)
    if cached is not None:
        return cast(DataContractModel, cached)
    model = DataContractModel()
    for rel in sorted(ctx.files):
        if rel.suffix.lower() not in {".yml", ".yaml", ".json"}:
            continue
        name = rel.name.lower()
        hinted = name in _CONTRACT_NAMES or bool(_CONTRACT_SUFFIX.search(name))
        doc = _load(ctx, rel)
        if not hinted and not _is_contract_file(rel, doc):
            continue
        if not isinstance(doc, dict) or not doc:
            if hinted:
                model.unparsed.append(rel.as_posix())
            continue
        model.contracts.append(_contract_from(rel, doc))
    if model.contracts:
        model.detected = _detected_schemas(ctx)
    setattr(ctx, _CACHE_ATTR, model)
    return model
