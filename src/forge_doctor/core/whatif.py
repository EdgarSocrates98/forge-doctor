"""Deterministic what-if evaluation.

Simulates a ``target.property = to`` change against the platform graph,
the capability engine, the architecture contract, and knowledge packs —
without executing anything. Every impact carries its evidence basis;
missing facts produce ``unknown`` entries, never invented certainty.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from forge_doctor.core.capabilities import (
    CapabilityStatus,
    capability_registry,
)

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext


# Canonical change targets -> (platform for capability queries, graph domain)
_TARGETS: dict[str, tuple[str, str, str]] = {
    "glue-version": ("glue", "glue", "glue_version"),
    "glue": ("glue", "glue", "glue_version"),
    "iceberg-format-version": ("iceberg", "iceberg", "format_version"),
    "iceberg": ("iceberg", "iceberg", "format_version"),
    "databricks-runtime": ("databricks", "databricks", "spark_version"),
    "dbr": ("databricks", "databricks", "spark_version"),
    "lambda-runtime": ("lambda", "lambda", "runtime"),
    "lambda": ("lambda", "lambda", "runtime"),
    "emr-release": ("emr", "emr", "release_label"),
    "emr": ("emr", "emr", "release_label"),
    # spec 224 — cross-platform migration on the abstraction layer
    "platform": ("platform", "platform", "platform"),
    "warehouse": ("platform", "platform", "platform"),
}


@dataclass(frozen=True)
class WhatIfChange:
    """One hypothetical property change on a target domain."""

    target: str  # canonical: glue|iceberg|databricks|lambda|emr
    property: str  # glue_version | format_version | spark_version | runtime | release_label
    from_: str  # observed current value ("" = not observed)
    to: str
    assumptions: tuple[str, ...] = ()


@dataclass(frozen=True)
class WhatIfImpact:
    """One evaluated consequence of the change."""

    category: str  # compatibility_risk|enabler|drift|prerequisite|contract_conflict
    severity: str  # blocker|warn|info
    detail: str
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class WhatIfReport:
    """Full deterministic evaluation of one change."""

    change: WhatIfChange
    affected_entities: tuple[str, ...] = ()
    impacts: tuple[WhatIfImpact, ...] = ()
    unsupported_now: tuple[str, ...] = ()  # capability ids lost at `to`
    supported_now: tuple[str, ...] = ()  # capability ids gained at `to`
    unknown: tuple[str, ...] = ()  # dimensions with insufficient facts

    @property
    def has_blockers(self) -> bool:
        return any(i.severity == "blocker" for i in self.impacts)


def parse_change(spec: str) -> WhatIfChange:
    """``glue-version=5.1`` / ``iceberg-format-version=2`` -> WhatIfChange."""
    key, _, value = spec.partition("=")
    key = key.strip().lower()
    value = value.strip()
    if not key or not value:
        raise ValueError(f"malformed --change {spec!r} (expected target=value)")
    if key not in _TARGETS:
        raise ValueError(f"unknown change target {key!r} (known: {', '.join(sorted(_TARGETS))})")
    platform, _, prop = _TARGETS[key]
    return WhatIfChange(target=platform, property=prop, from_="", to=value)


def _observed_versions(ctx: ProjectContext, target: str, prop: str) -> list[str]:
    """Current property values, best-effort from domain models/TF."""
    out: list[str] = []
    if target == "glue":
        from forge_doctor.analyzers.terraform_model import terraform_model

        for b in terraform_model(ctx).resources:
            if b.labels and b.labels[0] == "aws_glue_job":
                v = str(b.attrs.get("glue_version") or "")
                if v:
                    out.append(v)
    elif target == "iceberg":
        from forge_doctor.analyzers.iceberg_model import iceberg_model

        fv = iceberg_model(ctx).properties.get("format-version")
        if fv and fv[0]:
            out.append(str(fv[0]))
    elif target == "databricks":
        from forge_doctor.analyzers.databricks_model import databricks_model

        for c in databricks_model(ctx).clusters:
            if c.dbr_version:
                out.append(c.dbr_version)
    elif target == "lambda":
        from forge_doctor.analyzers.lambda_model import lambda_model

        for fn in lambda_model(ctx).functions:
            if fn.runtime:
                out.append(fn.runtime)
    elif target == "emr":
        from forge_doctor.analyzers.emr_model import emr_model

        for ec in emr_model(ctx).clusters:
            v = ec.release or ""
            if v:
                out.append(str(v))
    return sorted(set(out))


def _affected_entities(ctx: ProjectContext, change: WhatIfChange) -> list[str]:
    """Graph entities whose domain/property the change touches."""
    from forge_doctor.analyzers.platform_graph_builder import build_platform_graph

    g = build_platform_graph(ctx)
    domains = {
        "glue": {"glue"},
        "iceberg": {"iceberg", "glue", "emr", "athena", "databricks"},
        "databricks": {"databricks"},
        "lambda": {"lambda"},
        "emr": {"emr"},
    }.get(change.target, {change.target})
    hits: list[str] = []
    for e in g.entities():
        if e.domain in domains and e.kind.value in (
            "compute_job",
            "table",
            "catalog",
            "query",
            "stream",
            "dataset",
        ):
            hits.append(e.id)
    return sorted(set(hits))


def _capability_diff(
    ctx: ProjectContext, change: WhatIfChange
) -> tuple[list[WhatIfImpact], list[str], list[str], list[str]]:
    """Re-evaluate every capability of the platform at from → to."""
    reg = capability_registry()
    caps = reg.capabilities_for(change.target)
    if not caps:
        return [], [], [], [f"no capability facts for platform '{change.target}'"]

    impacts: list[WhatIfImpact] = []
    lost: list[str] = []
    gained: list[str] = []
    unknown: list[str] = []
    for cap in caps:
        before = reg.evaluate(
            cap,
            platform=change.target,
            version=change.from_ or None,
            attributes=ctx_attrs(change),
        )
        after = reg.evaluate(
            cap,
            platform=change.target,
            version=change.to or None,
            attributes=ctx_attrs(change),
        )
        if before.status == after.status:
            continue
        ev = (
            f"{change.from_ or '?'}={before.status.value}",
            f"{change.to}={after.status.value}",
        )
        if after.status == CapabilityStatus.UNSUPPORTED:
            lost.append(cap)
            impacts.append(
                WhatIfImpact(
                    "compatibility_risk",
                    "blocker",
                    f"{cap} becomes unsupported at {change.to}: {after.reason}",
                    ev,
                )
            )
        elif after.status == CapabilityStatus.SUPPORTED:
            gained.append(cap)
            impacts.append(
                WhatIfImpact(
                    "enabler",
                    "info",
                    f"{cap} becomes supported at {change.to}: {after.reason}",
                    ev,
                )
            )
        elif after.status == CapabilityStatus.CONDITIONAL:
            impacts.append(
                WhatIfImpact(
                    "compatibility_risk",
                    "warn",
                    f"{cap} is conditional at {change.to}: {after.reason} "
                    f"({'; '.join(after.conditions)})",
                    ev,
                )
            )
        else:
            unknown.append(f"{cap}: status unknown at {change.to}")
        for lim in after.limitations:
            impacts.append(WhatIfImpact("prerequisite", "warn", f"{cap}: {lim}", ()))
    return impacts, lost, gained, unknown


def ctx_attrs(change: WhatIfChange) -> tuple[tuple[str, str], ...]:
    """Context attributes implied by the change target (pack conditions)."""
    attrs: dict[str, str] = {}
    if change.property == "format_version":
        attrs["format_version"] = change.to
    return tuple(sorted(attrs.items()))


def _compat_notes(ctx: ProjectContext, change: WhatIfChange) -> list[WhatIfImpact]:
    """Version-change facts from domain compatibility packs."""
    from forge_doctor.core.knowledge import load_pack

    out: list[WhatIfImpact] = []
    if change.target == "glue":
        pack = load_pack("glue", "compatibility")
        target_entry = (pack.get("targets") or {}).get(change.to)
        if isinstance(target_entry, dict):
            for ch in target_entry.get("changes") or []:
                sev = str(ch.get("severity", "INFO")).lower()
                out.append(
                    WhatIfImpact(
                        "compatibility_risk",
                        {"high": "blocker", "medium": "warn"}.get(sev, "info"),
                        f"glue {change.to}: {ch.get('change')} — {ch.get('detail', '')}",
                        ("pack:glue/compatibility",),
                    )
                )
        # runtime bundling facts from the iceberg pack
        ice = load_pack("iceberg", "compatibility")
        rt = ((ice.get("runtimes") or {}).get("glue") or {}).get(change.to)
        if isinstance(rt, dict):
            out.append(
                WhatIfImpact(
                    "prerequisite",
                    "info",
                    f"glue {change.to} bundles spark {rt.get('spark')}, "
                    f"iceberg {rt.get('iceberg')}: {rt.get('note', '')}",
                    ("pack:iceberg/compatibility",),
                )
            )
    elif change.target == "databricks":
        pack = load_pack("databricks", "runtime")
        for rt in pack.get("runtimes") or []:
            if str(rt.get("dbr", "")).startswith(change.to.split(".")[0]):
                out.append(
                    WhatIfImpact(
                        "prerequisite",
                        "info",
                        f"DBR {rt.get('dbr')}: spark={rt.get('spark')} "
                        f"delta={rt.get('delta')} status={rt.get('status')} — "
                        f"{rt.get('note', '')}",
                        ("pack:databricks/runtime",),
                    )
                )
    elif change.target == "iceberg":
        pack = load_pack("iceberg", "versions")
        fv = (pack.get("format_versions") or {}).get(change.to)
        if isinstance(fv, dict):
            out.append(
                WhatIfImpact(
                    "prerequisite",
                    "info",
                    f"iceberg format-version {change.to}: {fv.get('notes', '')} "
                    f"(status={fv.get('status', '?')})",
                    ("pack:iceberg/versions",),
                )
            )
    elif change.target == "lambda":
        pack = load_pack("lambda", "runtimes")
        for rt in pack.get("runtimes") or []:
            if str(rt.get("runtime", "")) == change.to:
                out.append(
                    WhatIfImpact(
                        "prerequisite",
                        "info",
                        f"lambda {change.to}: status={rt.get('status', '?')} — "
                        f"{rt.get('note', '')}",
                        ("pack:lambda/runtimes",),
                    )
                )
    return out


def _contract_conflicts(ctx: ProjectContext, change: WhatIfChange) -> list[WhatIfImpact]:
    """Architecture-contract pins the change would violate."""
    contract = getattr(ctx, "contract", None)
    if contract is None or not getattr(contract, "ok", False):
        return []
    out: list[WhatIfImpact] = []
    for pipe in contract.pipelines:
        want = pipe.compute_version
        if want and pipe.compute_platform == change.target and want != change.to:
            out.append(
                WhatIfImpact(
                    "contract_conflict",
                    "warn",
                    f"pipeline '{pipe.name}' contract pins {pipe.compute_platform} "
                    f"{want}; change to {change.to} would drift (ARCH002)",
                    (f"contract:{pipe.name}",),
                )
            )
    return out


def _platform_change_report(ctx: ProjectContext, change: WhatIfChange) -> WhatIfReport:
    """`--change platform=<target>`: cross-platform on the abstraction layer."""
    from forge_doctor.analyzers.abstractions import abstractions_model
    from forge_doctor.core.crossmigration import plan_platform_migration
    from forge_doctor.core.models import Severity

    model = abstractions_model(ctx)
    wh_sources = sorted({s.service for s in model.services if s.abstraction == "warehouse"})
    source = (
        wh_sources[0]
        if len(wh_sources) == 1
        else (change.from_ or (wh_sources[0] if wh_sources else "unknown"))
    )
    plan = plan_platform_migration(ctx, source, change.to)
    resolved = WhatIfChange(
        target="platform",
        property="platform",
        from_=source,
        to=change.to,
        assumptions=change.assumptions,
    )
    impacts: list[WhatIfImpact] = []
    for f in plan.findings:
        sev = (
            "blocker"
            if f.severity == Severity.ERROR
            else ("warn" if f.severity == Severity.WARNING else "info")
        )
        impacts.append(WhatIfImpact("migration", sev, f.message, ((f.evidence or ""),)))
    for m in plan.entity_map:
        impacts.append(
            WhatIfImpact(
                "drift",
                "info",
                f"{m.source} -> {m.target_service or 'UNMAPPED'} [{m.confidence}]",
                (m.note,),
            )
        )
    sev_rank = {"blocker": 0, "warn": 1, "info": 2}
    impacts.sort(key=lambda i: (sev_rank.get(i.severity, 3), i.category, i.detail))
    return WhatIfReport(
        change=resolved,
        affected_entities=tuple(m.source for m in plan.entity_map),
        impacts=tuple(impacts),
        unsupported_now=tuple(plan.lost_capabilities()),
        supported_now=tuple(plan.gained_capabilities()),
        unknown=tuple(
            f"{d.capability}: {d.target_status}"
            for d in plan.capability_deltas
            if d.delta == "review"
        ),
    )


def evaluate_change(ctx: ProjectContext, change: WhatIfChange) -> WhatIfReport:
    """Full deterministic what-if evaluation for one change."""
    if change.target == "platform":
        return _platform_change_report(ctx, change)
    observed = _observed_versions(ctx, change.target, change.property)
    from_ = change.from_ or ",".join(observed) or "unobserved"
    resolved = WhatIfChange(
        target=change.target,
        property=change.property,
        from_=from_,
        to=change.to,
        assumptions=change.assumptions,
    )
    affected = _affected_entities(ctx, resolved)
    impacts, lost, gained, unknown = _capability_diff(ctx, resolved)
    impacts.extend(_compat_notes(ctx, resolved))
    impacts.extend(_contract_conflicts(ctx, resolved))
    if not _observed_versions(ctx, resolved.target, resolved.property):
        unknown.append(f"no current {resolved.property} observed — 'from' is unverified")
    sev_rank = {"blocker": 0, "warn": 1, "info": 2}
    impacts.sort(key=lambda i: (sev_rank.get(i.severity, 3), i.category, i.detail))
    return WhatIfReport(
        change=resolved,
        affected_entities=tuple(affected),
        impacts=tuple(impacts),
        unsupported_now=tuple(sorted(lost)),
        supported_now=tuple(sorted(gained)),
        unknown=tuple(sorted(unknown)),
    )
