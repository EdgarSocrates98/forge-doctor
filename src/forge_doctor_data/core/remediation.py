"""Deterministic remediation planning.

Maps findings and root-cause clusters to ordered remediation actions from
``knowledge/remediation/`` packs. Forge Doctor Data only ever describes *what*
to change, *where*, *why*, and *how to validate* — it never edits code,
applies patches, runs Terraform, or deploys anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from forge_doctor_data.core.diagnosis import FindingCluster
from forge_doctor_data.core.knowledge import list_packs
from forge_doctor_data.core.models import CheckResult


@dataclass(frozen=True)
class RemediationAction:
    """One ordered step of a remediation plan."""

    id: str
    description: str
    target_entity: str | None
    rationale: str
    expected_effect: str
    validation: str
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class RemediationPlan:
    """Ordered, deterministic plan for one problem class."""

    id: str
    problem: str
    check_id: str
    targets: tuple[str, ...]
    actions: tuple[RemediationAction, ...]
    prerequisites: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    validation_steps: tuple[str, ...] = ()
    rollback_notes: tuple[str, ...] = ()


def remediation_entries() -> list[dict[str, Any]]:
    """All remediation entries across ``knowledge/remediation/`` packs."""
    entries: list[dict[str, Any]] = []
    for domain, _name, pack in list_packs():
        if domain != "remediation":
            continue
        for entry in pack.get("remediations", []):
            if isinstance(entry, dict):
                entries.append(entry)
    return sorted(entries, key=lambda e: str(e.get("match", {})))


def _match_key(entry: dict[str, Any]) -> tuple[str, str]:
    match = entry.get("match", {})
    if not isinstance(match, dict):
        return ("", "")
    if "check_id" in match:
        return ("check", str(match["check_id"]))
    if "cluster" in match:
        return ("cluster", str(match["cluster"]))
    return ("", "")


def _actions(entry: dict[str, Any]) -> tuple[RemediationAction, ...]:
    actions = []
    for i, raw in enumerate(entry.get("actions", [])):
        if not isinstance(raw, dict):
            continue
        actions.append(
            RemediationAction(
                id=str(raw.get("id") or f"A{i + 1}"),
                description=str(raw.get("description", "")),
                target_entity=raw.get("target_entity"),
                rationale=str(raw.get("rationale", "")),
                expected_effect=str(raw.get("expected_effect", "")),
                validation=str(raw.get("validation", "")),
                depends_on=tuple(str(d) for d in raw.get("depends_on", [])),
            )
        )
    return tuple(actions)


def _plan(entry: dict[str, Any], key: str, targets: tuple[str, ...]) -> RemediationPlan:
    return RemediationPlan(
        id=f"PLAN-{key}",
        problem=str(entry.get("problem", "")),
        check_id=key,
        targets=targets,
        actions=_actions(entry),
        prerequisites=tuple(str(p) for p in entry.get("prerequisites", [])),
        dependencies=tuple(str(d) for d in entry.get("dependencies", [])),
        risks=tuple(str(r) for r in entry.get("risks", [])),
        validation_steps=tuple(str(v) for v in entry.get("validation_steps", [])),
        rollback_notes=tuple(str(r) for r in entry.get("rollback_notes", [])),
    )


def plan_remediation(
    results: list[CheckResult],
    clusters: list[FindingCluster] | None = None,
    *,
    root_cause: str | None = None,
) -> list[RemediationPlan]:
    """Build remediation plans for findings (and clusters) deterministically.

    ``root_cause`` filters to the plan(s) whose chain id matches (prefix
    match, since cluster ids carry a digest suffix). When ``root_cause``
    is set, findings-based plans are skipped.
    """
    entries = remediation_entries()
    clusters = clusters or []
    plans: list[RemediationPlan] = []

    # Cluster plans: entry match.cluster is the chain id prefix.
    for entry in entries:
        kind, key = _match_key(entry)
        if kind != "cluster":
            continue
        for cluster in clusters:
            if not cluster.id.startswith(key):
                continue
            if root_cause and not cluster.id.startswith(root_cause):
                continue
            targets = tuple(
                sorted(
                    {f.split(":", 1)[0] for f in cluster.related_findings}
                    | set(cluster.affected_entities)
                )
            )
            plans.append(_plan(entry, key, targets))

    if root_cause:
        return sorted(plans, key=lambda p: p.id)

    # Finding plans: one plan per check_id; targets = sorted finding files.
    by_check: dict[str, set[str]] = {}
    for r in results:
        by_check.setdefault(r.check_id, set()).add(r.file.as_posix() if r.file else "?")
    for entry in entries:
        kind, key = _match_key(entry)
        if kind == "check" and key in by_check:
            plans.append(_plan(entry, key, tuple(sorted(by_check[key]))))
    return sorted(plans, key=lambda p: p.id)


def plan_to_dict(plan: RemediationPlan) -> dict[str, Any]:
    """Stable JSON-ready serialization of one remediation plan."""
    return {
        "id": plan.id,
        "problem": plan.problem,
        "check_id": plan.check_id,
        "targets": list(plan.targets),
        "prerequisites": list(plan.prerequisites),
        "dependencies": list(plan.dependencies),
        "risks": list(plan.risks),
        "validation_steps": list(plan.validation_steps),
        "rollback_notes": list(plan.rollback_notes),
        "actions": [
            {
                "id": a.id,
                "description": a.description,
                "target_entity": a.target_entity,
                "rationale": a.rationale,
                "expected_effect": a.expected_effect,
                "validation": a.validation,
                "depends_on": list(a.depends_on),
            }
            for a in plan.actions
        ],
    }
