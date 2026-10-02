"""Trino federated-SQL model — config-centric evidence (spec 218).

Evidence planes:

- **`etc/catalog/*.properties`** — one file per catalog;
  ``connector.name=<connector>`` is the strong attribution marker.
- **`config.properties`** — coordinator/worker flags
  (``coordinator=true``, ``discovery.uri``, memory limits, spill keys,
  ``resource-groups.config-file``, event listeners).
- **`node.properties` / `jvm.config`** — cluster surface when adjacent
  to catalog/config evidence.
- **Three-part SQL names** — ``catalog.schema.table`` refs in authored
  SQL, from the shared sql index.
- **Observed cluster exports** — JSON under ``trino/`` or
  ``.forge-doctor/evidence/`` with a positive Trino field signal
  (``coordinator``, ``nodeVersion``, ``environment``).

Attribution gate: at least one catalog file carrying
``connector.name``, or a ``config.properties`` carrying
``coordinator``/``discovery.uri``. Plain ``.properties`` files and
three-part SQL alone never attribute to Trino (the adversarial lab pins
this). Presto variants are out of scope per the spec.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext

_CACHE_ATTR = "_forge_doctor_trino_model"


def _parse_properties(text: str) -> dict[str, str]:
    """Java ``.properties`` subset: key=value, #/! comments, no escapes."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "!")):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
        elif ":" in line:
            key, _, val = line.partition(":")
        else:
            continue
        out[key.strip()] = val.strip()
    return out


@dataclass(frozen=True)
class TrinoCatalog:
    """One ``catalog/*.properties`` file with its connector."""

    name: str  # file stem
    connector: str  # connector.name value
    file: Path
    props: tuple[tuple[str, str], ...] = ()

    def prop(self, key: str) -> str:
        return dict(self.props).get(key, "")


@dataclass(frozen=True)
class ThreePartRef:
    """``catalog.schema.table`` reference seen in authored SQL."""

    catalog: str
    name: str  # full dotted name
    file: Path
    line: int


@dataclass(frozen=True)
class ObservedRow:
    """One row from an exported cluster-info artifact."""

    file: Path
    fields: tuple[tuple[str, str], ...]

    def get(self, *keys: str) -> str:
        d = {k.lower(): v for k, v in self.fields}
        for key in keys:
            if key.lower() in d:
                return d[key.lower()]
        return ""


@dataclass
class TrinoProjectModel:
    """All Trino evidence for a project."""

    catalogs: list[TrinoCatalog] = field(default_factory=list)
    coordinator_props: dict[str, str] = field(default_factory=dict)
    worker_props: dict[str, str] = field(default_factory=dict)
    node_props: dict[str, str] = field(default_factory=dict)
    jvm_flags: tuple[str, ...] = ()
    config_file: Path | None = None  # the config.properties that parsed
    node_file: Path | None = None
    resource_groups_file: Path | None = None  # resource-groups.json evidence
    event_listener_file: Path | None = None
    refs: list[ThreePartRef] = field(default_factory=list)
    observed: list[ObservedRow] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)

    @property
    def has_evidence(self) -> bool:
        return bool(self.catalogs) or bool(self.coordinator_props)

    @property
    def is_coordinator(self) -> bool:
        return self.coordinator_props.get("coordinator", "").lower() == "true"

    def catalog_names(self) -> set[str]:
        return {c.name for c in self.catalogs}

    def connector(self, name: str) -> str:
        for c in self.catalogs:
            if c.name == name:
                return c.connector
        return ""

    def has_spill_config(self) -> bool:
        """Spill-to-disk keys on coordinator or worker config."""
        keys = set(self.coordinator_props) | set(self.worker_props)
        return any(
            k.startswith(("query.max-spill", "spill", "spiller-", "experimental.spill"))
            or "spill" in k
            for k in keys
        )

    def has_resource_groups(self) -> bool:
        if self.resource_groups_file is not None:
            return True
        return "resource-groups.config-file" in self.coordinator_props


# ---------------------------------------------------------------------------
# Discovery


_CATALOG_DIR = re.compile(r"(?:^|[/\\])(?:etc[/\\])?catalog(?:$|[/\\])", re.IGNORECASE)
_CONFIG_NAMES = {"config.properties", "node.properties", "jvm.config"}
_RG_NAMES = re.compile(r"resource-groups.*\.json$", re.IGNORECASE)
_EL_NAMES = re.compile(r"event-listener.*\.properties$", re.IGNORECASE)

# Keys that prove a config.properties is Trino (not a random props file).
_CONFIG_MARKERS = {"coordinator", "discovery.uri", "node-scheduler.include-coordinator"}
# Test-only connectors that mustn't sit in a real deployment (TRINO003).
TEST_CONNECTORS = {"tpch", "tcds", "jmx", "system", "blackhole", "memory", "localfile"}
# Connectors that definitely move data (non-test surface).
DATA_CONNECTOR_HINT = {
    "hive",
    "iceberg",
    "delta",
    "deltalake",
    "jdbc",
    "mysql",
    "postgresql",
    "oracle",
    "sqlserver",
    "kafka",
    "mongodb",
    "cassandra",
    "elasticsearch",
    "opensearch",
    "redis",
    "clickhouse",
    "pinot",
    "druid",
    "bigquery",
    "snowflake",
    "redshift",
    "kinesis",
    "s3",
    "gs",
    "phoenix",
    "ignite",
    "prometheus",
    "accumulo",
    "kudu",
    "singlestore",
    "mariadb",
}

_OBSERVED_FIELD_SIGNAL = re.compile(
    r"coordinator|nodeversion|environment|datanasupport|runningtasks|activedrivers",
    re.IGNORECASE,
)


def _catalog_files(ctx: ProjectContext) -> list[Path]:
    out = []
    for rel in sorted(ctx.files):
        if rel.suffix.lower() != ".properties":
            continue
        if _CATALOG_DIR.search(rel.as_posix()):
            out.append(rel)
    return out


def _scan_sql_refs(ctx: ProjectContext, model: TrinoProjectModel) -> None:
    from forge_doctor.analyzers.sql_ast import analyze_sql

    for stmt in analyze_sql(ctx).statements:
        for name in (*stmt.tables_read, *stmt.tables_written):
            if name.count(".") >= 2:
                catalog = name.split(".")[0]
                model.refs.append(ThreePartRef(catalog, name, stmt.file, stmt.line))


def _scan_observed(model: TrinoProjectModel, ctx: ProjectContext) -> None:
    ev_dir = re.compile(r"(?:^|[/\\])(?:trino|\.forge-doctor[/\\]evidence)(?:$|[/\\])")
    for rel in sorted(ctx.files):
        if rel.suffix.lower() != ".json" or not ev_dir.search(rel.as_posix()):
            continue
        text = ctx.read_text(rel)
        if text is None:
            continue
        try:
            doc = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            model.unparsed.append(rel.as_posix())
            continue
        rows: list[dict[str, Any]] = []
        if isinstance(doc, dict):
            rows = (
                [doc]
                if all(isinstance(v, (str, int, float, bool)) for v in doc.values())
                else [r for r in doc.values() if isinstance(r, dict)]
            )
        elif isinstance(doc, list):
            rows = [r for r in doc if isinstance(r, dict)]
        claimed = False
        for row in rows:
            keys = {str(k).lower() for k in row}
            if any(_OBSERVED_FIELD_SIGNAL.search(k) for k in keys):
                model.observed.append(
                    ObservedRow(
                        rel,
                        tuple(sorted((str(k), str(v)) for k, v in row.items())),
                    )
                )
                claimed = True
        if not claimed:
            model.unparsed.append(rel.as_posix())


def trino_model(ctx: ProjectContext) -> TrinoProjectModel:
    """Memoized Trino model over ctx evidence."""
    cached = getattr(ctx, _CACHE_ATTR, None)
    if cached is not None:
        return cast(TrinoProjectModel, cached)
    model = TrinoProjectModel()

    for rel in _catalog_files(ctx):
        props = _parse_properties(ctx.read_text(rel) or "")
        connector = props.get("connector.name", "")
        if connector:
            model.catalogs.append(
                TrinoCatalog(rel.stem, connector.lower(), rel, tuple(sorted(props.items())))
            )

    # config.properties / node.properties / jvm.config — claim only with
    # Trino markers or adjacency to catalog evidence.
    catalog_dirs = {c.file.parent.parent for c in model.catalogs}
    for rel in sorted(ctx.files):
        name = rel.name.lower()
        posix = rel.as_posix()
        if name == "config.properties":
            props = _parse_properties(ctx.read_text(rel) or "")
            if set(props) & _CONFIG_MARKERS or rel.parent in catalog_dirs:
                model.config_file = rel
                if props.get("coordinator", "").lower() == "true":
                    model.coordinator_props = props
                else:
                    model.worker_props = {**model.worker_props, **props}
        elif name == "node.properties" and rel.parent in catalog_dirs:
            model.node_file = rel
            model.node_props = _parse_properties(ctx.read_text(rel) or "")
        elif name == "jvm.config" and rel.parent in catalog_dirs:
            model.jvm_flags = tuple(
                line.strip()
                for line in (ctx.read_text(rel) or "").splitlines()
                if line.strip() and not line.strip().startswith("#")
            )
        elif _RG_NAMES.search(name) and ("trino" in posix.lower() or rel.parent in catalog_dirs):
            model.resource_groups_file = rel
        elif _EL_NAMES.search(name) and rel.parent in catalog_dirs:
            model.event_listener_file = rel

    if model.has_evidence:
        _scan_sql_refs(ctx, model)
        _scan_observed(model, ctx)
    setattr(ctx, _CACHE_ATTR, model)
    return model
