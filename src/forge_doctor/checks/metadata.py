"""Metadata/catalog drift checks (META###) — declared vs actual.

The estate model carries the *declared* catalog (DataHub/OpenMetadata/
Glue/Unity exports); the platform graph carries the *detected* estate.
Drift findings name which side drives the finding — both directions are
findings, never auto-fixes. META002 is capped at 20 per-entity findings
and reports grouped counts after that (spec open question: coverage
gaps are informational).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge_doctor.analyzers.metadata_model import MetadataEstateModel, metadata_model
from forge_doctor.core.models import Confidence, EvidenceKind, Severity
from forge_doctor.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.models import CheckResult
    from forge_doctor.core.platform_graph import DataPlatformGraph

_META002_CAP = 20

# Entity kinds that count as "data assets" for catalog coverage.
_DATA_KIND_VALUES = {"table", "dataset", "view", "dbt_model", "stream"}


class _MetadataCheck(CheckBase):
    category = "metadata"


def _graph(ctx: ProjectContext) -> DataPlatformGraph:
    from forge_doctor.analyzers.platform_graph_builder import build_platform_graph

    return build_platform_graph(ctx)


def _entity_names(g: DataPlatformGraph) -> dict[str, str]:
    """tail-name -> entity id for *detected* data-bearing entities.

    Domain ``metadata`` entities are the declared side — they must never
    satisfy a drift check looking for detected platform evidence.
    """
    out: dict[str, str] = {}
    for e in g.entities():
        if e.kind.value not in _DATA_KIND_VALUES or e.domain == "metadata":
            continue
        tail = e.identifier.rstrip("/").rsplit("/", 1)[-1].rsplit(".", 1)[-1].lower()
        out.setdefault(tail, e.id)
        out.setdefault(e.identifier.lower(), e.id)
    return out


def _detected_upstreams(g: DataPlatformGraph, entity_id: str) -> set[str]:
    """Detected upstream tail-names for ``entity_id``.

    Two evidence shapes:
    - direct lineage: ``e READS_FROM src`` (dbt/catalog adapters).
    - query-mediated: ``q READS t`` + ``q WRITES e`` — the generic sql
      adapter keeps lineage on query nodes, so upstream tables are the
      read-set of every query writing ``e``.
    """
    from forge_doctor.core.platform_graph import RelKind

    def tail(eid: str) -> str:
        return eid.rsplit(":", 1)[-1].rsplit("/", 1)[-1].rsplit(".", 1)[-1].lower()

    ups: set[str] = set()
    rels = g.relationships()
    for rel in rels:
        if rel.kind == RelKind.READS_FROM and rel.src == entity_id:
            ups.add(tail(rel.dst))
    writers = {
        rel.src
        for rel in rels
        if rel.kind in (RelKind.WRITES, RelKind.WRITES_TO) and rel.dst == entity_id
    }
    for rel in rels:
        if rel.kind in (RelKind.READS, RelKind.READS_FROM) and rel.src in writers:
            ups.add(tail(rel.dst))
    return ups


def _catalog_names(model: MetadataEstateModel) -> set[str]:
    names = set()
    for d in model.datasets:
        names.add(d.name.lower())
        if d.qualified:
            names.add(d.qualified.lower())
    return names


class MetadataSurface(_MetadataCheck):
    """META000: anchor census of the declared metadata estate."""

    id = "META000"
    title = "Metadata estate surface"
    why = "Anchor: sizes the declared catalog feeding the META checks."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the counts to size the surface."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = metadata_model(ctx)
        if not model.has_evidence:
            return [
                self.result(Severity.PASS, "no metadata-catalog evidence detected")
            ]
        return [
            self.result(
                Severity.INFO,
                f"{len(model.datasets)} cataloged datasets "
                f"({', '.join(sorted(model.vendors()))}), "
                f"{len(model.recipes)} ingestion recipes",
            )
        ]


class StaleCatalogEntry(_MetadataCheck):
    """META001: cataloged dataset with no detected platform entity."""

    id = "META001"
    title = "Stale catalog entry (declared side)"
    why = (
        "A dataset exists in the catalog but no detected platform entity "
        "matches it — either the asset was dropped or the export is "
        "stale. Declared-side drift."
    )
    when_ok = "Every cataloged dataset tail-matches a detected entity."
    fix = "Remove the catalog entry or restore the missing dataset."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = metadata_model(ctx)
        if not model.datasets:
            return []
        detected = _entity_names(_graph(ctx))
        if not detected:
            return []  # nothing detected at all — nothing to drift against
        out: list[CheckResult] = []
        for d in model.datasets:
            if d.name.lower() in detected or (
                d.qualified and d.qualified.lower() in detected
            ):
                continue
            out.append(
                self.result(
                    Severity.WARNING,
                    f"{d.vendor} catalogs '{d.qualified or d.urn}' but no "
                    "detected entity matches — stale entry?",
                    file=d.file,
                    evidence=f"declared: {d.qualified or d.urn} ({d.vendor})",
                    evidence_kind=EvidenceKind.OBSERVED_METADATA,
                )
            )
        return out


class CatalogCoverageGap(_MetadataCheck):
    """META002: detected entity absent from the catalog (info, grouped)."""

    id = "META002"
    title = "Platform entity absent from catalog"
    why = (
        "Detected data assets missing from the catalog are invisible to "
        "governance — a coverage gap on the actual side."
    )
    when_ok = "Every detected data entity appears in the catalog."
    fix = "Run catalog ingestion covering the missing assets."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = metadata_model(ctx)
        if not model.datasets:
            return []  # no catalog → coverage is undefined, not a gap
        declared = _catalog_names(model)
        g = _graph(ctx)
        missing = [
            e
            for e in g.entities()
            if e.kind.value in _DATA_KIND_VALUES
            and e.domain != "metadata"
            and e.identifier.rsplit("/", 1)[-1].rsplit(".", 1)[-1].lower() not in declared
            and e.identifier.lower() not in declared
        ]
        if not missing:
            return []
        out = [
            self.result(
                Severity.INFO,
                f"detected entity '{e.id}' ({e.kind.value}) is not cataloged",
                file=e.file,
                evidence=f"actual: {e.id}",
            )
            for e in missing[:_META002_CAP]
        ]
        if len(missing) > _META002_CAP:
            out.append(
                self.result(
                    Severity.INFO,
                    f"...and {len(missing) - _META002_CAP} more uncataloged "
                    "entities (grouped — see --json for the full count)",
                )
            )
        return out


class DatasetNoOwner(_MetadataCheck):
    """META003: cataloged dataset without an owner."""

    id = "META003"
    title = "Cataloged dataset without owner"
    why = "Ownerless datasets have no accountable steward for quality or access."
    when_ok = "Every cataloged dataset names at least one owner."
    fix = "Assign an owner in the catalog source and re-ingest."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.WARNING,
                f"{d.vendor} dataset '{d.qualified or d.urn}' has no owner",
                file=d.file,
                evidence="owners empty",
                evidence_kind=EvidenceKind.OBSERVED_METADATA,
            )
            for d in metadata_model(ctx).datasets
            if d.vendor in ("datahub", "openmetadata") and not d.owners
        ]


class ProdDatasetUndocumented(_MetadataCheck):
    """META004: prod-flagged dataset without description/tags."""

    id = "META004"
    title = "Prod dataset undocumented"
    why = (
        "PROD assets without description and tags defeat discovery and "
        "classification for the exact consumers they serve."
    )
    when_ok = "Prod-flagged datasets carry a description or tags."
    fix = "Add a description and at least one tag/glossary term."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        out: list[CheckResult] = []
        for d in metadata_model(ctx).datasets:
            prod = d.environment == "PROD" or "prod" in d.qualified.lower().split(".")
            if not prod:
                continue
            if d.has_description or d.tags or d.glossary_terms:
                continue
            out.append(
                self.result(
                    Severity.WARNING,
                    f"{d.vendor} prod dataset '{d.qualified or d.urn}' has no "
                    "description and no tags/glossary terms",
                    file=d.file,
                    evidence="prod asset undocumented",
                    evidence_kind=EvidenceKind.OBSERVED_METADATA,
                )
            )
        return out


class LineageContradiction(_MetadataCheck):
    """META005: catalog lineage disagrees with detected graph lineage."""

    id = "META005"
    title = "Declared lineage contradicts detected lineage"
    why = (
        "The catalog declares upstreams the detected graph doesn't show "
        "(or vice versa) — one side's lineage is wrong or stale."
    )
    when_ok = "Declared upstreams are a subset of detected upstreams."
    fix = "Re-ingest lineage, or fix the declared upstream list."

    confidence = Confidence.MEDIUM  # name-tail matching across two worlds

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = metadata_model(ctx)
        declared = [d for d in model.datasets if d.upstreams]
        if not declared:
            return []
        g = _graph(ctx)
        names = _entity_names(g)
        out: list[CheckResult] = []
        for d in declared:
            eid = names.get(d.name.lower()) or (
                names.get(d.qualified.lower()) if d.qualified else None
            )
            if eid is None:
                continue  # META001 already reports the stale entry
            detected_ups = _detected_upstreams(g, eid)
            if not detected_ups:
                continue  # nothing detected — can't contradict
            missing = [
                u
                for u in d.upstreams
                if u.lower() not in detected_ups
            ]
            if missing:
                out.append(
                    self.result(
                        Severity.WARNING,
                        f"{d.vendor} declares '{d.qualified or d.urn}' upstreams "
                        f"{missing} absent from detected lineage (detected: "
                        f"{sorted(detected_ups)})",
                        file=d.file,
                        evidence=(
                            f"declared {sorted(d.upstreams)} "
                            f"vs detected {sorted(detected_ups)}"
                        ),
                        evidence_kind=EvidenceKind.OBSERVED_METADATA,
                    )
                )
        return out


CHECKS: tuple[Check, ...] = (
    MetadataSurface(),
    StaleCatalogEntry(),
    CatalogCoverageGap(),
    DatasetNoOwner(),
    ProdDatasetUndocumented(),
    LineageContradiction(),
)
