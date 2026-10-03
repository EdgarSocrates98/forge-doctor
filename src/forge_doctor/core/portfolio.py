"""Platform portfolio intelligence (spec 247).

Cross-repo estate view over a merged ``WorkspaceModel`` graph: which
engines serve the same workload, which logical datasets live on
multiple platforms, which technologies are deprecated, where
cross-cloud dependencies concentrate, and how complexity accumulates.

Facts, not scores — there is no "platform health = 74". Every answer
is a deterministic list of evidence-backed rows. Duplication is an
OPPORTUNITY classification, never an error.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from forge_doctor.core.platform_graph import DataPlatformGraph, Entity, EntityKind, RelKind
from forge_doctor.core.workspace import WorkspaceModel

# ---------------------------------------------------------------------------
# Lifecycle — versioned-pack data via the capability registry
# ---------------------------------------------------------------------------

# domain prefix -> cloud family; anything else is "agnostic"
_CLOUD_PREFIXES = {
    "aws": "aws",
    "athena": "aws",
    "glue": "aws",
    "redshift": "aws",
    "s3": "aws",
    "lambda": "aws",
    "emr": "aws",
    "dynamodb": "aws",
    "lakeformation": "aws",
    "kinesis": "aws",
    "msk": "aws",
    "gcp": "gcp",
    "bigquery": "gcp",
    "gcs": "gcp",
    "dataflow": "gcp",
    "pubsub": "gcp",
    "composer": "gcp",
    "dataproc": "gcp",
    "azure": "azure",
    "adls": "azure",
    "synapse": "azure",
    "fabric": "azure",
    "databricks": "agnostic",  # multi-cloud by design — not a cloud edge
    "azure_event_hubs": "azure",
    "eventhub": "azure",
}

# kinds that represent runnable workloads
_WORKLOAD_KINDS = {
    EntityKind.WORKFLOW,
    EntityKind.TASK,
    EntityKind.COMPUTE_JOB,
    EntityKind.QUERY,
    EntityKind.DBT_MODEL,
}
# kinds that represent stored/logical data
_DATASET_KINDS = {
    EntityKind.TABLE,
    EntityKind.DATASET,
    EntityKind.VIEW,
    EntityKind.STREAM,
    EntityKind.STORAGE_LOCATION,
}
# orchestrator domains (workflow schedulers)
_ORCHESTRATOR_DOMAINS = {
    "airflow",
    "controlm",
    "databricks",
    "dbt",
    "adf",
    "composer",
    "stepfunctions",
}
# governance-plane domains (catalogs/contracts/lineage planes)
_GOVERNANCE_DOMAINS = {"datacontract", "metadata", "lakeformation", "unity_catalog"}


class LifecycleStatus(Enum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    EOL = "eol"
    UNKNOWN = "unknown"


def _cloud_of(domain: str) -> str:
    """Cloud family of a domain id; ``agnostic`` when not cloud-bound."""
    d = domain.lower()
    if d in _CLOUD_PREFIXES:
        return _CLOUD_PREFIXES[d]
    head = d.split("_", 1)[0]
    return _CLOUD_PREFIXES.get(head, "agnostic")


def _tail(identifier: str) -> str:
    """Logical name: last dotted component, normalized."""
    return identifier.rpartition(".")[2].strip().lower()


@dataclass(frozen=True)
class TechnologyInstance:
    """One platform domain as deployed, with lifecycle facts from
    capability packs (EOL/deprecation are pack facts, not inference)."""

    platform: str
    version: str  # "" = unversioned
    lifecycle_status: LifecycleStatus
    owner: str = ""
    workloads: tuple[str, ...] = ()
    criticality: str = ""  # highest declared criticality/sla marker, "" = none
    replacement: str = ""
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "version": self.version,
            "lifecycle_status": self.lifecycle_status.value,
            "owner": self.owner,
            "workloads": list(self.workloads),
            "criticality": self.criticality,
            "replacement": self.replacement,
            "evidence": list(self.evidence),
        }


class DuplicationKind(Enum):
    DATASET_ACROSS_PLATFORMS = "dataset_across_platforms"
    WORKLOAD_ACROSS_ORCHESTRATORS = "workload_across_orchestrators"
    CROSS_CLOUD_COPY = "cross_cloud_copy"


class DuplicationClass(Enum):
    OPPORTUNITY = "opportunity"  # the only class — duplication is never an error


@dataclass(frozen=True)
class PortfolioDuplicationSignal:
    kind: DuplicationKind
    subject: str  # logical name shared across locations
    locations: tuple[str, ...]  # entity ids, sorted
    classification: DuplicationClass = DuplicationClass.OPPORTUNITY
    note: str = ""
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "subject": self.subject,
            "locations": list(self.locations),
            "classification": self.classification.value,
            "note": self.note,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class PortfolioComplexitySignal:
    """Estate complexity facts — counts, no scoring."""

    engines: int
    orchestrators: int
    governance_planes: int
    copies: int  # duplicated logical datasets
    cross_cloud_edges: int
    owners: int
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "engines": self.engines,
            "orchestrators": self.orchestrators,
            "governance_planes": self.governance_planes,
            "copies": self.copies,
            "cross_cloud_edges": self.cross_cloud_edges,
            "owners": self.owners,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class RecurringPattern:
    """Org-learning fact: the same signal appears across N repos."""

    pattern: str  # e.g. check id / family value shared across repos
    count: int
    repos: tuple[str, ...]
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "count": self.count,
            "repos": list(self.repos),
            "note": self.note,
        }


@dataclass(frozen=True)
class PlatformPortfolio:
    """The estate as observed facts."""

    platforms: tuple[TechnologyInstance, ...] = ()
    workloads: tuple[str, ...] = ()  # entity ids of runnable units
    logical_datasets: tuple[str, ...] = ()  # normalized names
    physical_representations: dict[str, tuple[str, ...]] = field(default_factory=dict)
    teams: tuple[str, ...] = ()
    environments: tuple[str, ...] = ()
    cost_drivers: tuple[str, ...] = ()  # entity ids carrying cost evidence
    reliability_signals: tuple[str, ...] = ()  # entity ids carrying sla/reliability attrs
    lifecycle: dict[str, str] = field(default_factory=dict)  # platform -> status
    criticality: tuple[str, ...] = ()  # entity ids under an SLO/criticality marker
    duplications: tuple[PortfolioDuplicationSignal, ...] = ()
    complexity: PortfolioComplexitySignal | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "platforms": [p.to_dict() for p in self.platforms],
            "workloads": list(self.workloads),
            "logical_datasets": list(self.logical_datasets),
            "physical_representations": {
                k: list(v) for k, v in sorted(self.physical_representations.items())
            },
            "teams": list(self.teams),
            "environments": list(self.environments),
            "cost_drivers": list(self.cost_drivers),
            "reliability_signals": list(self.reliability_signals),
            "lifecycle": dict(sorted(self.lifecycle.items())),
            "criticality": list(self.criticality),
            "duplications": [d.to_dict() for d in self.duplications],
            "complexity": self.complexity.to_dict() if self.complexity else None,
        }


# ---------------------------------------------------------------------------
# Portfolio construction
# ---------------------------------------------------------------------------

_SLA_ATTRS = (
    "sla_availability",
    "sla_latency",
    "sla_freshness",
    "sla_throughput",
    "sla_error_rate",
    "rpo",
    "rto",
    "criticality",
    "tier",
)
_COST_ATTRS = ("retention_days", "storage_gb", "size_gb", "files_scanned", "partition_count")


def _lifecycle(platform: str, version: str) -> tuple[LifecycleStatus, str]:
    """(status, replacement) from pack lifecycle facts:

    1. capability-pack ``deprecated_in``/``removed_in``/``replacement``
    2. domain runtime packs (``knowledge/<platform>/runtimes.json``)
       with ``eol``/``deprecated`` version lists
    """
    from forge_doctor.core.capabilities import (
        _compare,
        capability_registry,
    )
    from forge_doctor.core.knowledge import load_pack

    registry = capability_registry()
    caps = registry.capabilities_for(platform)
    status = LifecycleStatus.UNKNOWN
    replacement = ""
    if not caps:
        # runtime-pack EOL evidence still applies without capability facts
        runtime_pack = load_pack(platform, "runtimes")
        eol = {str(v) for v in runtime_pack.get("eol", [])}
        depr = {str(v) for v in runtime_pack.get("deprecated", [])}
        if version and version in eol:
            return LifecycleStatus.EOL, replacement
        if version and version in depr:
            return LifecycleStatus.DEPRECATED, replacement
        return status, replacement
    worst: LifecycleStatus = LifecycleStatus.ACTIVE
    for cap in caps:
        facts = registry.dependencies(platform, cap)
        if version and facts.removed_in and _compare(version, facts.removed_in) >= 0:
            worst = LifecycleStatus.EOL
            replacement = facts.replacement or replacement
        elif (
            worst is not LifecycleStatus.EOL
            and version
            and facts.deprecated_in
            and _compare(version, facts.deprecated_in) >= 0
        ):
            worst = LifecycleStatus.DEPRECATED
            replacement = facts.replacement or replacement
        elif facts.replacement and not replacement:
            replacement = facts.replacement
    if worst is LifecycleStatus.ACTIVE and version:
        runtime_pack = load_pack(platform, "runtimes")
        if version in {str(v) for v in runtime_pack.get("eol", [])}:
            worst = LifecycleStatus.EOL
        elif version in {str(v) for v in runtime_pack.get("deprecated", [])}:
            worst = LifecycleStatus.DEPRECATED
    return worst, replacement


def build_portfolio(model: WorkspaceModel) -> PlatformPortfolio:
    """Portfolio facts over the merged fleet graph — all rows sorted."""
    g = model.graph
    entities = list(g.entities())

    workloads = sorted(e.id for e in entities if e.kind in _WORKLOAD_KINDS)
    datasets: dict[str, list[Entity]] = {}
    for e in entities:
        if e.kind in _DATASET_KINDS:
            datasets.setdefault(_tail(e.identifier), []).append(e)
    logical = sorted(datasets)
    representations = {
        name: tuple(sorted(e.id for e in ents)) for name, ents in sorted(datasets.items())
    }
    owners = sorted({e.attr("owner") for e in entities if e.attr("owner")})
    environments = sorted({e.attr("environment") or e.attr("env") for e in entities} - {""})
    cost_entities = sorted(e.id for e in entities if any(e.attr(k) for k in _COST_ATTRS))
    reliability_entities = sorted(e.id for e in entities if any(e.attr(k) for k in _SLA_ATTRS))

    # Technology instances: one per (domain, version) with workloads on it.
    by_platform: dict[tuple[str, str], list[Entity]] = {}
    for e in entities:
        version = next(
            (
                e.attr(k)
                for k in (
                    "version",
                    "runtime_version",
                    "engine_version",
                    "runtime",  # lambda/serverless runtime doubles as version
                )
                if e.attr(k)
            ),
            "",
        )
        by_platform.setdefault((e.domain, version), []).append(e)
    platforms: list[TechnologyInstance] = []
    lifecycle: dict[str, str] = {}
    for (domain, version), ents in sorted(by_platform.items()):
        status, replacement = _lifecycle(domain, version)
        workload_ids = sorted(e.id for e in ents if e.kind in _WORKLOAD_KINDS)
        crit = next((e.attr("criticality") for e in ents if e.attr("criticality")), "")
        owner = next((e.attr("owner") for e in ents if e.attr("owner")), "")
        platforms.append(
            TechnologyInstance(
                platform=domain,
                version=version,
                lifecycle_status=status,
                owner=owner,
                workloads=tuple(workload_ids),
                criticality=crit,
                replacement=replacement,
                evidence=tuple(sorted({e.id for e in ents[:10]})),
            )
        )
        key = f"{domain}@{version}" if version else domain
        lifecycle[key] = status.value

    duplications = duplication_signals(g)
    complexity = PortfolioComplexitySignal(
        engines=len({e.domain for e in entities} - _ORCHESTRATOR_DOMAINS - _GOVERNANCE_DOMAINS),
        orchestrators=len({e.domain for e in entities} & _ORCHESTRATOR_DOMAINS),
        governance_planes=len({e.domain for e in entities} & _GOVERNANCE_DOMAINS),
        copies=sum(1 for d in duplications if d.kind is DuplicationKind.DATASET_ACROSS_PLATFORMS),
        cross_cloud_edges=len(cross_cloud_edges(g)),
        owners=len(owners),
        evidence=(),
    )

    return PlatformPortfolio(
        platforms=tuple(platforms),
        workloads=tuple(workloads),
        logical_datasets=tuple(logical),
        physical_representations=representations,
        teams=tuple(owners),
        environments=tuple(environments),
        cost_drivers=tuple(cost_entities),
        reliability_signals=tuple(reliability_entities),
        lifecycle=lifecycle,
        criticality=tuple(reliability_entities),
        duplications=duplications,
        complexity=complexity,
    )


def cross_cloud_edges(g: DataPlatformGraph) -> list[tuple[str, str, str]]:
    """(src_cloud, dst_cloud, relationship id) where the two endpoints'
    domains map to different cloud families."""
    out: list[tuple[str, str, str]] = []
    for rel in g.relationships():
        src = g.entity(rel.src)
        dst = g.entity(rel.dst)
        if src is None or dst is None:
            continue
        c1, c2 = _cloud_of(src.domain), _cloud_of(dst.domain)
        if c1 != "agnostic" and c2 != "agnostic" and c1 != c2:
            out.append((c1, c2, f"{rel.src}->{rel.dst}"))
    return sorted(set(out))


def duplication_signals(g: DataPlatformGraph) -> tuple[PortfolioDuplicationSignal, ...]:
    """Same logical dataset on >=2 platforms; same workload under >=2
    orchestrators; same dataset name across clouds — OPPORTUNITY class."""
    out: list[PortfolioDuplicationSignal] = []

    datasets: dict[str, list[Entity]] = {}
    workloads: dict[str, list[Entity]] = {}
    for e in g.entities():
        if e.kind in _DATASET_KINDS:
            datasets.setdefault(_tail(e.identifier), []).append(e)
        elif e.kind in _WORKLOAD_KINDS:
            workloads.setdefault(_tail(e.identifier), []).append(e)

    for name, ents in sorted(datasets.items()):
        domains = {e.domain for e in ents}
        if len(domains) < 2 or not name:
            continue
        clouds = {_cloud_of(d) for d in domains} - {"agnostic"}
        kind = (
            DuplicationKind.CROSS_CLOUD_COPY
            if len(clouds) >= 2
            else DuplicationKind.DATASET_ACROSS_PLATFORMS
        )
        out.append(
            PortfolioDuplicationSignal(
                kind=kind,
                subject=name,
                locations=tuple(sorted(e.id for e in ents)),
                note=(
                    f"logical dataset '{name}' materialized on "
                    f"{len(domains)} platforms"
                    + (f" across clouds {sorted(clouds)}" if len(clouds) >= 2 else "")
                ),
                evidence=tuple(sorted(domains)),
            )
        )
    for name, ents in sorted(workloads.items()):
        domains = {e.domain for e in ents}
        orchestrated = domains & _ORCHESTRATOR_DOMAINS
        if len(orchestrated) < 2 or not name:
            continue
        out.append(
            PortfolioDuplicationSignal(
                kind=DuplicationKind.WORKLOAD_ACROSS_ORCHESTRATORS,
                subject=name,
                locations=tuple(sorted(e.id for e in ents)),
                note=(f"workload '{name}' defined under orchestrators {sorted(orchestrated)}"),
                evidence=tuple(sorted(orchestrated)),
            )
        )
    return tuple(out)


# ---------------------------------------------------------------------------
# §7.2 answers — deterministic fact lists
# ---------------------------------------------------------------------------


def answer_engines_per_workload(p: PlatformPortfolio) -> dict[str, list[str]]:
    """Workload name -> platforms serving it (only when >1)."""
    by_name: dict[str, set[str]] = {}
    for t in p.platforms:
        for w in t.workloads:
            # entity id is ``kind:domain:identifier`` — the workload name is
            # the identifier segment
            name = w.split(":", 2)[-1].lower() if ":" in w else w.lower()
            by_name.setdefault(name, set()).add(t.platform)
    return {k: sorted(v) for k, v in sorted(by_name.items()) if len(v) > 1}


def answer_datasets_across_platforms(p: PlatformPortfolio) -> dict[str, list[str]]:
    """Logical dataset -> platforms (only when >1)."""
    out: dict[str, list[str]] = {}
    for d in p.duplications:
        if d.kind is DuplicationKind.DATASET_ACROSS_PLATFORMS:
            out[d.subject] = sorted(d.evidence)
    return dict(sorted(out.items()))


def answer_critical_platforms(p: PlatformPortfolio) -> list[str]:
    """Platforms carrying entities under an SLO/criticality marker."""
    return sorted({pid.split(":", 2)[1] for pid in p.criticality if ":" in pid})


def answer_deprecated(p: PlatformPortfolio) -> list[TechnologyInstance]:
    return [
        t
        for t in p.platforms
        if t.lifecycle_status in (LifecycleStatus.DEPRECATED, LifecycleStatus.EOL)
    ]


def answer_cross_cloud_concentration(
    g: DataPlatformGraph,
) -> list[tuple[str, str, int]]:
    """(src_cloud, dst_cloud, edge_count) sorted by count desc then name."""
    counts: dict[tuple[str, str], int] = {}
    for c1, c2, _rel in cross_cloud_edges(g):
        counts[(c1, c2)] = counts.get((c1, c2), 0) + 1
    return sorted(
        ((a, b, n) for (a, b), n in counts.items()),
        key=lambda t: (-t[2], t[0], t[1]),
    )


def answer_team_cross_platform_deps(
    g: DataPlatformGraph,
) -> dict[str, int]:
    """Owner -> count of entities on cross-domain dependency edges."""
    counts: dict[str, int] = {}
    for rel in g.relationships():
        if rel.kind is not RelKind.DEPENDS_ON:
            continue
        src, dst = g.entity(rel.src), g.entity(rel.dst)
        if src is None or dst is None or src.domain == dst.domain:
            continue
        for e in (src, dst):
            owner = e.attr("owner")
            if owner:
                counts[owner] = counts.get(owner, 0) + 1
    return dict(sorted(counts.items()))


def recurring_patterns(
    per_repo_findings: dict[str, list[str]],
) -> tuple[RecurringPattern, ...]:
    """Same finding id across >=2 repos -> org-level learning signal."""
    by_check: dict[str, set[str]] = {}
    for repo, ids in per_repo_findings.items():
        for cid in ids:
            by_check.setdefault(cid, set()).add(repo)
    return tuple(
        RecurringPattern(
            pattern=cid,
            count=len(repos),
            repos=tuple(sorted(repos)),
            note="same signal in multiple repos — candidate shared capability",
        )
        for cid, repos in sorted(by_check.items())
        if len(repos) >= 2
    )


# ---------------------------------------------------------------------------
# ValidatedOptimizationEvidence — project-local learning record (§24-26)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidatedOptimizationEvidence:
    """Closes OptimizationCandidate -> ExperimentPlan -> ExperimentResult
    -> Accepted/Rejected. Scope is always project-local: results never
    generalize globally."""

    candidate_family: str
    subject: str
    verdict: str  # supported | not_supported | inconclusive | constraint_violated
    accepted: bool
    recorded_at: str  # ISO timestamp
    project: str
    details: dict[str, Any] = field(default_factory=dict)
    scope: str = "project-local"

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_family": self.candidate_family,
            "subject": self.subject,
            "verdict": self.verdict,
            "accepted": self.accepted,
            "recorded_at": self.recorded_at,
            "project": self.project,
            "details": self.details,
            "scope": self.scope,
        }


_EVIDENCE_FILE = "optimization-evidence.jsonl"


def record_optimization_evidence(root: Path, evidence: ValidatedOptimizationEvidence) -> Path:
    """Append one record to ``.forge-doctor/optimization-evidence.jsonl``."""
    target = root / ".forge-doctor" / _EVIDENCE_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(evidence.to_dict(), sort_keys=True) + "\n")
    return target


def load_optimization_evidence(root: Path) -> list[ValidatedOptimizationEvidence]:
    """All locally-validated records for this project, oldest first."""
    target = root / ".forge-doctor" / _EVIDENCE_FILE
    if not target.is_file():
        return []
    out: list[ValidatedOptimizationEvidence] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        out.append(
            ValidatedOptimizationEvidence(
                candidate_family=str(raw.get("candidate_family", "")),
                subject=str(raw.get("subject", "")),
                verdict=str(raw.get("verdict", "")),
                accepted=bool(raw.get("accepted")),
                recorded_at=str(raw.get("recorded_at", "")),
                project=str(raw.get("project", "")),
                details=raw.get("details") if isinstance(raw.get("details"), dict) else {},
            )
        )
    return out
