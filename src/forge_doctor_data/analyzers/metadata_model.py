"""Metadata/catalog estate model — DataHub, OpenMetadata, Glue, Unity (spec 221).

The model captures the *declared* estate so drift checks (META###) can
compare it against the detected `DataPlatformGraph`:

- **DataHub** — ``*.datahub.json`` exports: MCP/MCE shapes carrying
  ``entityUrn``/``urn`` + ``aspects`` (datasetProperties,
  ownership, globalTags, glossaryTerms, upstreamLineage).
- **OpenMetadata** — ``*.ometa.json`` entity exports:
  ``fullyQualifiedName``/``entityType`` + ``owners``/``tags``/
  ``description``/``service``.
- **Glue Data Catalog** — ``glue``/``glue-catalog`` observed exports
  with ``DatabaseList``/``TableList``/``Table`` rows (minimal coverage
  signal; the graph keeps owning the detected side).
- **Unity Catalog** — ``unity``/``*unity*.json`` exports with
  ``catalog_name``/``schema_name``/``table_name``/``metastore`` keys
  (minimal presence + coverage only).

Ingestion recipes are parsed for connector *types* only — connection
blocks (host/user/password) are never carried into the model, per the
spec constraint.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from forge_doctor_data.core.context import ProjectContext

_CACHE_ATTR = "_forge_doctor_data_metadata_model"


@dataclass(frozen=True)
class CatalogDataset:
    """One declared catalog dataset."""

    vendor: str  # datahub | openmetadata | glue | unity
    urn: str  # catalog-native identifier (urn or fqn)
    name: str  # tail identifier for matching against detected entities
    file: Path
    platform: str = ""  # datahub urn platform segment
    environment: str = ""  # PROD/DEV/... fabric hint
    qualified: str = ""  # full dotted name for finer matching
    owners: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    glossary_terms: tuple[str, ...] = ()
    has_description: bool = False
    upstreams: tuple[str, ...] = ()  # declared lineage tail-names


@dataclass(frozen=True)
class IngestionRecipe:
    """Connector types only — never connection secrets."""

    vendor: str
    file: Path
    source_type: str
    sink_type: str = ""


@dataclass
class MetadataEstateModel:
    """All declared-catalog evidence for a project."""

    datasets: list[CatalogDataset] = field(default_factory=list)
    recipes: list[IngestionRecipe] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)

    @property
    def has_evidence(self) -> bool:
        return bool(self.datasets or self.recipes)

    def vendors(self) -> set[str]:
        return {d.vendor for d in self.datasets} | {r.vendor for r in self.recipes}


# ---------------------------------------------------------------------------
# Shared extraction helpers


_URN_RE = re.compile(r"urn:li:dataset:\(\s*urn:li:dataPlatform:([^,]+),\s*([^,]+),\s*(\w+)\s*\)")


def _tail(name: str) -> str:
    """Tail identifier: last dotted/slashed segment."""
    return name.rstrip("/").rsplit("/", 1)[-1].rsplit(".", 1)[-1]


def _str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        out = []
        for v in value:
            if isinstance(v, str):
                out.append(v)
            elif isinstance(v, dict):
                for key in (
                    "dataset",
                    "name",
                    "tagFQN",
                    "tag",
                    "urn",
                    "entityUrn",
                    "owner",
                    "type",
                    "value",
                ):
                    if isinstance(v.get(key), str):
                        out.append(str(v[key]).split(":")[-1])
                        break
        return out
    return []


def _flatten_aspects(aspects: Any) -> dict[str, Any]:
    """Normalize DataHub aspect arrays/maps into one flat dict."""
    out: dict[str, Any] = {}
    if isinstance(aspects, dict):
        return aspects
    if isinstance(aspects, list):
        for a in aspects:
            if isinstance(a, dict):
                out.update(a)
    return out


def _owners_from(aspects: dict[str, Any]) -> list[str]:
    own = aspects.get("ownership") or aspects.get("owners") or aspects.get("owner")
    if isinstance(own, dict):
        owners = own.get("owners", [])
        return _str_list(owners) if isinstance(owners, list) else []
    return _str_list(own)


def _tags_from(aspects: dict[str, Any]) -> list[str]:
    tags = aspects.get("globalTags") or aspects.get("tags")
    if isinstance(tags, dict):
        tags = tags.get("tags", [])
    return _str_list(tags)


def _glossary_from(aspects: dict[str, Any]) -> list[str]:
    gl = aspects.get("glossaryTerms")
    if isinstance(gl, dict):
        gl = gl.get("terms", [])
    return _str_list(gl)


def _description_present(aspects: dict[str, Any]) -> bool:
    for key in ("datasetProperties", "documentation", "editableDatasetProperties"):
        blk = aspects.get(key)
        if isinstance(blk, dict) and str(blk.get("description", "")).strip():
            return True
    return bool(str(aspects.get("description", "")).strip())


def _upstreams_from(aspects: dict[str, Any]) -> list[str]:
    """Declared upstream tails — URNs kept intact until _URN_RE extracts
    the qualified name (``_str_list``'s colon-splitting would mangle
    them)."""
    up = aspects.get("upstreamLineage") or aspects.get("upstream")
    rows: list[Any] = []
    if isinstance(up, dict):
        rows = up.get("upstreams") or up.get("upstreamTables") or []
    elif isinstance(up, list):
        rows = up
    ups: list[str] = []
    for r in rows:
        raw = ""
        if isinstance(r, str):
            raw = r
        elif isinstance(r, dict):
            raw = str(r.get("dataset") or r.get("urn") or r.get("entityUrn") or r.get("name") or "")
        if not raw:
            continue
        m = _URN_RE.search(raw)
        ups.append(_tail(m.group(2) if m else raw))
    return [u for u in ups if u]


def _dataset_rows(doc: Any) -> list[dict[str, Any]]:
    """Accept both a list of entities and a single entity dict."""
    if isinstance(doc, list):
        return [d for d in doc if isinstance(d, dict)]
    if isinstance(doc, dict):
        for key in ("entities", "datasets", "results", "data"):
            if isinstance(doc.get(key), list):
                return [d for d in doc[key] if isinstance(d, dict)]
        return [doc]
    return []


# ---------------------------------------------------------------------------
# DataHub


def _is_datahub_doc(doc: Any) -> bool:
    if isinstance(doc, list):
        return any(
            isinstance(d, dict) and ("entityUrn" in d or "urn" in d or "aspects" in d)
            for d in doc[:5]
        )
    if isinstance(doc, dict):
        if "entityUrn" in doc or "urn" in doc or "aspects" in doc:
            return True
        return any(isinstance(doc.get(k), list) for k in ("entities", "datasets", "results"))
    return False


def _scan_datahub(ctx: ProjectContext, model: MetadataEstateModel) -> None:
    for rel in sorted(ctx.files):
        name_hint = rel.name.endswith(".datahub.json") or ".datahub." in rel.name
        if rel.suffix.lower() != ".json" and not name_hint:
            continue
        if not name_hint and "datahub" not in rel.as_posix().lower():
            continue
        text = ctx.read_text(rel)
        if text is None:
            continue
        try:
            doc = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            model.unparsed.append(rel.as_posix())
            continue
        if not _is_datahub_doc(doc):
            if name_hint:
                model.unparsed.append(rel.as_posix())
            continue
        for row in _dataset_rows(doc):
            urn = str(row.get("entityUrn") or row.get("urn") or "")
            if not urn:
                continue
            aspects = _flatten_aspects(row.get("aspects", row))
            m = _URN_RE.search(urn)
            if m:
                platform, qualified, env = m.group(1), m.group(2), m.group(3)
            else:
                platform, qualified, env = "", urn, ""
            model.datasets.append(
                CatalogDataset(
                    vendor="datahub",
                    urn=urn,
                    name=_tail(qualified),
                    qualified=qualified,
                    platform=platform,
                    environment=env.upper(),
                    file=rel,
                    owners=tuple(_owners_from(aspects)),
                    tags=tuple(_tags_from(aspects)),
                    glossary_terms=tuple(_glossary_from(aspects)),
                    has_description=_description_present(aspects),
                    upstreams=tuple(_upstreams_from(aspects)),
                )
            )


# ---------------------------------------------------------------------------
# OpenMetadata


def _scan_openmetadata(ctx: ProjectContext, model: MetadataEstateModel) -> None:
    for rel in sorted(ctx.files):
        name_hint = rel.name.endswith(".ometa.json") or ".ometa." in rel.name
        if rel.suffix.lower() != ".json" and not name_hint:
            continue
        if (
            not name_hint
            and "openmetadata" not in rel.as_posix().lower()
            and "ometa" not in rel.as_posix().lower()
        ):
            continue
        text = ctx.read_text(rel)
        if text is None:
            continue
        try:
            doc = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            model.unparsed.append(rel.as_posix())
            continue
        rows = _dataset_rows(doc)
        claimed = False
        for row in rows:
            fqn = str(row.get("fullyQualifiedName") or row.get("name") or "")
            if not fqn or not any(
                k in row
                for k in (
                    "entityType",
                    "owners",
                    "owner",
                    "tags",
                    "service",
                    "databaseSchema",
                    "tableType",
                    "upstream",
                )
            ):
                continue
            upstream = row.get("upstream") or row.get("upstreamTables") or []
            owners_raw = row.get("owners") or row.get("owner") or []
            if isinstance(owners_raw, dict):
                owners_raw = [owners_raw]
            model.datasets.append(
                CatalogDataset(
                    vendor="openmetadata",
                    urn=fqn,
                    name=_tail(fqn),
                    qualified=fqn,
                    file=rel,
                    owners=tuple(_str_list(owners_raw)),
                    tags=tuple(_str_list(row.get("tags") or [])),
                    has_description=bool(str(row.get("description", "")).strip()),
                    upstreams=tuple(_tail(u) for u in _str_list(upstream)),
                )
            )
            claimed = True
        if not claimed:
            model.unparsed.append(rel.as_posix())


# ---------------------------------------------------------------------------
# Glue catalog + Unity catalog observed exports (minimal: presence only)


def _scan_glue_unity(ctx: ProjectContext, model: MetadataEstateModel) -> None:
    glue_dir = re.compile(r"(?:^|[/\\])glue(?:[-_]catalog)?(?:$|[/\\])", re.I)
    unity_dir = re.compile(r"(?:^|[/\\])unity(?:[-_]catalog)?(?:$|[/\\])|unity", re.I)
    for rel in sorted(ctx.files):
        if rel.suffix.lower() != ".json":
            continue
        posix = rel.as_posix()
        in_glue = bool(glue_dir.search(posix))
        in_unity = bool(unity_dir.search(posix))
        if not in_glue and not in_unity:
            continue
        text = ctx.read_text(rel)
        if text is None:
            continue
        try:
            doc = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            model.unparsed.append(posix)
            continue
        claimed = False
        if in_glue:
            rows: list[dict[str, Any]] = []
            if isinstance(doc, dict):
                for key in ("TableList", "DatabaseList"):
                    rows.extend(r for r in doc.get(key, []) if isinstance(r, dict))
            elif isinstance(doc, list):
                rows = [r for r in doc if isinstance(r, dict)]
            for r in rows:
                name = str(r.get("Name") or r.get("tableName") or r.get("name") or "")
                db = str(r.get("DatabaseName") or r.get("databaseName") or "")
                if not name:
                    continue
                qualified = f"{db}.{name}" if db else name
                model.datasets.append(
                    CatalogDataset(
                        vendor="glue",
                        urn=qualified,
                        name=_tail(qualified),
                        qualified=qualified,
                        file=rel,
                        owners=(),
                        tags=(),
                        has_description=bool(str(r.get("Description", "")).strip()),
                    )
                )
                claimed = True
        elif in_unity:
            rows = [r for r in (doc if isinstance(doc, list) else [doc]) if isinstance(r, dict)]
            for r in rows:
                keys = {str(k).lower() for k in r}
                if not {"catalog_name", "schema_name", "table_name", "metastore_id"} & keys:
                    continue
                name = str(r.get("table_name") or r.get("name") or r.get("schema_name") or "")
                if not name:
                    continue
                cat = str(r.get("catalog_name", ""))
                sch = str(r.get("schema_name", ""))
                qualified = ".".join(p for p in (cat, sch, name) if p)
                model.datasets.append(
                    CatalogDataset(
                        vendor="unity",
                        urn=qualified,
                        name=_tail(qualified),
                        qualified=qualified,
                        file=rel,
                        has_description=bool(
                            str(r.get("comment", r.get("description", ""))).strip()
                        ),
                    )
                )
                claimed = True
        if not claimed:
            model.unparsed.append(posix)


# ---------------------------------------------------------------------------
# Ingestion recipes — connector types only, secrets never parsed


def _scan_recipes(ctx: ProjectContext, model: MetadataEstateModel) -> None:
    recipe_hint = re.compile(r"(?:^|[/\\])(?:recipes|ingestion)(?:$|[/\\])|recipe", re.I)
    for rel in sorted(ctx.files):
        if rel.suffix.lower() not in (".json", ".yml", ".yaml"):
            continue
        if not recipe_hint.search(rel.as_posix()):
            continue
        text = ctx.read_text(rel)
        if text is None or "source" not in text:
            continue
        if rel.suffix.lower() == ".json":
            try:
                doc = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                continue
        else:
            try:
                from forge_doctor_data.core.contract import _load_yaml

                doc, _err = _load_yaml(text)
            except Exception:
                continue
        if not isinstance(doc, dict) or "source" not in doc:
            continue
        src = doc.get("source")
        src_type = ""
        if isinstance(src, dict):
            src_type = str(src.get("type") or src.get("serviceName", ""))
        sink = doc.get("sink")
        sink_type = str(sink.get("type")) if isinstance(sink, dict) else ""
        model.recipes.append(
            IngestionRecipe(
                vendor="datahub" if "datahub" in text.lower() else "metadata",
                file=rel,
                source_type=src_type or "-",
                sink_type=sink_type,
            )
        )


# ---------------------------------------------------------------------------
# Model entry point


def metadata_model(ctx: ProjectContext) -> MetadataEstateModel:
    """Memoized declared-catalog estate model over ctx evidence."""
    cached = getattr(ctx, _CACHE_ATTR, None)
    if cached is not None:
        return cast(MetadataEstateModel, cached)
    model = MetadataEstateModel()
    _scan_datahub(ctx, model)
    _scan_openmetadata(ctx, model)
    _scan_glue_unity(ctx, model)
    _scan_recipes(ctx, model)
    setattr(ctx, _CACHE_ATTR, model)
    return model
