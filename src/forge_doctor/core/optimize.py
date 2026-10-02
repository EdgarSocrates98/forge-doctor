"""Optimization intelligence — deterministic improvement candidates.

Reads the twin's inputs (scan findings + platform graph) and enumerates
the optimizations the platform is *eligible* for. Each candidate names
the optimization, the files/entities it applies to, the evidence that
qualifies it, a static cost-proxy estimate, a confidence derived from
the evidence planes present, and the exact ``lab experiment`` /
``what-if`` invocation that validates it.

Optimize ranks *opportunities*; ``advise`` ranks *problems*. They share
citation discipline but are intentionally disjoint.

Candidates are suggested, never applied — the engine collects no new
evidence, runs nothing, and mutates nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from forge_doctor.core.diagnosis import PromotionLevel, cluster_findings
from forge_doctor.core.models import EvidenceKind, ScanReport
from forge_doctor.core.platform_graph import DataPlatformGraph

# ---------------------------------------------------------------------------
# Candidate model


@dataclass(frozen=True)
class OptimizationCandidate:
    """One eligible optimization, deduplicated per (optimization, file)."""

    optimization: str
    """Optimization name — reuses the experiment-hypothesis / what-if
    vocabulary so the validation handoff is a real command."""
    reason: str
    """Why this candidate qualifies (eligibility evidence, human text)."""
    files: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    """Citations: check ids, fingerprints, pack ids, entity ids."""
    cost_proxy: int = 0
    """Static estimate — affected-file count scaled by blast radius.
    Labeled a proxy: never live cost, never runtime numbers."""
    confidence: str = "low"
    """low|medium|high — derived from the evidence planes present."""
    validate_command: str = ""
    """Exact CLI invocation that measures the candidate; never run."""

    def to_dict(self) -> dict[str, object]:
        return {
            "optimization": self.optimization,
            "reason": self.reason,
            "files": list(self.files),
            "entities": list(self.entities),
            "evidence": list(self.evidence),
            "cost_proxy": self.cost_proxy,
            "confidence": self.confidence,
            "validate_command": self.validate_command,
        }


# ---------------------------------------------------------------------------
# Finding -> hypothesis map (file-level transforms measured by `lab experiment`)

_FINDING_HYPOTHESES: dict[str, tuple[str, str]] = {
    # check_id -> (optimization name, hypothesis / validation target)
    "SPARK003": (
        "partition-data",
        "widen single-partition shuffle to a bounded fan-out",
    ),
    "STREAM002": (
        "add-checkpoint",
        "add durable checkpointLocation to the writeStream",
    ),
    "PLAT003": (
        "increase-trigger-interval",
        "raise the micro-batch interval so fewer, larger batches land",
    ),
}


def _confidence(kinds: set[EvidenceKind], confirmed: bool) -> str:
    """Evidence planes present -> confidence label (deterministic)."""
    if confirmed or EvidenceKind.RUNTIME in kinds:
        return "high"
    if kinds & {EvidenceKind.DERIVED, EvidenceKind.OBSERVED_METADATA}:
        return "medium"
    return "low"


def optimize(
    report: ScanReport, graph: DataPlatformGraph, root: Path
) -> list[OptimizationCandidate]:
    """Enumerate eligible optimizations from findings + graph evidence.

    Deterministic: same inputs -> same ordered candidates. Rows dedupe
    per (optimization, file); finding-based rows cite fingerprints.
    """
    results = report.results
    clusters = cluster_findings(results, [])

    # fingerprint ref -> cluster confidence (refs are "check_id:fingerprint")
    order = list(PromotionLevel)  # declared low -> high
    fp_promotion: dict[str, PromotionLevel] = {}
    for cl in clusters:
        for ref in cl.related_findings:
            prev = fp_promotion.get(ref, order[0])
            fp_promotion[ref] = order[max(order.index(prev), order.index(cl.confidence))]

    # file -> entity ids (graph attribution)
    file_entities: dict[str, list[str]] = {}
    for e in graph.entities():
        if e.file is not None:
            file_entities.setdefault(e.file.as_posix(), []).append(e.id)
    for ids in file_entities.values():
        ids.sort()

    candidates: list[OptimizationCandidate] = []

    # 1) finding-based candidates — one per (optimization, file)
    seen: set[tuple[str, str]] = set()
    for r in results:
        mapping = _FINDING_HYPOTHESES.get(r.check_id)
        if mapping is None:
            continue
        name, reason = mapping
        file = r.file.as_posix() if r.file is not None else ""
        key = (name, file)
        if key in seen:
            continue
        seen.add(key)
        kinds = {r.evidence_kind} if r.evidence_kind else set()
        ref = f"{r.check_id}:{r.fingerprint}"
        confirmed = fp_promotion.get(ref) == PromotionLevel.CONFIRMED
        entities = tuple(file_entities.get(file, ()))
        blast = len(entities)
        candidates.append(
            OptimizationCandidate(
                optimization=name,
                reason=f"{r.check_id} {r.title}: {reason}",
                files=(file,) if file else (),
                entities=entities,
                evidence=(
                    ref,
                    *(f"entity:{eid}" for eid in entities),
                ),
                cost_proxy=max(1, blast),
                confidence=_confidence(kinds, confirmed),
                validate_command=(
                    f"forge-doctor lab experiment {root.as_posix()} --hypothesis {name}"
                ),
            )
        )

    # 2) version-upgrade candidates — observed platform version older
    #    than the newest declared target in a compatibility pack.
    candidates.extend(_version_candidates(graph, root))

    rank = {"high": 0, "medium": 1, "low": 2}
    return sorted(
        candidates,
        key=lambda c: (
            rank.get(c.confidence, 3),
            -c.cost_proxy,
            c.optimization,
            c.files[0] if c.files else "",
        ),
    )


def _version_candidates(graph: DataPlatformGraph, root: Path) -> list[OptimizationCandidate]:
    """Platform-version upgrades the compatibility packs can evaluate.

    Only versions the pack declares a ``targets`` entry for produce a
    candidate — the validation command is real only if the pack knows
    the target. Deterministic: highest declared target wins.
    """
    from forge_doctor.core.change_intel import platform_versions
    from forge_doctor.core.knowledge import load_pack

    # platform domain -> (what-if change target, compatibility pack kind)
    upgradeable = {
        "glue": ("glue-version", "compatibility"),
    }
    versions = platform_versions(graph)
    out: list[OptimizationCandidate] = []
    for domain, (target, kind) in sorted(upgradeable.items()):
        observed = versions.get(domain)
        if observed is None:
            continue
        pack = load_pack(domain, kind)
        targets = sorted((pack.get("targets") or {}).keys())
        newer = [t for t in targets if t > observed]
        if not newer:
            continue
        to = newer[-1]
        entities = sorted(e.id for e in graph.entities() if e.domain == domain)
        out.append(
            OptimizationCandidate(
                optimization=f"{domain}-version-upgrade",
                reason=(
                    f"{domain} {observed} observed; pack declares {domain} {to} as a known target"
                ),
                entities=tuple(entities),
                evidence=(
                    f"version:{domain}={observed}",
                    f"pack:{domain}/{kind}",
                    *(f"entity:{eid}" for eid in entities),
                ),
                cost_proxy=max(1, len(entities)),
                confidence=_confidence({EvidenceKind.OBSERVED_METADATA}, confirmed=False),
                validate_command=(f"forge-doctor what-if --change {target}={to} {root.as_posix()}"),
            )
        )
    return out
