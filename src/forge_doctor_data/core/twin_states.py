"""Five-state digital twin — reconciliation and drift (spec 232).

The formal twin (``core/twin.py``) is one validated snapshot. This module
tags every fact with *which* state it came from and reconciles states
against each other:

- DESIRED — what the org asked for: ``platform-contract.*``, data
  contracts, org policy.
- DECLARED — what IaC/config declares: Terraform, bundle/config files.
- IMPLEMENTED — what code/models actually do (analyzer-derived).
- OBSERVED — what runtime/metadata evidence shows (committed artifacts
  only — never live cloud calls).
- HYPOTHETICAL — what a what-if/migration/experiment transform proposes;
  collected only from such transforms, never mutating the twin.

Join rule: facts reconcile on ``(entity, property)`` where ``entity`` is
a normalized comparison key. Contract pipelines are authored names —
joining ``contract.pipelines.X`` to an entity whose identifier is ``X``
is explicit evidence, not name-similarity inference.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from forge_doctor_data.core.models import EvidenceKind

if TYPE_CHECKING:
    from pathlib import Path

    from forge_doctor_data.core.contract import PlatformContract
    from forge_doctor_data.core.platform_graph import DataPlatformGraph
    from forge_doctor_data.core.whatif import WhatIfReport


class TwinState(Enum):
    DESIRED = "desired"
    DECLARED = "declared"
    IMPLEMENTED = "implemented"
    OBSERVED = "observed"
    HYPOTHETICAL = "hypothetical"


class DriftType(Enum):
    CONFIG_DRIFT = "config_drift"
    IMPLEMENTATION_DRIFT = "implementation_drift"
    RUNTIME_DRIFT = "runtime_drift"
    CAPABILITY_DRIFT = "capability_drift"
    OWNERSHIP_DRIFT = "ownership_drift"
    SCHEMA_DRIFT = "schema_drift"
    PERFORMANCE_DRIFT = "performance_drift"
    SECURITY_DRIFT = "security_drift"


_DRIFT_DEFS: dict[DriftType, str] = {
    DriftType.CONFIG_DRIFT: "Desired config diverges from declared IaC/config.",
    DriftType.IMPLEMENTATION_DRIFT: "Declared intent diverges from implemented code.",
    DriftType.RUNTIME_DRIFT: "Implemented behavior diverges from observed runtime.",
    DriftType.CAPABILITY_DRIFT: "Capability assumptions diverge across states.",
    DriftType.OWNERSHIP_DRIFT: "Declared ownership diverges across sources.",
    DriftType.SCHEMA_DRIFT: "Declared schema diverges from implemented/observed.",
    DriftType.PERFORMANCE_DRIFT: "Latency/throughput properties diverge.",
    DriftType.SECURITY_DRIFT: "Security posture diverges (encryption, access).",
}


@dataclass(frozen=True)
class TwinFact:
    """One state-tagged property claim about an entity."""

    entity: str  # normalized comparison key (see module docstring)
    property: str
    value: str
    state: TwinState
    evidence_kind: EvidenceKind | None = None
    source: str = ""  # file / tool that produced the claim
    confidence: str = "declared"  # declared | observed | inferred | hypothetical


@dataclass(frozen=True)
class TwinReconciliation:
    """One divergent (entity, property) across two states."""

    entity: str
    property: str
    expected: str
    actual: str
    states_compared: tuple[TwinState, TwinState]
    difference: str
    drift_type: DriftType
    confidence: str
    affected_entities: tuple[str, ...]


# ---------------------------------------------------------------------------
# Fact collection
# ---------------------------------------------------------------------------

# producer domain -> state. Explicit: terraform entities are declarations;
# contract/data-contract entities are desired intent; metadata/runtime
# entities are observed artifacts; everything else is implemented code.
_DOMAIN_STATE: dict[str, TwinState] = {
    "terraform": TwinState.DECLARED,
    "datacontract": TwinState.DESIRED,
    "metadata": TwinState.OBSERVED,
    "runtime": TwinState.OBSERVED,
}

# state -> default evidence plane for collected facts
_STATE_EVIDENCE: dict[TwinState, EvidenceKind] = {
    TwinState.DESIRED: EvidenceKind.CONFIG,
    TwinState.DECLARED: EvidenceKind.CONFIG,
    TwinState.IMPLEMENTED: EvidenceKind.STATIC,
    TwinState.OBSERVED: EvidenceKind.OBSERVED_METADATA,
    TwinState.HYPOTHETICAL: EvidenceKind.DERIVED,
}


def _entity_state(entity: object) -> TwinState:
    """Classify an entity into a twin state.

    Precedence: explicit ``source``/``producer`` attrs (``terraform`` =
    declared IaC) → file suffix (``.tf/.hcl`` = declared) → producer
    domain map → default IMPLEMENTED (analyzer-derived from code).
    """
    domain = getattr(entity, "domain", "")
    attrs = dict(getattr(entity, "attrs", ()))
    marker = str(attrs.get("source") or attrs.get("producer") or "")
    if marker == "terraform":
        return TwinState.DECLARED
    file = getattr(entity, "file", None)
    if file is not None and str(file).rsplit(".", 1)[-1].lower() in {"tf", "tfvars", "hcl"}:
        return TwinState.DECLARED
    return _DOMAIN_STATE.get(domain, TwinState.IMPLEMENTED)


_CONTRACT_PROPERTIES = (
    "platform",
    "version",
    "format",
    "orchestrator",
    "sla_seconds",
    "idempotent",
    "owner",
)


def collect_desired_facts(contract: PlatformContract) -> tuple[TwinFact, ...]:
    """DESIRED facts from a platform contract's pipeline/dataset claims."""
    facts: list[TwinFact] = []
    source = str(contract.path)
    for pipe in contract.pipelines:
        values = {
            "platform": pipe.compute_platform,
            "version": pipe.compute_version,
            "format": pipe.storage_format,
            "orchestrator": pipe.orchestrator,
            "sla_seconds": str(pipe.sla_seconds) if pipe.sla_seconds is not None else None,
            "idempotent": (str(pipe.idempotent).lower() if pipe.idempotent is not None else None),
            "owner": pipe.owner,
        }
        for prop in _CONTRACT_PROPERTIES:
            value = values[prop]
            if value is None:
                continue
            facts.append(
                TwinFact(
                    entity=pipe.name,
                    property=prop,
                    value=value,
                    state=TwinState.DESIRED,
                    evidence_kind=EvidenceKind.CONFIG,
                    source=source,
                    confidence="declared",
                )
            )
    for dataset in contract.datasets:
        facts.append(
            TwinFact(
                entity=dataset,
                property="exists",
                value="true",
                state=TwinState.DESIRED,
                evidence_kind=EvidenceKind.CONFIG,
                source=source,
                confidence="declared",
            )
        )
    return tuple(sorted(facts, key=lambda f: (f.entity, f.property)))


def collect_graph_facts(graph: DataPlatformGraph) -> tuple[TwinFact, ...]:
    """State-tagged facts from entity attributes.

    Entity attrs become facts whose state follows the producer domain.
    The join key is the entity's short ``identifier`` — the contract
    pipeline that names it is explicit evidence for the join.
    """
    facts: list[TwinFact] = []
    for e in graph.entities():
        state = _entity_state(e)
        for key, value in e.attrs:
            facts.append(
                TwinFact(
                    entity=e.identifier,
                    property=key,
                    value=str(value),
                    state=state,
                    evidence_kind=_STATE_EVIDENCE[state],
                    source=str(e.file) if e.file else e.domain,
                    confidence=("observed" if state is TwinState.OBSERVED else "inferred"),
                )
            )
    return tuple(sorted(facts, key=lambda f: (f.entity, f.property, f.state.value)))


def collect_twin_facts(
    graph: DataPlatformGraph, contract: PlatformContract | None = None
) -> tuple[TwinFact, ...]:
    """All collected facts (DESIRED contract + state-tagged graph attrs)."""
    facts = list(collect_graph_facts(graph))
    if contract is not None:
        facts.extend(collect_desired_facts(contract))
    return tuple(sorted(set(facts), key=lambda f: (f.entity, f.property, f.state.value)))


def hypothetical_facts(report: WhatIfReport) -> tuple[TwinFact, ...]:
    """HYPOTHETICAL facts from a what-if/migration report.

    These are returned separately — callers merge them for comparison
    but the stored twin is never mutated by hypothetical state.
    """
    facts: list[TwinFact] = []
    change = report.change
    facts.append(
        TwinFact(
            entity=change.target,
            property=change.property,
            value=change.to,
            state=TwinState.HYPOTHETICAL,
            evidence_kind=EvidenceKind.DERIVED,
            source="what-if",
            confidence="hypothetical",
        )
    )
    for impact in report.impacts:
        facts.append(
            TwinFact(
                entity=change.target,
                property=impact.category,
                value=impact.detail,
                state=TwinState.HYPOTHETICAL,
                evidence_kind=EvidenceKind.DERIVED,
                source="what-if",
                confidence="hypothetical",
            )
        )
    return tuple(facts)


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

# state-pair -> drift type when the property doesn't classify itself
_STATE_PAIR_DRIFT: dict[tuple[TwinState, TwinState], DriftType] = {
    (TwinState.DESIRED, TwinState.DECLARED): DriftType.CONFIG_DRIFT,
    (TwinState.DECLARED, TwinState.IMPLEMENTED): DriftType.IMPLEMENTATION_DRIFT,
    (TwinState.DESIRED, TwinState.IMPLEMENTED): DriftType.IMPLEMENTATION_DRIFT,
    (TwinState.IMPLEMENTED, TwinState.OBSERVED): DriftType.RUNTIME_DRIFT,
    (TwinState.DECLARED, TwinState.OBSERVED): DriftType.RUNTIME_DRIFT,
    (TwinState.DESIRED, TwinState.OBSERVED): DriftType.RUNTIME_DRIFT,
}

# property-name -> drift type (property wins over state pair)
_PROPERTY_DRIFT: tuple[tuple[re.Pattern[str], DriftType], ...] = (
    (re.compile(r"owner|team|steward", re.I), DriftType.OWNERSHIP_DRIFT),
    (re.compile(r"schema|column|field|type", re.I), DriftType.SCHEMA_DRIFT),
    (re.compile(r"capabilit|platform$", re.I), DriftType.CAPABILITY_DRIFT),
    (re.compile(r"sla|latency|throughput|duration|rps", re.I), DriftType.PERFORMANCE_DRIFT),
    (re.compile(r"encrypt|public|acl|grant|policy|role|iam", re.I), DriftType.SECURITY_DRIFT),
)


def _drift_type(property_name: str, a: TwinState, b: TwinState) -> DriftType:
    for pat, drift in _PROPERTY_DRIFT:
        if pat.search(property_name):
            return drift
    return _STATE_PAIR_DRIFT.get((a, b), DriftType.CONFIG_DRIFT)


def _confidence(facts: list[TwinFact]) -> str:
    confs = {f.confidence for f in facts}
    if "observed" in confs:
        return "observed"
    if "declared" in confs:
        return "declared"
    return "inferred"


def reconcile(facts: tuple[TwinFact, ...] | list[TwinFact]) -> tuple[TwinReconciliation, ...]:
    """Compare states per (entity, property); emit divergences.

    Only *different* values across *different* states reconcile —
    same-value facts are agreement, same-state duplicates are merged
    (last-write is nondeterministic, so identical values collapse and
    conflicting same-state claims both surface via ``affected_entities``).
    Deterministic ordering throughout.
    """
    by_key: dict[tuple[str, str], list[TwinFact]] = {}
    for f in facts:
        by_key.setdefault((f.entity, f.property), []).append(f)

    out: list[TwinReconciliation] = []
    for (entity, prop), group in sorted(by_key.items()):
        by_state: dict[TwinState, set[str]] = {}
        confs: dict[TwinState, str] = {}
        sources: dict[TwinState, list[TwinFact]] = {}
        for f in group:
            by_state.setdefault(f.state, set()).add(f.value)
            confs[f.state] = f.confidence
            sources.setdefault(f.state, []).append(f)
        if len(by_state) < 2:
            continue
        states = sorted(by_state, key=lambda s: s.value)
        for i, a in enumerate(states):
            for b in states[i + 1 :]:
                va, vb = sorted(by_state[a]), sorted(by_state[b])
                if va == vb:
                    continue
                drift = _drift_type(prop, a, b)
                out.append(
                    TwinReconciliation(
                        entity=entity,
                        property=prop,
                        expected=";".join(va),
                        actual=";".join(vb),
                        states_compared=(a, b),
                        difference=f"{a.value}={','.join(va)} vs {b.value}={','.join(vb)}",
                        drift_type=drift,
                        confidence=_confidence(sources[a] + sources[b]),
                        affected_entities=tuple(sorted({entity})),
                    )
                )
    return tuple(sorted(out, key=lambda r: (r.entity, r.property, r.states_compared)))


def drift_summary(recs: tuple[TwinReconciliation, ...]) -> dict[str, int]:
    """Counts by drift type (deterministic)."""
    counts: dict[str, int] = {}
    for r in recs:
        counts[r.drift_type.value] = counts.get(r.drift_type.value, 0) + 1
    return dict(sorted(counts.items()))


def facts_for_entity(
    facts: tuple[TwinFact, ...], entity: str
) -> dict[str, dict[str, tuple[TwinFact, ...]]]:
    """One entity across states: ``{state: {property: (facts,...)}}``."""
    out: dict[str, dict[str, list[TwinFact]]] = {}
    for f in facts:
        if f.entity != entity:
            continue
        out.setdefault(f.state.value, {}).setdefault(f.property, []).append(f)
    return {
        state: {prop: tuple(fs) for prop, fs in sorted(props.items())}
        for state, props in sorted(out.items())
    }


# ---------------------------------------------------------------------------
# Temporal snapshots
# ---------------------------------------------------------------------------

TWIN_SNAPSHOT_FORMAT = "forge-doctor-data/twin-state@1"


def twin_state_snapshot(
    twin_graph: DataPlatformGraph,
    facts: tuple[TwinFact, ...],
    timestamp: str,
) -> dict[str, object]:
    """Versioned twin-state artifact: graph + state facts + reconciliation."""
    recs = reconcile(facts)
    return {
        "format": TWIN_SNAPSHOT_FORMAT,
        "timestamp": timestamp,
        "entities": sorted(e.id for e in twin_graph.entities()),
        "relationships": sorted(
            f"{r.src}|{r.kind.value}|{r.dst}" for r in twin_graph.relationships()
        ),
        "capabilities": sorted(
            f"{e.domain}/{e.identifier}={dict(e.attrs).get('status', '')}"
            for e in twin_graph.entities()
            if e.kind.value == "capability"
        ),
        "facts": [
            {
                "entity": f.entity,
                "property": f.property,
                "value": f.value,
                "state": f.state.value,
                "source": f.source,
                "confidence": f.confidence,
            }
            for f in sorted(facts, key=lambda x: (x.entity, x.property, x.state.value))
        ],
        "drift": [
            {
                "entity": r.entity,
                "property": r.property,
                "states": [s.value for s in r.states_compared],
                "expected": r.expected,
                "actual": r.actual,
                "drift_type": r.drift_type.value,
            }
            for r in recs
        ],
        "drift_summary": drift_summary(recs),
    }


@dataclass(frozen=True)
class TwinSnapshotDiff:
    """Deterministic delta between two twin-state snapshots."""

    entities_added: tuple[str, ...] = ()
    entities_removed: tuple[str, ...] = ()
    relationships_added: tuple[str, ...] = ()
    relationships_removed: tuple[str, ...] = ()
    capabilities_changed: tuple[str, ...] = ()
    drift_introduced: tuple[str, ...] = ()
    drift_resolved: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "entities_added": list(self.entities_added),
            "entities_removed": list(self.entities_removed),
            "relationships_added": list(self.relationships_added),
            "relationships_removed": list(self.relationships_removed),
            "capabilities_changed": list(self.capabilities_changed),
            "drift_introduced": list(self.drift_introduced),
            "drift_resolved": list(self.drift_resolved),
        }


def _cap_key(row: str) -> str:
    return row.rsplit("=", 1)[0]


def _snap_rows(d: dict[str, object], key: str) -> list[str]:
    v = d.get(key, [])
    return [r for r in v if isinstance(r, str)] if isinstance(v, (list, tuple)) else []


def _snap_dicts(d: dict[str, object], key: str) -> list[dict[str, object]]:
    v = d.get(key, [])
    return [r for r in v if isinstance(r, dict)] if isinstance(v, (list, tuple)) else []


def diff_twin_snapshots(a: dict[str, object], b: dict[str, object]) -> TwinSnapshotDiff:
    """Diff two ``twin_state_snapshot`` artifacts (a=older, b=newer)."""
    ent_a, ent_b = set(_snap_rows(a, "entities")), set(_snap_rows(b, "entities"))
    rel_a, rel_b = set(_snap_rows(a, "relationships")), set(_snap_rows(b, "relationships"))
    cap_a = {_cap_key(r): r for r in _snap_rows(a, "capabilities")}
    cap_b = {_cap_key(r): r for r in _snap_rows(b, "capabilities")}
    cap_changed = sorted(k for k in cap_a.keys() & cap_b.keys() if cap_a[k] != cap_b[k])
    drift_a = {json_row_key(r) for r in _snap_dicts(a, "drift")}
    drift_b = {json_row_key(r) for r in _snap_dicts(b, "drift")}
    return TwinSnapshotDiff(
        entities_added=tuple(sorted(ent_b - ent_a)),
        entities_removed=tuple(sorted(ent_a - ent_b)),
        relationships_added=tuple(sorted(rel_b - rel_a)),
        relationships_removed=tuple(sorted(rel_a - rel_b)),
        capabilities_changed=tuple(cap_changed),
        drift_introduced=tuple(sorted(drift_b - drift_a)),
        drift_resolved=tuple(sorted(drift_a - drift_b)),
    )


def json_row_key(row: dict[str, object]) -> str:
    """Stable identity for a drift row."""
    states = row.get("states", [])
    states_s = ",".join(str(s) for s in states) if isinstance(states, (list, tuple)) else ""
    return (
        f"{row.get('entity')}|{row.get('property')}|{states_s}|"
        f"{row.get('expected')}|{row.get('actual')}"
    )


def twin_history_dir(root: Path) -> Path:
    from forge_doctor_data.core.history import history_dir

    return history_dir(root) / "twin"


def record_twin_snapshot(root: Path, snapshot: dict[str, object], name: str) -> Path:
    """Persist a twin-state snapshot under the history dir."""
    import json

    target = twin_history_dir(root)
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{name}.json"
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_twin_snapshot(root: Path, ref: str) -> dict[str, object]:
    """Resolve a twin snapshot by file path or recorded name."""
    import json
    from pathlib import Path

    path = root / ref
    if not path.is_file():
        path = Path(ref) if Path(ref).is_file() else twin_history_dir(root) / f"{ref}.json"
    if not path.is_file():
        from forge_doctor_data.core.history import HistoryError

        raise HistoryError(f"no twin snapshot '{ref}'")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("format") != TWIN_SNAPSHOT_FORMAT:
        from forge_doctor_data.core.history import HistoryError

        raise HistoryError(f"{path}: not a {TWIN_SNAPSHOT_FORMAT} snapshot")
    return data
