"""Physical design model + PHY findings (spec 236 §17-19).

Formalizes how each engine physically lays out data — partitioning,
clustering, ordering, distribution, sharding, indexing, replication,
caching — extracted only from declared config or exported evidence.
PHY findings compare the design to the workload where evidence exists;
they never guess a design that isn't declared.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PhysicalDesign:
    """One physical representation's layout, per engine."""

    engine: str
    subject: str  # table/index/cluster identifier
    partitioning: tuple[str, ...] = ()
    clustering: tuple[str, ...] = ()
    ordering: tuple[str, ...] = ()
    distribution: str = ""  # diststyle/distkey summary
    sharding: str = ""
    indexing: tuple[str, ...] = ()
    replication: str = ""
    caching: str = ""
    evidence: tuple[str, ...] = ()  # file:line / artifact / attr citations

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "subject": self.subject,
            "partitioning": list(self.partitioning),
            "clustering": list(self.clustering),
            "ordering": list(self.ordering),
            "distribution": self.distribution,
            "sharding": self.sharding,
            "indexing": list(self.indexing),
            "replication": self.replication,
            "caching": self.caching,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class PhysicalFinding:
    """PHY### finding — workload/design mismatch evidence."""

    check_id: str
    title: str
    severity: str  # warning | info
    message: str
    evidence: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "title": self.title,
            "severity": self.severity,
            "message": self.message,
            "evidence": list(self.evidence),
        }


# ---------------------------------------------------------------------------
# Extraction — declared config only (DDL/TF), each entry evidence-tagged
# ---------------------------------------------------------------------------


def extract_designs(graph: Any) -> list[PhysicalDesign]:
    """Pull PhysicalDesign rows from platform-graph entity attrs.

    Each design is anchored to a table/index entity whose attrs carry
    the engine's declared layout keys (partition_by, diststyle,
    cluster_by, order_by, shards/replicas). Entities without layout
    evidence produce no design — nothing is guessed.
    """
    designs: list[PhysicalDesign] = []
    if graph is None:
        return designs
    for e in graph.entities():
        engine = _design_engine(e)
        if engine is None:
            continue
        part = _attrs(e, ("partition_by", "partitioning", "partition", "partition_key"))
        clus = _attrs(e, ("cluster_by", "clustering", "cluster_keys"))
        order = _attrs(e, ("order_by", "sortkey", "sort_key", "ordering"))
        dist = e.attr("diststyle") or e.attr("dist_style")
        distkey = e.attr("distkey") or e.attr("dist_key")
        shards = e.attr("shards") or e.attr("number_of_shards")
        replicas = e.attr("replicas") or e.attr("number_of_replicas")
        index = _attrs(e, ("index", "indexes", "primary_key"))
        repl = e.attr("replication") or ""
        cache = e.attr("cache") or e.attr("caching") or ""
        if not any((part, clus, order, dist, distkey, shards, replicas, index, repl, cache)):
            continue
        ev: list[str] = [f"entity:{e.id}"]
        if e.file is not None:
            ev.append(f"file:{e.file.as_posix()}" + (f":{e.line}" if e.line else ""))
        designs.append(
            PhysicalDesign(
                engine=engine,
                subject=e.name or e.identifier,
                partitioning=tuple(sorted(part)),
                clustering=tuple(sorted(clus)),
                ordering=tuple(sorted(order)),
                distribution=f"diststyle={dist} distkey={distkey}".strip(),
                sharding=f"shards={shards}" if shards else "",
                indexing=tuple(sorted(index)),
                replication=f"replicas={replicas}" if replicas else repl,
                caching=cache,
                evidence=tuple(ev),
            )
        )
    return sorted(designs, key=lambda d: (d.engine, d.subject))


def _attrs(e: Any, keys: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for k in keys:
        v = e.attr(k)
        if v:
            out.append(v)
    return out


_DESIGN_ENGINES = {
    "redshift": "redshift",
    "bigquery": "bigquery",
    "snowflake": "snowflake",
    "clickhouse": "clickhouse",
    "opensearch": "opensearch",
    "elasticsearch": "opensearch",
}


def _design_engine(e: Any) -> str | None:
    """Table/index-ish entities on engines with physical-design surface."""
    kind = getattr(e.kind, "value", "")
    if kind not in ("table", "view", "warehouse", "catalog"):
        return None
    dom = (e.domain or "").lower()
    for needle, engine in _DESIGN_ENGINES.items():
        if needle in dom or needle in e.id.lower():
            return engine
    return None


# ---------------------------------------------------------------------------
# PHY findings — design vs workload evidence
# ---------------------------------------------------------------------------


def physical_findings(
    designs: list[PhysicalDesign],
    signals: list[Any] | None = None,
) -> list[PhysicalFinding]:
    """PHY001-005 over extracted designs (+ optional perf signals).

    PHY002/003/004 need runtime/workload evidence — without signals they
    stay quiet rather than speculate.
    """
    out: list[PhysicalFinding] = []
    # PHY001: design exists but declares no data-organization keys —
    # only secondary features (replication/caching/indexing/sharding).
    for d in designs:
        if not any((d.partitioning, d.clustering, d.ordering, d.distribution)):
            out.append(
                PhysicalFinding(
                    check_id="PHY001",
                    title="No data-organization keys declared",
                    severity="info",
                    message=(
                        f"{d.subject} ({d.engine}): physical design declares "
                        "no partitioning, clustering, ordering or distribution"
                    ),
                    evidence=d.evidence,
                )
            )
    # PHY005: same subject materialized in many physical designs
    by_subject: dict[str, list[PhysicalDesign]] = {}
    for d in designs:
        by_subject.setdefault(d.subject, []).append(d)
    for subject, group in sorted(by_subject.items()):
        if len(group) >= 4:
            out.append(
                PhysicalFinding(
                    check_id="PHY005",
                    title="Excessive physical representation count",
                    severity="warning",
                    message=(
                        f"{subject} materialized in {len(group)} physical "
                        f"designs ({', '.join(sorted({g.engine for g in group}))})"
                    ),
                    evidence=tuple(ev for g in group for ev in g.evidence)[:8],
                )
            )
    if signals:
        for sig in signals:
            fam = getattr(getattr(sig, "family", None), "value", "")
            # PHY002 — pruning evidence contradicts declared partitioning
            if fam == "poor_pruning":
                subject = getattr(sig, "subject", "")
                out.append(
                    PhysicalFinding(
                        check_id="PHY002",
                        title="Pruning ineffective",
                        severity="warning",
                        message=(
                            f"{subject}: declared layout does not prune — "
                            f"{getattr(sig, 'derived', '')}"
                        ),
                        evidence=tuple(getattr(sig, "evidence", ())),
                    )
                )
            # PHY004 — layout/compaction pressure from storage-shape signals
            if fam in (
                "small_file_amplification",
                "write_amplification",
                "materialization_overhead",
                "remote_io_amplification",
            ):
                out.append(
                    PhysicalFinding(
                        check_id="PHY004",
                        title="Storage layout under pressure",
                        severity="info",
                        message=(
                            f"{getattr(sig, 'subject', '')}: {fam} suggests "
                            f"layout/compaction review — {getattr(sig, 'derived', '')}"
                        ),
                        evidence=tuple(getattr(sig, "evidence", ())),
                    )
                )
            # PHY003 — distribution mismatch under join/exchange pressure
            if fam in ("data_exchange_amplification", "high_skew"):
                out.append(
                    PhysicalFinding(
                        check_id="PHY003",
                        title="Distribution mismatch",
                        severity="info",
                        message=(
                            f"{getattr(sig, 'subject', '')}: {fam} suggests "
                            f"distribution/layout review — {getattr(sig, 'derived', '')}"
                        ),
                        evidence=tuple(getattr(sig, "evidence", ())),
                    )
                )
    return sorted(set(out), key=lambda f: (f.check_id, f.message))
