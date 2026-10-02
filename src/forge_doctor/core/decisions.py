"""Decision intelligence: one ranked, fully-cited action list.

Merges existing signals — finding severity/confidence, root-cause
clusters, remediation plans, fix safety classes, policy violations, and
graph blast radius — into deterministic per-check advice. Automate
ranking, not decisions: every recommendation cites fingerprints and
entity ids, and the score is the literal sum of its breakdown terms.

Scoring (documented constants, pure function):

- severity: ``error`` +100, ``warning`` +40 (info/pass rows are not
  advice — they are not problems);
- confidence: ``high`` +30, ``medium`` +15, ``low`` +5;
- cluster: +10 for cluster membership plus ``min(cluster_size, 5) * 5``;
  +30 more when the cluster is runtime-confirmed;
- fix safety: ``safe`` +20, ``review`` +10, ``manual`` +0;
- remediation plan exists: +15;
- policy violation (``POLICY*``): +40;
- blast radius: ``min(len(impacted), 10) * 3``.

Ties break on score, then smallest fingerprint — total order, no
nondeterminism.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from forge_doctor.core.models import CheckResult, Confidence, Severity

if TYPE_CHECKING:
    from forge_doctor.core.diagnosis import FindingCluster
    from forge_doctor.core.fixes import FixAction
    from forge_doctor.core.platform_graph import DataPlatformGraph
    from forge_doctor.core.remediation import RemediationPlan

# -- scoring constants (documented; no config surface per spec open q) --------

SEVERITY_SCORE = {Severity.ERROR: 100, Severity.WARNING: 40}
CONFIDENCE_SCORE = {Confidence.HIGH: 30, Confidence.MEDIUM: 15, Confidence.LOW: 5}
CLUSTER_MEMBER_SCORE = 10
CLUSTER_CONFIRMED_SCORE = 30
CLUSTER_SIZE_CAP = 5
CLUSTER_SIZE_SCORE = 5
FIX_SCORE = {"safe": 20, "review": 10, "manual": 0}
PLAN_SCORE = 15
POLICY_SCORE = 40
BLAST_CAP = 10
BLAST_SCORE = 3


@dataclass(frozen=True)
class Advice:
    """One ranked, cited action. ``score == sum(breakdown.values())``."""

    check_id: str
    title: str
    action: str
    score: int
    breakdown: dict[str, int]
    fingerprints: tuple[str, ...]
    entities: tuple[str, ...] = ()
    fix_class: str | None = None
    plan_id: str | None = None
    cluster_id: str | None = None
    unknowns: tuple[str, ...] = ()
    count: int = 1

    def to_dict(self) -> dict[str, object]:
        return {
            "check_id": self.check_id,
            "title": self.title,
            "action": self.action,
            "score": self.score,
            "breakdown": self.breakdown,
            "fingerprints": list(self.fingerprints),
            "entities": list(self.entities),
            "fix_class": self.fix_class,
            "plan_id": self.plan_id,
            "cluster_id": self.cluster_id,
            "unknowns": list(self.unknowns),
            "count": self.count,
        }


def _worst_severity(results: list[CheckResult]) -> Severity:
    order = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2, Severity.PASS: 3}
    return min((r.severity for r in results), key=lambda s: order.get(s, 3))


def advise(
    results: list[CheckResult],
    clusters: list[FindingCluster],
    plans: list[RemediationPlan],
    fixes: list[FixAction],
    graph: DataPlatformGraph,
) -> list[Advice]:
    """Rank findings into advice rows — one per ``check_id``.

    Inputs are the artifacts a scan already produces; this function adds
    no new evidence, only ordering.
    """
    # check_id -> cluster (related_findings are "check_id:fingerprint"
    # refs; first match wins deterministically)
    from forge_doctor.core.diagnosis import PromotionLevel
    from forge_doctor.core.semantic_diff import blast_radius

    cluster_of: dict[str, FindingCluster] = {}
    for cl in sorted(clusters, key=lambda c: c.id):
        for ref in sorted(cl.related_findings):
            cluster_of.setdefault(ref.split(":", 1)[0], cl)

    plans_by_check: dict[str, RemediationPlan] = {}
    for pl in sorted(plans, key=lambda p: p.id):
        plans_by_check.setdefault(pl.check_id, pl)

    fix_by_check: dict[str, FixAction] = {}
    for fx in fixes:
        fix_by_check.setdefault(fx.check_id, fx)

    grouped: dict[str, list[CheckResult]] = {}
    for r in results:
        if r.severity not in (Severity.ERROR, Severity.WARNING):
            continue
        grouped.setdefault(r.check_id, []).append(r)

    out: list[Advice] = []
    for check_id in sorted(grouped):
        group = sorted(grouped[check_id], key=lambda r: r.fingerprint or "")
        fingerprints = tuple(r.fingerprint or "" for r in group)
        breakdown: dict[str, int] = {"severity": SEVERITY_SCORE[_worst_severity(group)]}

        confs = [r.confidence for r in group if r.confidence is not None]
        if confs:
            breakdown["confidence"] = max(CONFIDENCE_SCORE.get(c, 0) for c in confs)

        cluster = cluster_of.get(check_id)
        entities: tuple[str, ...] = ()
        if cluster is not None:
            breakdown["cluster"] = (
                CLUSTER_MEMBER_SCORE
                + min(len(cluster.related_findings), CLUSTER_SIZE_CAP) * CLUSTER_SIZE_SCORE
            )
            if cluster.confidence is PromotionLevel.CONFIRMED:
                breakdown["cluster"] += CLUSTER_CONFIRMED_SCORE
            entities = tuple(sorted(cluster.affected_entities))

        fix = fix_by_check.get(check_id)
        if fix is not None:
            breakdown["fix"] = FIX_SCORE.get(fix.fix_class, 0)

        plan = plans_by_check.get(check_id)
        if plan is not None:
            breakdown["plan"] = PLAN_SCORE

        if check_id.startswith("POLICY"):
            breakdown["policy"] = POLICY_SCORE

        # Blast radius: union of downstream impact over the cluster's
        # affected entities (empty without a cluster — honest).
        impacted: set[str] = set()
        for eid in entities:
            impacted |= blast_radius(graph, eid) - {eid}
        if impacted:
            breakdown["blast_radius"] = min(len(impacted), BLAST_CAP) * BLAST_SCORE
            entities = tuple(sorted(set(entities) | impacted))

        unknowns: tuple[str, ...] = ()
        if cluster is None and not entities:
            unknowns = ("no entity attribution — findings unlinked to the graph",)

        if plan is not None:
            action = plan.problem
        elif fix is not None:
            action = fix.title
        else:
            action = group[0].title

        score = sum(breakdown.values())
        out.append(
            Advice(
                check_id=check_id,
                title=group[0].title,
                action=action,
                score=score,
                breakdown=breakdown,
                fingerprints=fingerprints,
                entities=entities,
                fix_class=fix.fix_class if fix is not None else None,
                plan_id=plan.id if plan is not None else None,
                cluster_id=cluster.id if cluster is not None else None,
                unknowns=unknowns,
                count=len(group),
            )
        )

    return sorted(out, key=lambda a: (-a.score, a.fingerprints[0], a.check_id))
