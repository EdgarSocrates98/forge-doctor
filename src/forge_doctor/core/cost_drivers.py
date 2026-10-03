"""Technical cost-driver intelligence (spec 237, program P phase 3).

A taxonomy of *technical* cost drivers — bytes, slot-ms, credits,
executor time — never billing. No monetary amounts are emitted without
explicit pricing/config evidence in the repo; the model reports units
and evidence, not dollars.

Drivers attach to the spec-235 ``QueryExecution`` spine and to
evidence-tagged platform-graph entities. Attribution runs
execution → dataset → team → environment; a team is attached only when
ownership evidence exists, never inferred.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from forge_doctor.core.execution_model import QueryExecution
from forge_doctor.core.knowledge import load_pack
from forge_doctor.core.models import Confidence, EvidenceKind, Severity


class CostDriverKind(Enum):
    COMPUTE_DURATION = "compute_duration"
    IDLE_CAPACITY = "idle_capacity"
    SCAN_VOLUME = "scan_volume"
    STORAGE_VOLUME = "storage_volume"
    NETWORK_EGRESS = "network_egress"
    CROSS_REGION_TRANSFER = "cross_region_transfer"
    REPLICATION = "replication"
    REQUEST_COUNT = "request_count"
    INDEX_STORAGE = "index_storage"
    CACHE_STORAGE = "cache_storage"
    SHUFFLE_VOLUME = "shuffle_volume"
    SPILL_IO = "spill_io"
    WAREHOUSE_UPTIME = "warehouse_uptime"
    SLOT_USAGE = "slot_usage"
    CREDIT_USAGE = "credit_usage"
    RPU_USAGE = "rpu_usage"
    EXECUTOR_TIME = "executor_time"


@dataclass(frozen=True)
class CostDriver:
    """One technical cost driver anchored to evidence."""

    kind: CostDriverKind
    source: str  # execution | entity id
    value: float
    unit: str  # bytes | ms | slot_ms | count | credits | rpu_s
    entity: str = ""  # dataset/table the driver belongs to
    execution: str = ""  # execution_id when runtime-sourced
    team: str = ""  # only with ownership evidence
    environment: str = ""  # env tag when declared
    confidence: Confidence = Confidence.LOW
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "source": self.source,
            "value": self.value,
            "unit": self.unit,
            "entity": self.entity,
            "execution": self.execution,
            "team": self.team,
            "environment": self.environment,
            "confidence": self.confidence.value,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class DataTransferSignal:
    """Observed cross-cloud / cross-region data movement."""

    source_cloud: str
    target_cloud: str
    source_region: str = ""
    target_region: str = ""
    bytes: float | None = None
    mode: str = ""  # read_remote | write_remote | replicate
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_cloud": self.source_cloud,
            "target_cloud": self.target_cloud,
            "source_region": self.source_region,
            "target_region": self.target_region,
            "bytes": self.bytes,
            "mode": self.mode,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class CostFinding:
    """COST### finding — technical driver evidence, never a price."""

    check_id: str
    title: str
    severity: Severity
    message: str
    observed: str
    derived: str
    threshold: str
    evidence: tuple[str, ...]
    confidence: Confidence
    evidence_kind: EvidenceKind = EvidenceKind.RUNTIME

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "title": self.title,
            "severity": self.severity.value,
            "message": self.message,
            "observed": self.observed,
            "derived": self.derived,
            "threshold": self.threshold,
            "confidence": self.confidence.value,
            "evidence": list(self.evidence),
        }


# ---------------------------------------------------------------------------
# Policy — bounds are explicit inputs, never hidden globals
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CostPolicy:
    """Optional bounds for COST findings; None bound -> informational."""

    scan_volume_bytes: float | None = None  # COST002 per-execution scan
    replication_max_copies: float | None = None  # COST004
    materialize_min_dup: float | None = None  # COST005 shared subject count
    shuffle_bytes: float | None = None  # COST006
    small_file_bytes: float | None = None  # COST007 avg bytes/file

    @classmethod
    def defaults(cls) -> CostPolicy:
        """Declared defaults from ``knowledge/cost_drivers/thresholds.json``;
        falls back to the same values inline so the pack is a config
        surface, not a hard dependency."""
        try:
            pack = load_pack("cost_drivers", "thresholds")
            thresholds = pack.get("thresholds", {})
            if isinstance(thresholds, dict) and thresholds:
                vals = {
                    k: _as_float((v or {}).get("value"))
                    for k, v in thresholds.items()
                    if isinstance(v, dict)
                }
                fields = cls.__dataclass_fields__
                return cls(**{k: v for k, v in vals.items() if v is not None and k in fields})
        except Exception:
            pass
        return cls(
            scan_volume_bytes=10.0 * 1024**3,
            replication_max_copies=3.0,
            materialize_min_dup=3,
            shuffle_bytes=1024**3,
            small_file_bytes=8.0 * 1024**2,
        )


# ---------------------------------------------------------------------------
# Extraction — executions + graph entities, mapped per engine semantics
# ---------------------------------------------------------------------------

# Engine-specific meaning of the normalized duration/cpu fields.
_ENGINE_TIME_KIND: dict[str, CostDriverKind] = {
    "snowflake": CostDriverKind.WAREHOUSE_UPTIME,
    "redshift": CostDriverKind.RPU_USAGE,
}
_ENGINE_CPU_KIND: dict[str, CostDriverKind] = {
    "bigquery": CostDriverKind.SLOT_USAGE,
    "spark": CostDriverKind.EXECUTOR_TIME,
    "databricks": CostDriverKind.EXECUTOR_TIME,
    "emr": CostDriverKind.EXECUTOR_TIME,
}


def extract_drivers(
    executions: list[QueryExecution],
    graph: Any = None,
) -> list[CostDriver]:
    """Map normalized execution + entity evidence onto CostDriver rows.

    Only demonstrable fields produce drivers: absent metrics stay
    absent (no driver emitted), vendor units are labeled per the
    knowledge pack rather than translated.
    """
    out: list[CostDriver] = []
    for ex in executions:
        ev = _ev(ex)
        conf = _conf(ex)
        entity = ex.inputs[0] if ex.inputs else ""
        if ex.duration_ms is not None:
            out.append(
                CostDriver(
                    kind=_ENGINE_TIME_KIND.get(ex.engine, CostDriverKind.COMPUTE_DURATION),
                    source=ex.execution_id,
                    value=ex.duration_ms,
                    unit="ms",
                    entity=entity,
                    execution=ex.execution_id,
                    confidence=conf,
                    evidence=ev,
                )
            )
        if ex.cpu_time_ms is not None:
            out.append(
                CostDriver(
                    kind=_ENGINE_CPU_KIND.get(ex.engine, CostDriverKind.EXECUTOR_TIME),
                    source=ex.execution_id,
                    value=ex.cpu_time_ms,
                    unit="slot_ms" if ex.engine == "bigquery" else "ms",
                    entity=entity,
                    execution=ex.execution_id,
                    confidence=conf,
                    evidence=ev,
                )
            )
        if ex.bytes_read is not None:
            out.append(
                CostDriver(
                    kind=CostDriverKind.SCAN_VOLUME,
                    source=ex.execution_id,
                    value=ex.bytes_read,
                    unit="bytes",
                    entity=entity,
                    execution=ex.execution_id,
                    confidence=conf,
                    evidence=ev,
                )
            )
        shuffle = _stage_sum(ex, "shuffle_bytes")
        if shuffle:
            out.append(
                CostDriver(
                    kind=CostDriverKind.SHUFFLE_VOLUME,
                    source=ex.execution_id,
                    value=shuffle,
                    unit="bytes",
                    entity=entity,
                    execution=ex.execution_id,
                    confidence=conf,
                    evidence=ev,
                )
            )
        spill = ex.spill_bytes if ex.spill_bytes is not None else _stage_sum(ex, "spill_bytes")
        if spill:
            out.append(
                CostDriver(
                    kind=CostDriverKind.SPILL_IO,
                    source=ex.execution_id,
                    value=spill,
                    unit="bytes",
                    entity=entity,
                    execution=ex.execution_id,
                    confidence=conf,
                    evidence=ev,
                )
            )
        remote = _stage_sum(ex, "remote_io_bytes")
        if remote:
            out.append(
                CostDriver(
                    kind=CostDriverKind.NETWORK_EGRESS,
                    source=ex.execution_id,
                    value=remote,
                    unit="bytes",
                    entity=entity,
                    execution=ex.execution_id,
                    confidence=conf,
                    evidence=ev,
                )
            )
    out.extend(_entity_drivers(graph))
    return _attribute(out, graph)


def _entity_drivers(graph: Any) -> list[CostDriver]:
    """Storage/replication/index/cache drivers from declared entity attrs."""
    out: list[CostDriver] = []
    if graph is None:
        return out
    for e in graph.entities():
        ev: tuple[str, ...] = (f"entity:{e.id}",)
        if e.file is not None:
            ev += (f"file:{e.file.as_posix()}" + (f":{e.line}" if e.line else ""),)
        size = _as_float(e.attr("size_bytes") or e.attr("storage_bytes"))
        if size is not None:
            out.append(
                CostDriver(
                    kind=CostDriverKind.STORAGE_VOLUME,
                    source=e.id,
                    value=size,
                    unit="bytes",
                    entity=e.id,
                    confidence=Confidence.MEDIUM,
                    evidence=ev,
                )
            )
        repl = _as_float(e.attr("replicas") or e.attr("number_of_replicas"))
        if repl is not None and repl > 0:
            out.append(
                CostDriver(
                    kind=CostDriverKind.REPLICATION,
                    source=e.id,
                    value=repl,
                    unit="count",
                    entity=e.id,
                    confidence=Confidence.MEDIUM,
                    evidence=ev,
                )
            )
        if e.attr("index") or e.attr("indexes") or e.attr("primary_key"):
            out.append(
                CostDriver(
                    kind=CostDriverKind.INDEX_STORAGE,
                    source=e.id,
                    value=0.0,
                    unit="presence",
                    entity=e.id,
                    confidence=Confidence.LOW,
                    evidence=ev,
                )
            )
        if e.attr("cache") or e.attr("caching"):
            out.append(
                CostDriver(
                    kind=CostDriverKind.CACHE_STORAGE,
                    source=e.id,
                    value=0.0,
                    unit="presence",
                    entity=e.id,
                    confidence=Confidence.LOW,
                    evidence=ev,
                )
            )
    return out


def _attribute(drivers: list[CostDriver], graph: Any) -> list[CostDriver]:
    """Attach team/environment only where ownership evidence exists."""
    if graph is None:
        return drivers
    owners: dict[str, tuple[str, str]] = {}  # entity/name -> (team, env)
    for e in graph.entities():
        team = e.attr("owner") or e.attr("team")
        env = e.attr("env") or e.attr("environment")
        if team or env:
            owners[e.id] = (team or "", env or "")
            owners[e.name or e.identifier] = (team or "", env or "")
    out: list[CostDriver] = []
    for d in drivers:
        team, env = owners.get(d.entity, ("", ""))
        if not team and not env:
            team, env = owners.get(d.source, ("", ""))
        out.append(
            CostDriver(
                kind=d.kind,
                source=d.source,
                value=d.value,
                unit=d.unit,
                entity=d.entity,
                execution=d.execution,
                team=team,
                environment=env,
                confidence=d.confidence,
                evidence=d.evidence,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Cross-cloud / cross-region transfer detection
# ---------------------------------------------------------------------------

_ENGINE_CLOUD: dict[str, str] = {
    "redshift": "aws",
    "emr": "aws",
    "athena": "aws",
    "bigquery": "gcp",
    "dataflow": "gcp",
    "dataproc": "gcp",
    "synapse": "azure",
    "adls": "azure",
    "databricks": "",  # multi-cloud — unknown without more evidence
    "snowflake": "",  # multi-cloud
    "trino": "",  # deployment-dependent
    "clickhouse": "",  # deployment-dependent
}


def detect_transfers(
    executions: list[QueryExecution],
    graph: Any = None,
) -> list[DataTransferSignal]:
    """Cross-cloud reads: engine cloud vs input entity cloud differ.

    Emits only when *both* clouds are known and demonstrably differ —
    never guesses a cloud for a deployment-neutral engine.
    """
    out: list[DataTransferSignal] = []
    if graph is None:
        return out
    ent_cloud: dict[str, str] = {}
    for e in graph.entities():
        cloud = _entity_cloud(e)
        if cloud:
            ent_cloud[e.id] = cloud
            if e.name:
                ent_cloud[e.name] = cloud
            ent_cloud.setdefault(e.identifier, cloud)
    for ex in executions:
        dst = _ENGINE_CLOUD.get(ex.engine, "")
        if not dst:
            continue
        for inp in ex.inputs:
            src = ent_cloud.get(inp, "")
            if src and src != dst:
                out.append(
                    DataTransferSignal(
                        source_cloud=src,
                        target_cloud=dst,
                        bytes=ex.bytes_read,
                        mode="read_remote",
                        evidence=(
                            f"execution:{ex.execution_id}",
                            f"input:{inp}",
                        ),
                    )
                )
    return sorted(set(out), key=lambda t: (t.source_cloud, t.target_cloud, t.mode))


def _entity_cloud(e: Any) -> str:
    dom = (e.domain or "").lower()
    for cloud in ("aws", "azure", "gcp"):
        if cloud in dom:
            return cloud
    # engine-named domains map through the engine cloud table
    return _ENGINE_CLOUD.get(dom, "")


# ---------------------------------------------------------------------------
# COST findings — same explainability contract as PERF
# ---------------------------------------------------------------------------


def cost_findings(
    drivers: list[CostDriver],
    transfers: list[DataTransferSignal] | None = None,
    policy: CostPolicy | None = None,
    executions: list[QueryExecution] | None = None,
) -> list[CostFinding]:
    """COST001-COST007 over extracted drivers + transfer signals."""
    out: list[CostFinding] = []

    # COST001 — idle compute: compute duration with no bytes read/written
    for ex in executions or []:
        if (
            ex.duration_ms is not None
            and ex.duration_ms > 0
            and not ex.bytes_read
            and not ex.bytes_written
        ):
            out.append(
                CostFinding(
                    check_id="COST001",
                    title="Idle compute evidence",
                    severity=Severity.INFO,
                    message=(
                        f"{ex.execution_id} ({ex.engine}): ran "
                        f"{ex.duration_ms:.0f}ms with no bytes read or written"
                    ),
                    observed=f"duration_ms={ex.duration_ms:.0f}, bytes_read=0, bytes_written=0",
                    derived="compute time with no I/O",
                    threshold="observational — no bound required",
                    evidence=_ev(ex),
                    confidence=_conf(ex),
                )
            )

    # COST002 — repeated high scan volume on the same subject
    bound = policy.scan_volume_bytes if policy else None
    scans: dict[str, list[CostDriver]] = {}
    for d in drivers:
        if d.kind is CostDriverKind.SCAN_VOLUME and d.entity:
            scans.setdefault(d.entity, []).append(d)
    for entity, rows in sorted(scans.items()):
        high = [d for d in rows if bound is None or d.value > bound]
        if len(rows) >= 2 and len(high) == len(rows):
            out.append(
                CostFinding(
                    check_id="COST002",
                    title="Repeated high scan volume",
                    severity=Severity.WARNING if bound is not None else Severity.INFO,
                    message=(
                        f"{entity}: {len(rows)} executions scanned "
                        f"{sum(d.value for d in rows):.0f} bytes total"
                    ),
                    observed=f"{len(rows)} scans of {entity}",
                    derived=f"total={sum(d.value for d in rows):.0f} bytes",
                    threshold=(
                        f"scan_volume_bytes={bound}" if bound is not None else "no configured bound"
                    ),
                    evidence=tuple(ev for d in rows for ev in d.evidence)[:8],
                    confidence=_max_conf(rows),
                )
            )

    # COST003 — cross-cloud movement detected
    for t in transfers or []:
        out.append(
            CostFinding(
                check_id="COST003",
                title="Cross-cloud data movement",
                severity=Severity.WARNING,
                message=(
                    f"{t.source_cloud} -> {t.target_cloud} ({t.mode})"
                    + (f", {t.bytes:.0f} bytes" if t.bytes is not None else "")
                ),
                observed=f"mode={t.mode}, clouds={t.source_cloud}/{t.target_cloud}",
                derived="transfer crosses cloud boundary",
                threshold="any observed cross-cloud movement",
                evidence=t.evidence,
                confidence=Confidence.HIGH,
            )
        )

    # COST004 — excessive replication footprint
    rbound = policy.replication_max_copies if policy else None
    for d in drivers:
        if d.kind is CostDriverKind.REPLICATION:
            fired = rbound is not None and d.value > rbound
            out.append(
                CostFinding(
                    check_id="COST004",
                    title="Replication footprint declared",
                    severity=Severity.WARNING if fired else Severity.INFO,
                    message=f"{d.entity}: {d.value:.0f} replicas declared",
                    observed=f"replicas={d.value:.0f}",
                    derived=f"storage multiplier ~{1 + d.value:.0f}x",
                    threshold=(
                        f"replication_max_copies={rbound}"
                        if rbound is not None
                        else "no configured bound"
                    ),
                    evidence=d.evidence,
                    confidence=d.confidence if fired else Confidence.LOW,
                )
            )

    # COST005 — materialization duplication: same output written by many runs
    writes: dict[str, int] = {}
    for ex in executions or []:
        for tgt in ex.outputs:
            writes[tgt] = writes.get(tgt, 0) + 1
    mbound = (policy.materialize_min_dup if policy else None) or 3.0
    for tgt, n in sorted(writes.items()):
        if n >= mbound:
            out.append(
                CostFinding(
                    check_id="COST005",
                    title="Materialization duplication",
                    severity=Severity.INFO,
                    message=f"{tgt}: written by {n} distinct executions",
                    observed=f"{n} executions write {tgt}",
                    derived="repeated materialization of one subject",
                    threshold=f"materialize_min_dup={mbound}",
                    evidence=tuple(
                        f"execution:{ex.execution_id}"
                        for ex in (executions or [])
                        if tgt in ex.outputs
                    )[:8],
                    confidence=Confidence.MEDIUM,
                )
            )

    # COST006 — high shuffle/spill driver
    sbound = policy.shuffle_bytes if policy else None
    for d in drivers:
        if d.kind in (CostDriverKind.SHUFFLE_VOLUME, CostDriverKind.SPILL_IO):
            fired = sbound is not None and d.value > sbound
            out.append(
                CostFinding(
                    check_id="COST006",
                    title="Shuffle/spill cost driver",
                    severity=Severity.WARNING if fired else Severity.INFO,
                    message=(f"{d.source}: {d.kind.value}={d.value:.0f} bytes"),
                    observed=f"{d.kind.value}={d.value:.0f} bytes",
                    derived="exchange/spill I/O is a technical cost driver",
                    threshold=(
                        f"shuffle_bytes={sbound}" if sbound is not None else "no configured bound"
                    ),
                    evidence=d.evidence,
                    confidence=d.confidence if fired else Confidence.LOW,
                )
            )

    # COST007 — tiny-file overhead: needs an exported file count
    fbound = policy.small_file_bytes if policy else None
    for ex in executions or []:
        files = float(
            sum(
                sc.files_scanned
                for s in ex.stages
                for sc in s.scans
                if sc.files_scanned is not None
            )
        )
        if ex.bytes_read and files:
            avg = ex.bytes_read / files
            if fbound is not None and avg < fbound:
                out.append(
                    CostFinding(
                        check_id="COST007",
                        title="Tiny-file overhead driver",
                        severity=Severity.WARNING,
                        message=(
                            f"{ex.execution_id}: avg {avg:.0f} bytes/file over {files:.0f} files"
                        ),
                        observed=f"avg_file_bytes={avg:.0f}",
                        derived="per-file overhead dominates scan",
                        threshold=f"small_file_bytes={fbound}",
                        evidence=_ev(ex),
                        confidence=_conf(ex),
                    )
                )
    return sorted(out, key=lambda f: (f.severity is not Severity.WARNING, f.check_id, f.message))


# ---------------------------------------------------------------------------
# Migration cost deltas — compare driver *kinds*, never "target cheaper"
# ---------------------------------------------------------------------------


def driver_kinds_for_engine(engine: str) -> set[CostDriverKind]:
    """Driver kinds an engine can exhibit per the knowledge pack."""
    pack = load_pack("cost_drivers", "drivers") or {}
    kinds = pack.get("engines", {}).get(engine, {}).get("kinds", [])
    out: set[CostDriverKind] = set()
    for k in kinds:
        try:
            out.add(CostDriverKind(k))
        except ValueError:
            continue
    return out


def migration_cost_delta(
    source_engine: str,
    target_engine: str,
) -> dict[str, Any]:
    """Compare driver *kinds* between engines — shape change, not price.

    Reports which driver kinds are gained/lost/shared so a migration
    review sees the cost-model shift (e.g. credit-driven vs
    scan+slot-driven). Never claims the target is cheaper.
    """
    src = driver_kinds_for_engine(source_engine)
    dst = driver_kinds_for_engine(target_engine)
    return {
        "source_engine": source_engine,
        "target_engine": target_engine,
        "source_kinds": sorted(k.value for k in src),
        "target_kinds": sorted(k.value for k in dst),
        "gained": sorted(k.value for k in dst - src),
        "lost": sorted(k.value for k in src - dst),
        "shared": sorted(k.value for k in src & dst),
        "note": (
            "driver-kind comparison only — relative cost depends on "
            "workload shape, not covered here"
        ),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_CONF_RANK = {Confidence.LOW: 0, Confidence.MEDIUM: 1, Confidence.HIGH: 2}


def _max_conf(rows: list[CostDriver]) -> Confidence:
    return max(rows, key=lambda d: _CONF_RANK[d.confidence]).confidence


def _conf(ex: QueryExecution) -> Confidence:
    if any("adapter:" in e for e in ex.evidence):
        return Confidence.HIGH
    return Confidence.MEDIUM


def _ev(ex: QueryExecution, extra: str = "") -> tuple[str, ...]:
    out = tuple(ex.evidence)
    return (*out, extra) if extra else out


def _stage_sum(ex: QueryExecution, name: str) -> float:
    return float(sum(v for s in ex.stages if (v := getattr(s, name, None)) is not None))


def _as_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
