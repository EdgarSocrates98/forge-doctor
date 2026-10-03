"""Cross-engine performance intelligence (spec 236, program P phase 2).

Normalizes equivalent runtime problems across engines into shared
``SignalFamily`` values, then emits PERF findings over normalized
``QueryExecution`` rows. Thresholds come from an explicit
``PerfPolicy`` (contract / knowledge pack / baseline); without one,
findings degrade to informational opportunities — no magic numbers.

Signals carry observed-vs-derived provenance: a ``PerformanceSignal``
always names what was measured, what was derived, which threshold or
baseline applied, and which evidence anchors it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from forge_doctor.core.execution_model import (
    QueryExecution,
    StageKind,
)
from forge_doctor.core.models import Confidence, EvidenceKind, Severity


class SignalFamily(Enum):
    SCAN_AMPLIFICATION = "scan_amplification"
    JOIN_AMPLIFICATION = "join_amplification"
    DATA_EXCHANGE_AMPLIFICATION = "data_exchange_amplification"
    SPILL_PRESSURE = "spill_pressure"
    QUEUE_PRESSURE = "queue_pressure"
    POOR_PRUNING = "poor_pruning"
    REMOTE_IO_AMPLIFICATION = "remote_io_amplification"
    SMALL_FILE_AMPLIFICATION = "small_file_amplification"
    LOW_PARALLELISM = "low_parallelism"
    HIGH_SKEW = "high_skew"
    MATERIALIZATION_OVERHEAD = "materialization_overhead"
    WRITE_AMPLIFICATION = "write_amplification"


@dataclass(frozen=True)
class PerformanceSignal:
    """One normalized performance signal anchored to evidence."""

    family: SignalFamily
    subject: str  # execution_id | stage id | entity id
    engine: str
    observed: str  # what was measured ("spill_bytes=12GB")
    derived: str  # what was computed ("spill ratio 0.6")
    value: float | None = None
    unit: str = ""
    threshold: str = ""  # which policy/baseline bound fired
    confidence: Confidence = Confidence.LOW
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkewSignal:
    """Runtime skew — only when a real task/attempt distribution exists."""

    execution: str
    stage: str
    max_ms: float
    median_ms: float
    p95_ms: float
    ratio: float  # max/median
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class QueuePressure:
    """Normalized queue/wait pressure across engines."""

    engine: str  # snowflake | redshift | bigquery | trino
    execution: str
    queue_ms: float
    duration_ms: float
    share: float  # queue / duration
    mechanism: str  # warehouse_queue | wlm | slot_wait | resource_group


@dataclass(frozen=True)
class PerformanceFinding:
    """PERF### finding — engine-scoped (not a project-scan CheckResult)."""

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
# Policy — thresholds are explicit inputs, never hidden globals
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PerfPolicy:
    """Thresholds for PERF findings. ``None`` field = never fire on it."""

    scan_amplification: float | None = None  # PERF001
    pruning_min: float | None = None  # PERF002 share scanned
    exchange_amplification: float | None = None  # PERF003
    skew_ratio: float | None = None  # PERF004 max/median
    spill_ratio: float | None = None  # PERF005
    queue_share: float | None = None  # PERF006
    remote_io_ratio: float | None = None  # PERF007
    min_parallelism: float | None = None  # PERF008 stages-level
    small_file_bytes: float | None = None  # PERF009 avg bytes/file
    materialize_count: float | None = None  # PERF010 repeated materializations

    @classmethod
    def defaults(cls) -> PerfPolicy:
        """Declared defaults from ``knowledge/performance/thresholds.json``;
        falls back to the same values inline so the pack is a config
        surface, not a hard dependency."""
        try:
            from forge_doctor.core.knowledge import load_pack

            pack = load_pack("performance", "thresholds")
            thresholds = pack.get("thresholds", {})
            if isinstance(thresholds, dict) and thresholds:
                vals = {
                    k: _as_float((v or {}).get("value"))
                    for k, v in thresholds.items()
                    if isinstance(v, dict)
                }
                return cls(**{k: v for k, v in vals.items() if v is not None})
        except Exception:
            pass
        return cls(
            scan_amplification=5.0,
            pruning_min=0.5,
            exchange_amplification=3.0,
            skew_ratio=4.0,
            spill_ratio=0.5,
            queue_share=0.3,
            remote_io_ratio=0.5,
            min_parallelism=1.0,
            materialize_count=3.0,
        )


def _as_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Signal extraction over QueryExecution rows
# ---------------------------------------------------------------------------


def _ev(ex: QueryExecution, extra: str = "") -> tuple[str, ...]:
    out = [f"execution:{ex.execution_id}", f"engine:{ex.engine}", *ex.evidence]
    if extra:
        out.append(extra)
    return tuple(out)


def extract_signals(executions: list[QueryExecution]) -> list[PerformanceSignal]:
    """Normalize each execution into family-tagged signals.

    A signal exists only where the underlying evidence exists —
    missing inputs yield no signal rather than a zero.
    """
    signals: list[PerformanceSignal] = []
    for ex in executions:
        m = ex.metrics
        if m.scan_amplification is not None:
            signals.append(
                PerformanceSignal(
                    SignalFamily.SCAN_AMPLIFICATION,
                    ex.execution_id,
                    ex.engine,
                    observed=f"bytes_read={ex.bytes_read}",
                    derived=(
                        f"scan_amplification={m.scan_amplification.value} "
                        f"({m.scan_amplification.basis})"
                    ),
                    value=m.scan_amplification.value,
                    confidence=_conf(ex),
                    evidence=_ev(ex),
                )
            )
        if m.shuffle_amplification is not None:
            signals.append(
                PerformanceSignal(
                    SignalFamily.DATA_EXCHANGE_AMPLIFICATION,
                    ex.execution_id,
                    ex.engine,
                    observed=f"shuffle_bytes={_stage_sum(ex, 'shuffle_bytes')}",
                    derived=(
                        f"exchange_amplification={m.shuffle_amplification.value} "
                        f"({m.shuffle_amplification.basis})"
                    ),
                    value=m.shuffle_amplification.value,
                    confidence=_conf(ex),
                    evidence=_ev(ex),
                )
            )
        if m.spill_ratio is not None or ex.spill_bytes:
            signals.append(
                PerformanceSignal(
                    SignalFamily.SPILL_PRESSURE,
                    ex.execution_id,
                    ex.engine,
                    observed=f"spill_bytes={ex.spill_bytes}",
                    derived=(
                        f"spill_ratio={m.spill_ratio.value} ({m.spill_ratio.basis})"
                        if m.spill_ratio
                        else "spill present; ratio unknown (no denominator)"
                    ),
                    value=m.spill_ratio.value if m.spill_ratio else None,
                    confidence=_conf(ex),
                    evidence=_ev(ex),
                )
            )
        if m.queue_ratio is not None or ex.queue_time_ms:
            signals.append(
                PerformanceSignal(
                    SignalFamily.QUEUE_PRESSURE,
                    ex.execution_id,
                    ex.engine,
                    observed=f"queue_time_ms={ex.queue_time_ms}",
                    derived=(
                        f"queue_share={m.queue_ratio.value}" if m.queue_ratio else "queue present"
                    ),
                    value=m.queue_ratio.value if m.queue_ratio else None,
                    confidence=_conf(ex),
                    evidence=_ev(ex),
                )
            )
        if m.remote_io_ratio is not None:
            signals.append(
                PerformanceSignal(
                    SignalFamily.REMOTE_IO_AMPLIFICATION,
                    ex.execution_id,
                    ex.engine,
                    observed=f"remote_io={_stage_sum(ex, 'remote_io_bytes')}",
                    derived=f"remote_io_ratio={m.remote_io_ratio.value}",
                    value=m.remote_io_ratio.value,
                    confidence=_conf(ex),
                    evidence=_ev(ex),
                )
            )
        if m.output_amplification is not None:
            signals.append(
                PerformanceSignal(
                    SignalFamily.WRITE_AMPLIFICATION,
                    ex.execution_id,
                    ex.engine,
                    observed=f"bytes_written={ex.bytes_written}",
                    derived=f"output_amplification={m.output_amplification.value}",
                    value=m.output_amplification.value,
                    confidence=_conf(ex),
                    evidence=_ev(ex),
                )
            )
        for st in ex.stages:
            for scan in st.scans:
                if scan.pruning is not None:
                    signals.append(
                        PerformanceSignal(
                            SignalFamily.POOR_PRUNING,
                            ex.execution_id,
                            ex.engine,
                            observed=(
                                f"partitions {int(scan.partitions_scanned or 0)}/"
                                f"{int(scan.partitions_total or 0)} scanned"
                            ),
                            derived=f"pruning_share={scan.pruning}",
                            value=scan.pruning,
                            confidence=_conf(ex),
                            evidence=_ev(ex, f"stage:{st.id}"),
                        )
                    )
        for st in ex.stages:
            for j in st.joins:
                # join amplification: output far exceeds the larger input
                if j.output_rows is not None and (j.left_rows or j.right_rows):
                    biggest_in = max(j.left_rows or 0.0, j.right_rows or 0.0)
                    if biggest_in > 0:
                        ratio = round(j.output_rows / biggest_in, 6)
                        signals.append(
                            PerformanceSignal(
                                SignalFamily.JOIN_AMPLIFICATION,
                                f"{ex.execution_id}:{st.id}",
                                ex.engine,
                                observed=(
                                    f"join output_rows={j.output_rows} vs max input={biggest_in}"
                                ),
                                derived=f"join_amplification={ratio}x",
                                value=ratio,
                                confidence=_conf(ex),
                                evidence=_ev(ex, f"stage:{st.id}"),
                            )
                        )
                # skew recorded on a join comes from real task distributions
                if j.skew is not None:
                    signals.append(
                        PerformanceSignal(
                            SignalFamily.HIGH_SKEW,
                            f"{ex.execution_id}:{st.id}",
                            ex.engine,
                            observed=f"join task skew ratio={j.skew}",
                            derived=f"skew={j.skew}x (task distribution)",
                            value=j.skew,
                            confidence=_conf(ex),
                            evidence=_ev(ex, f"stage:{st.id}"),
                        )
                    )
            for scan in st.scans:
                # small-file amplification only when a file count was exported
                if scan.files_scanned and scan.bytes_scanned:
                    avg = scan.bytes_scanned / scan.files_scanned
                    signals.append(
                        PerformanceSignal(
                            SignalFamily.SMALL_FILE_AMPLIFICATION,
                            f"{ex.execution_id}:{st.id}",
                            ex.engine,
                            observed=(
                                f"{scan.files_scanned:.0f} files / {scan.bytes_scanned:.0f} bytes"
                            ),
                            derived=f"avg_file_bytes={avg:.0f}",
                            value=avg,
                            confidence=_conf(ex),
                            evidence=_ev(ex, f"stage:{st.id}"),
                        )
                    )
        mat_stages = [s for s in ex.stages if s.kind is StageKind.MATERIALIZE]
        if mat_stages:
            signals.append(
                PerformanceSignal(
                    SignalFamily.MATERIALIZATION_OVERHEAD,
                    ex.execution_id,
                    ex.engine,
                    observed=f"{len(mat_stages)} materialize stage(s)",
                    derived="repeated materialization observed",
                    value=float(len(mat_stages)),
                    confidence=_conf(ex),
                    evidence=_ev(ex),
                )
            )
        # Low parallelism: single-task stages when task counts were exported.
        singles = [s for s in ex.stages if s.duration_ms and s.input_rows == 1]
        if len(singles) >= 2:
            signals.append(
                PerformanceSignal(
                    SignalFamily.LOW_PARALLELISM,
                    ex.execution_id,
                    ex.engine,
                    observed=f"{len(singles)} near-serial stage(s)",
                    derived="low effective parallelism",
                    value=float(len(singles)),
                    confidence=_conf(ex),
                    evidence=_ev(ex),
                )
            )
    return signals


def _conf(ex: QueryExecution) -> Confidence:
    """§62 ladder: runtime evidence -> high; derived-only -> medium."""
    if any("adapter:" in e for e in ex.evidence):
        return Confidence.HIGH
    return Confidence.MEDIUM


def _stage_sum(ex: QueryExecution, name: str) -> float:
    return float(sum(v for s in ex.stages if (v := getattr(s, name)) is not None))


# ---------------------------------------------------------------------------
# Skew + queue shapes (require real distributions)
# ---------------------------------------------------------------------------


def skew_signals(
    executions: list[QueryExecution],
    task_durations: dict[str, list[float]],
) -> list[SkewSignal]:
    """Skew from task-duration distributions keyed ``execution:stage``.

    ``task_durations`` carries raw task durations from the artifact
    (e.g. Spark eventlog tasks). No distribution -> no signal.
    """
    out: list[SkewSignal] = []
    ex_ids = {e.execution_id for e in executions}
    for key, durations in sorted(task_durations.items()):
        if len(durations) < 4:
            continue
        ordered = sorted(durations)
        median = ordered[len(ordered) // 2]
        if median <= 0:
            continue
        p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
        worst = ordered[-1]
        exec_id, _, stage = key.partition(":")
        if exec_id not in ex_ids:
            continue
        out.append(
            SkewSignal(
                execution=exec_id,
                stage=stage,
                max_ms=worst,
                median_ms=median,
                p95_ms=p95,
                ratio=round(worst / median, 3),
                evidence=(f"tasks:{len(durations)}", f"key:{key}"),
            )
        )
    return out


_QUEUE_MECHANISMS = {
    "snowflake": "warehouse_queue",
    "redshift": "wlm",
    "bigquery": "slot_wait",
    "trino": "resource_group",
}


def queue_pressure(executions: list[QueryExecution]) -> list[QueuePressure]:
    out: list[QueuePressure] = []
    for ex in executions:
        if ex.queue_time_ms is None or not ex.duration_ms:
            continue
        out.append(
            QueuePressure(
                engine=ex.engine,
                execution=ex.execution_id,
                queue_ms=ex.queue_time_ms,
                duration_ms=ex.duration_ms,
                share=round(ex.queue_time_ms / ex.duration_ms, 6),
                mechanism=_QUEUE_MECHANISMS.get(ex.engine, "queue"),
            )
        )
    return out


# ---------------------------------------------------------------------------
# PERF findings — policy-gated
# ---------------------------------------------------------------------------


def perf_findings(
    executions: list[QueryExecution],
    signals: list[PerformanceSignal],
    policy: PerfPolicy | None,
    skew: list[SkewSignal] | None = None,
) -> list[PerformanceFinding]:
    """Emit PERF001-PERF010 findings where policy bounds exist.

    No policy bound -> informational opportunity, never a warning built
    on a magic number.
    """
    out: list[PerformanceFinding] = []
    for sig in signals:
        fid = _FAMILY_FINDING.get(sig.family)
        if fid is None:
            continue
        check_id, title, pol_attr, cmp_fn = fid
        bound = getattr(policy, pol_attr, None) if policy else None
        sev = Severity.INFO
        fired = False
        if bound is not None and sig.value is not None:
            fired = cmp_fn(sig.value, bound)
            sev = Severity.WARNING if fired else Severity.INFO
        out.append(
            PerformanceFinding(
                check_id=check_id,
                title=title,
                severity=sev,
                message=(f"{title} on {sig.subject} ({sig.engine}): {sig.observed}; {sig.derived}"),
                observed=sig.observed,
                derived=sig.derived,
                threshold=(f"{pol_attr}={bound}" if bound is not None else "no configured bound"),
                evidence=sig.evidence,
                confidence=sig.confidence if fired else Confidence.LOW,
            )
        )
    # PERF004 — skew needs real distributions, not ratios
    for sk in skew or []:
        bound = policy.skew_ratio if policy else None
        fired = bound is not None and sk.ratio > bound
        out.append(
            PerformanceFinding(
                check_id="PERF004",
                title="Runtime skew confirmed",
                severity=Severity.WARNING if fired else Severity.INFO,
                message=(
                    f"{sk.execution}/{sk.stage}: max {sk.max_ms:.0f}ms vs "
                    f"median {sk.median_ms:.0f}ms (p95 {sk.p95_ms:.0f}ms, "
                    f"ratio {sk.ratio}x)"
                ),
                observed=(
                    f"task distribution n>=4, max={sk.max_ms:.0f}ms median={sk.median_ms:.0f}ms"
                ),
                derived=f"skew ratio {sk.ratio}x",
                threshold=f"skew_ratio={bound}" if bound is not None else "no configured bound",
                evidence=sk.evidence,
                confidence=Confidence.HIGH if fired else Confidence.MEDIUM,
            )
        )
    return sorted(out, key=lambda f: (f.severity is not Severity.WARNING, f.check_id, f.message))


def _gt(v: float, b: float) -> bool:
    return v > b


def _lt(v: float, b: float) -> bool:
    return v < b


_FAMILY_FINDING: dict[SignalFamily, tuple[str, str, str, Any]] = {
    SignalFamily.SCAN_AMPLIFICATION: (
        "PERF001",
        "High scan amplification",
        "scan_amplification",
        _gt,
    ),
    SignalFamily.POOR_PRUNING: ("PERF002", "Poor partition pruning", "pruning_min", _gt),
    SignalFamily.DATA_EXCHANGE_AMPLIFICATION: (
        "PERF003",
        "High data exchange",
        "exchange_amplification",
        _gt,
    ),
    SignalFamily.SPILL_PRESSURE: ("PERF005", "Spill pressure", "spill_ratio", _gt),
    SignalFamily.QUEUE_PRESSURE: ("PERF006", "Excessive queue time", "queue_share", _gt),
    SignalFamily.REMOTE_IO_AMPLIFICATION: (
        "PERF007",
        "Remote I/O bottleneck",
        "remote_io_ratio",
        _gt,
    ),
    SignalFamily.LOW_PARALLELISM: (
        "PERF008",
        "Low effective parallelism",
        "min_parallelism",
        _gt,
    ),
    SignalFamily.SMALL_FILE_AMPLIFICATION: (
        "PERF009",
        "Small-file runtime penalty",
        "small_file_bytes",
        _lt,
    ),
    SignalFamily.MATERIALIZATION_OVERHEAD: (
        "PERF010",
        "Repeated materialization overhead",
        "materialize_count",
        _gt,
    ),
}


# ---------------------------------------------------------------------------
# Baselines + trends (§12-15)
# ---------------------------------------------------------------------------


class RegressionClass(Enum):
    NEW = "new"
    IMPROVED = "improved"
    REGRESSED = "regressed"
    STABLE = "stable"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True)
class ExecutionTrend:
    """Per-fingerprint history of normalized executions."""

    fingerprint: str
    timestamps: tuple[float, ...] = ()
    durations: tuple[float, ...] = ()
    scan: tuple[float, ...] = ()
    shuffle: tuple[float, ...] = ()
    spill: tuple[float, ...] = ()
    queue: tuple[float, ...] = ()

    @property
    def runs(self) -> int:
        return len(self.timestamps)


def build_trends(executions: list[QueryExecution]) -> dict[str, ExecutionTrend]:
    """Group executions by fingerprint into trends (sorted by time)."""
    by_fp: dict[str, list[QueryExecution]] = {}
    for ex in executions:
        if not ex.query_fingerprint:
            continue
        by_fp.setdefault(ex.query_fingerprint, []).append(ex)
    out: dict[str, ExecutionTrend] = {}
    for fp, exs in by_fp.items():
        ordered = sorted(exs, key=lambda e: (e.start_time is None, e.start_time or 0))
        out[fp] = ExecutionTrend(
            fingerprint=fp,
            timestamps=tuple(e.start_time or 0.0 for e in ordered),
            durations=tuple(e.duration_ms or 0.0 for e in ordered),
            scan=tuple(e.bytes_read or 0.0 for e in ordered),
            shuffle=tuple(_stage_sum(e, "shuffle_bytes") for e in ordered),
            spill=tuple(e.spill_bytes or 0.0 for e in ordered),
            queue=tuple(e.queue_time_ms or 0.0 for e in ordered),
        )
    return out


def _median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    o = sorted(xs)
    n = len(o)
    return o[n // 2] if n % 2 else (o[n // 2 - 1] + o[n // 2]) / 2


def _p95(xs: list[float]) -> float:
    if not xs:
        return 0.0
    o = sorted(xs)
    return o[min(len(o) - 1, int(len(o) * 0.95))]


@dataclass(frozen=True)
class FingerprintBaseline:
    fingerprint: str
    median_duration: float
    p95_duration: float
    scan_bytes: float
    shuffle_bytes: float
    spill_bytes: float
    runs: int


def baseline(trend: ExecutionTrend) -> FingerprintBaseline:
    return FingerprintBaseline(
        fingerprint=trend.fingerprint,
        median_duration=_median(list(trend.durations)),
        p95_duration=_p95(list(trend.durations)),
        scan_bytes=_median(list(trend.scan)),
        shuffle_bytes=_median(list(trend.shuffle)),
        spill_bytes=_median(list(trend.spill)),
        runs=trend.runs,
    )


def classify_regression(
    execution: QueryExecution,
    base: FingerprintBaseline | None,
    multiplier: float = 1.5,
) -> RegressionClass:
    """Relative regression vs baseline — no arbitrary absolute limits.

    ``multiplier`` defaults to 1.5x baseline p95; callers feed contract/
    config values when they exist.
    """
    if base is None or base.runs < 3:
        return RegressionClass.NEW if base is None else RegressionClass.INSUFFICIENT_DATA
    dur = execution.duration_ms
    if dur is None or base.p95_duration <= 0:
        return RegressionClass.INSUFFICIENT_DATA
    if dur > base.p95_duration * multiplier:
        return RegressionClass.REGRESSED
    if dur < base.median_duration * 0.7:
        return RegressionClass.IMPROVED
    return RegressionClass.STABLE


# ---------------------------------------------------------------------------
# Data gravity (§20) — facts, never a score
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DataGravitySignal:
    logical_dataset: str
    physical_representations: tuple[str, ...]
    consumers: tuple[str, ...]
    clouds: tuple[str, ...]
    runtime_volume_bytes: float | None = None
    movement_frequency: float | None = None  # observed moves/day when exported


def data_gravity(graph: Any) -> list[DataGravitySignal]:
    """Fact-level gravity over the platform graph.

    A logical dataset's representations are graph entities sharing an
    abstraction/label; consumers are evidenced READS edges. No scoring.
    """
    out: list[DataGravitySignal] = []
    if graph is None:
        return out
    reps: dict[str, set[str]] = {}
    clouds: dict[str, set[str]] = {}
    consumers: dict[str, set[str]] = {}
    names: dict[str, str] = {}
    for e in graph.entities():
        name = e.attr("dataset") or e.attr("logical_dataset") or e.name or e.identifier
        names[e.id] = str(name)
        reps.setdefault(str(name), set()).add(e.id)
        if e.domain:
            clouds.setdefault(str(name), set()).add(e.domain)
    for rel in graph.relationships():
        if rel.kind.value in ("READS", "READS_FROM", "CONSUMES"):
            tgt_name = names.get(rel.dst)
            if tgt_name:
                consumers.setdefault(tgt_name, set()).add(rel.src)
    for name in sorted(reps):
        out.append(
            DataGravitySignal(
                logical_dataset=name,
                physical_representations=tuple(sorted(reps[name])),
                consumers=tuple(sorted(consumers.get(name, ()))),
                clouds=tuple(sorted(clouds.get(name, ()))),
            )
        )
    return out
