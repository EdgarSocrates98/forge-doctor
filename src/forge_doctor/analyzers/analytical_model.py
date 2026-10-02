"""Shared real-time OLAP engine model — ClickHouse, Pinot, Druid (spec 219).

One ``AnalyticalEngineModel`` over three thin evidence adapters:

- **ClickHouse** — ``CREATE TABLE ... ENGINE = <family>`` in authored
  SQL (parsed by the shared sql index; generic dialect retains the
  ``ENGINE``/``ORDER BY``/``PARTITION BY`` property text). Engine
  attribution is an explicit family allowlist — ``ENGINE=InnoDB``
  (MySQL) and friends never attribute.
- **Pinot** — table config JSON (``*.table.json`` or any JSON with
  ``tableName`` + ``tableType``/``segmentsConfig``) and schema JSON
  (``schemaName`` + ``dimensionFieldSpecs``/``metricFieldSpecs``).
- **Druid** — ingestion-spec JSON (``ingestionSpec``, or a dict
  carrying ``dataSchema`` + ``ioConfig``).

Observed metadata: JSON exports under ``clickhouse/``, ``pinot/``,
``druid/``, or ``.forge-doctor/evidence/`` claimed only by positive
field signals (system-table / segment / query-log shapes). Plain JSON
and plain SQL never attribute (adversarial lab pins silence).

StarRocks/Doris deferral (spec open question): nothing here assumes
the three members are exhaustive — the model is a bag of typed rows
keyed by ``engine``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext

_CACHE_ATTR = "_forge_doctor_analytical_model"

# ClickHouse table-engine families that prove ClickHouse authorship.
# Ambiguous engines (Memory, CSV, InnoDB, MyISAM, Archive, Federated,
# Blackhole) are deliberately absent — they exist in MySQL too.
_CH_ENGINE_NAMES = {
    "mergetree",
    "distributed",
    "kafka",
    "tinylog",
    "stripelog",
    "log",
    "buffer",
    "s3",
    "hdfs",
    "url",
    "join",
    "set",
    "dictionary",
    "embeddedrocksdb",
    "keepermap",
    "azureblobstorage",
    "materializedpostgresql",
    "mysql",
    "postgresql",
    "mongodb",
    "jdbc",
    "odbc",
    "nats",
    "rabbitmq",
    "rocksdb",
    "sqlite",
}
_CH_ENGINE_PREFIXES = (
    "replicated",
    "replacing",
    "summing",
    "aggregating",
    "collapsing",
    "versionedcollapsing",
    "graphite",
)


def _is_ch_engine(name: str) -> bool:
    low = name.lower()
    return low in _CH_ENGINE_NAMES or low.startswith(_CH_ENGINE_PREFIXES)


_ENGINE_RE = re.compile(r"\bENGINE\s*=\s*(\w+)", re.IGNORECASE)
_ORDER_RE = re.compile(
    r"\bORDER\s+BY\s+([^;\n]+?)(?:\s+(?:PARTITION|PRIMARY|SETTINGS)\b|$)", re.IGNORECASE
)
_PARTITION_RE = re.compile(
    r"\bPARTITION\s+BY\s+([^;\n]+?)(?:\s+(?:ORDER|PRIMARY|SETTINGS)\b|$)", re.IGNORECASE
)
_DISTRIBUTED_RE = re.compile(r"\bDistributed\s*\(\s*[^,]+,\s*[^,]+,\s*'?([\w.]+)", re.IGNORECASE)
_KAFKA_SETTINGS_RE = re.compile(r"(kafka_\w+)\s*=\s*'([^']+)'", re.IGNORECASE)
_KEEPER_NAME_RE = re.compile(r"keeper|zookeeper", re.IGNORECASE)
_KEEPER_XML_RE = re.compile(r"<(?:zookeeper|keeper_server|clickhouse-keeper)", re.IGNORECASE)
_CREATE_TABLE_RE = re.compile(
    r"\bCREATE\s+(?:MATERIALIZED\s+VIEW\s+\w+\s+TO\s+|TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?)([\w.]+)",
    re.IGNORECASE,
)

# Dedupe-family engines that constitute a "dedupe/ordering plan" for
# Kafka ingestion (CH004).
_DEDUPE_ENGINE_RE = re.compile(
    r"^(?:Replacing|Summing|Aggregating|Collapsing|VersionedCollapsing)", re.IGNORECASE
)


@dataclass(frozen=True)
class EngineTable:
    """One table/datasource/relation owned by an analytical engine."""

    engine: str  # clickhouse | pinot | druid
    name: str
    file: Path
    line: int = 0
    kind: str = "table"  # table | materialized_view | datasource | pinot_table
    table_engine: str = ""  # ClickHouse engine family name
    order_by: str = ""
    partition_by: str = ""
    table_type: str = ""  # Pinot REALTIME/OFFLINE
    props: tuple[tuple[str, str], ...] = ()  # engine-specific flat config

    def prop(self, *keys: str) -> str:
        d = {k.lower(): v for k, v in self.props}
        for key in keys:
            if key.lower() in d:
                return d[key.lower()]
        return ""


@dataclass(frozen=True)
class PinotSchema:
    """One Pinot ``*.schema.json`` row-set."""

    name: str
    file: Path
    dims: tuple[str, ...] = ()
    metrics: tuple[str, ...] = ()
    # dims with explicit high-cardinality evidence (cardinality field
    # > threshold, or named in a table config's noDictionaryColumns).
    high_card_dims: tuple[str, ...] = ()


@dataclass(frozen=True)
class ObservedRow:
    """One row from an exported engine-metadata artifact."""

    engine: str
    file: Path
    fields: tuple[tuple[str, str], ...]

    def get(self, *keys: str) -> str:
        d = {k.lower(): v for k, v in self.fields}
        for key in keys:
            if key.lower() in d:
                return d[key.lower()]
        return ""


@dataclass
class AnalyticalEngineModel:
    """All real-time OLAP evidence for a project."""

    tables: list[EngineTable] = field(default_factory=list)
    pinot_schemas: list[PinotSchema] = field(default_factory=list)
    keeper_files: list[Path] = field(default_factory=list)  # CH keeper/zk config
    observed: list[ObservedRow] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)

    @property
    def has_evidence(self) -> bool:
        return bool(self.tables) or bool(self.pinot_schemas)

    def engines(self) -> set[str]:
        return {t.engine for t in self.tables} | {"pinot" for _ in self.pinot_schemas}

    def tables_of(self, engine: str) -> list[EngineTable]:
        return [t for t in self.tables if t.engine == engine]


# ---------------------------------------------------------------------------
# ClickHouse


def _scan_clickhouse(ctx: ProjectContext, model: AnalyticalEngineModel) -> None:
    from forge_doctor.analyzers.sql_ast import analyze_sql

    for stmt in analyze_sql(ctx).statements:
        if stmt.kind != "create":
            continue
        m = _ENGINE_RE.search(stmt.text)
        if not m or not _is_ch_engine(m.group(1)):
            continue
        name_m = _CREATE_TABLE_RE.search(stmt.text)
        name = name_m.group(1) if name_m else "-"
        kind = (
            "materialized_view"
            if re.search(r"MATERIALIZED\s+VIEW", stmt.text, re.IGNORECASE)
            else "table"
        )
        order = _ORDER_RE.search(stmt.text)
        part = _PARTITION_RE.search(stmt.text)
        props = list(_KAFKA_SETTINGS_RE.findall(stmt.text))
        dist = _DISTRIBUTED_RE.search(stmt.text)
        if dist:
            props.append(("distributed_local", dist.group(1)))
        model.tables.append(
            EngineTable(
                engine="clickhouse",
                name=name,
                file=stmt.file,
                line=stmt.line,
                kind=kind,
                table_engine=m.group(1),
                order_by=order.group(1).strip() if order else "",
                partition_by=part.group(1).strip() if part else "",
                props=tuple(props),
            )
        )
    # keeper/zookeeper config evidence — filename or XML content signal
    for rel in sorted(ctx.files):
        if _KEEPER_NAME_RE.search(rel.name):
            model.keeper_files.append(rel)
        elif rel.suffix.lower() == ".xml":
            text = ctx.read_text(rel)
            if text and _KEEPER_XML_RE.search(text):
                model.keeper_files.append(rel)


# ---------------------------------------------------------------------------
# Pinot


def _pinot_table_config(doc: dict[str, Any]) -> bool:
    return "tableName" in doc and (
        "tableType" in doc or "segmentsConfig" in doc or "tenants" in doc
    )


def _scan_pinot(ctx: ProjectContext, model: AnalyticalEngineModel) -> None:
    for rel in sorted(ctx.files):
        if rel.suffix.lower() != ".json":
            continue
        text = ctx.read_text(rel)
        if text is None:
            continue
        if not any(k in text for k in ("tableName", "schemaName", "ingestionSpec", "dataSchema")):
            continue
        try:
            doc = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(doc, dict):
            continue
        if _pinot_table_config(doc):
            model.tables.append(
                EngineTable(
                    engine="pinot",
                    name=str(doc.get("tableName", "-")),
                    file=rel,
                    kind="pinot_table",
                    table_type=str(doc.get("tableType", "")).upper(),
                    props=tuple(
                        sorted(
                            (str(k), json.dumps(v) if isinstance(v, (dict, list)) else str(v))
                            for k, v in _flatten_pinot(doc).items()
                        )
                    ),
                )
            )
        elif "schemaName" in doc and any(
            k in doc for k in ("dimensionFieldSpecs", "metricFieldSpecs", "timeFieldSpec")
        ):
            dims: list[str] = []
            high_card: list[str] = []
            for d in doc.get("dimensionFieldSpecs", []):
                if isinstance(d, dict):
                    dims.append(str(d.get("name", "")))
                    card = d.get("cardinality")
                    try:
                        if card is not None and int(card) > 1000:
                            high_card.append(str(d.get("name", "")))
                    except (TypeError, ValueError):
                        pass
            metrics = [
                str(m.get("name", ""))
                for m in doc.get("metricFieldSpecs", [])
                if isinstance(m, dict)
            ]
            model.pinot_schemas.append(
                PinotSchema(
                    name=str(doc.get("schemaName", "-")),
                    file=rel,
                    dims=tuple(dims),
                    metrics=tuple(metrics),
                    high_card_dims=tuple(high_card),
                )
            )


def _flatten_pinot(doc: dict[str, Any]) -> dict[str, Any]:
    """Surface the check-relevant Pinot keys as flat props."""
    out: dict[str, Any] = {}
    seg = doc.get("segmentsConfig")
    if isinstance(seg, dict):
        for k in ("retentionTimeValue", "retentionTimeUnit", "replicasPerPartition", "replication"):
            if k in seg:
                out[f"segmentsConfig.{k}"] = seg[k]
    idx = doc.get("tableIndexConfig")
    if isinstance(idx, dict):
        for k in ("invertedIndexColumns", "sortedColumn", "noDictionaryColumns"):
            if k in idx:
                out[f"tableIndexConfig.{k}"] = idx[k]
        out["tableIndexConfig.starTree"] = bool(idx.get("starTreeIndexConfigs"))
    for k in ("fieldConfigList", "ingestionConfig", "streamConfigs"):
        if k in doc:
            out[k] = doc[k]
    return out


# ---------------------------------------------------------------------------
# Druid


def _scan_druid(ctx: ProjectContext, model: AnalyticalEngineModel) -> None:
    for rel in sorted(ctx.files):
        if rel.suffix.lower() != ".json":
            continue
        text = ctx.read_text(rel)
        if text is None or ("ingestionSpec" not in text and "dataSchema" not in text):
            continue
        try:
            doc = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(doc, dict):
            continue
        spec = doc.get("ingestionSpec", doc.get("spec"))
        if not isinstance(spec, dict) or not isinstance(spec.get("dataSchema"), dict):
            continue
        data_schema = spec["dataSchema"]
        gran = spec.get("granularitySpec", {}) or data_schema.get("granularitySpec", {})
        dims_spec = data_schema.get("dimensionsSpec", {})
        dims = dims_spec.get("dimensions", []) if isinstance(dims_spec, dict) else []
        io = spec.get("ioConfig", {})
        tuning = spec.get("tuningConfig", {})
        props: list[tuple[str, str]] = [
            ("rollup", str(gran.get("rollup", "")).lower()),
            ("segmentGranularity", str(gran.get("segmentGranularity", ""))),
            ("partitionsSpec", "true" if isinstance(tuning.get("partitionsSpec"), dict) else ""),
            ("io.type", str(io.get("type", ""))),
            ("metrics.count", str(len(data_schema.get("metricsSpec", []) or []))),
            ("dims.count", str(len(dims) if isinstance(dims, list) else 0)),
        ]
        model.tables.append(
            EngineTable(
                engine="druid",
                name=str(data_schema.get("dataSource", "-")),
                file=rel,
                kind="datasource",
                props=tuple(props),
            )
        )


# ---------------------------------------------------------------------------
# Observed metadata


_OBSERVED_DIRS = re.compile(
    r"(?:^|[/\\])(?:clickhouse|pinot|druid|\.forge-doctor[/\\]evidence)(?:$|[/\\])",
    re.IGNORECASE,
)
_OBSERVED_SIGNAL = {
    "clickhouse": re.compile(r"partition_key|sorting_key|total_parts|engine_full", re.I),
    "pinot": re.compile(r"^quer(y|ies)$|executionmillis|numdocs|schemename", re.I),
    "druid": re.compile(r"datasource|segment|num_rows|shard_spec", re.I),
}


def _scan_observed(model: AnalyticalEngineModel, ctx: ProjectContext) -> None:
    for rel in sorted(ctx.files):
        if rel.suffix.lower() != ".json" or not _OBSERVED_DIRS.search(rel.as_posix()):
            continue
        text = ctx.read_text(rel)
        if text is None:
            continue
        try:
            doc = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            model.unparsed.append(rel.as_posix())
            continue
        rows: list[dict[str, Any]]
        if isinstance(doc, dict):
            rows = (
                [doc]
                if all(isinstance(v, (str, int, float, bool)) for v in doc.values())
                else [r for r in doc.values() if isinstance(r, dict)]
            )
        elif isinstance(doc, list):
            rows = [r for r in doc if isinstance(r, dict)]
        else:
            rows = []
        # attribute by the parent dir first, then field signal
        dir_engine = ""
        for part in rel.parts:
            low = part.lower()
            if low in ("clickhouse", "pinot", "druid"):
                dir_engine = low
        claimed = False
        for row in rows:
            keys = {str(k).lower() for k in row}
            engine = dir_engine or next(
                (e for e, sig in _OBSERVED_SIGNAL.items() if any(sig.search(k) for k in keys)),
                "",
            )
            if not engine:
                continue
            # keep rows honest: dir-claimed rows still need one field hit
            if dir_engine and not any(
                sig.search(k) for sig in _OBSERVED_SIGNAL.values() for k in keys
            ):
                continue
            model.observed.append(
                ObservedRow(
                    engine,
                    rel,
                    tuple(sorted((str(k), str(v)) for k, v in row.items())),
                )
            )
            claimed = True
        if not claimed:
            model.unparsed.append(rel.as_posix())


# ---------------------------------------------------------------------------
# Model entry point


def analytical_model(ctx: ProjectContext) -> AnalyticalEngineModel:
    """Memoized shared OLAP-engine model over ctx evidence."""
    cached = getattr(ctx, _CACHE_ATTR, None)
    if cached is not None:
        return cast(AnalyticalEngineModel, cached)
    model = AnalyticalEngineModel()
    _scan_clickhouse(ctx, model)
    _scan_pinot(ctx, model)
    _scan_druid(ctx, model)
    if model.has_evidence:
        _scan_observed(model, ctx)
    setattr(ctx, _CACHE_ATTR, model)
    return model


def has_materialized_view(model: AnalyticalEngineModel) -> bool:
    return any(t.engine == "clickhouse" and t.kind == "materialized_view" for t in model.tables)


def has_dedupe_plan(model: AnalyticalEngineModel) -> bool:
    """Kafka ingestion dedupe plan: dedupe-family engine or a MV sink."""
    return has_materialized_view(model) or any(
        t.engine == "clickhouse" and _DEDUPE_ENGINE_RE.search(t.table_engine) for t in model.tables
    )
