"""Experiment / simulation intelligence 2.0 (spec 240, program P phase 6).

Extends ``core/experiments.py`` (findings-diff verdicts) with measured
behavior comparison over evidence bundles: two exported artifact
bundles (before/after) are ingested as ``QueryExecution`` rows and
compared on declared metrics. Verdicts derive only from measured
metrics + declared expectations — no runtime is ever executed.

A protected-constraint breach overrides benefit: an experiment that
improves the target metric while violating a declared constraint is
CONSTRAINT_VIOLATED, not SUPPORTED.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from forge_doctor_data.core.execution_model import (
    ExecutionStage,
    ExecutionStatus,
    QueryExecution,
    StageKind,
    derive_metrics,
)


class ExperimentMetric(Enum):
    DURATION = "duration_ms"
    SCAN_BYTES = "scan_bytes"
    SHUFFLE_BYTES = "shuffle_bytes"
    SPILL_BYTES = "spill_bytes"
    FILE_COUNT = "file_count"
    MEDIAN_FILE_SIZE = "median_file_size"
    QUEUE_TIME = "queue_time_ms"
    FRESHNESS = "freshness_seconds"
    THROUGHPUT = "throughput_rows_per_s"
    ERROR_COUNT = "error_count"


class ExperimentVerdict(Enum):
    SUPPORTED = "supported"
    NOT_SUPPORTED = "not_supported"
    INCONCLUSIVE = "inconclusive"
    CONSTRAINT_VIOLATED = "constraint_violated"


@dataclass(frozen=True)
class ExperimentPlanV2:
    """A measured-behavior experiment: hypothesis + declared expectations.

    ``expected_effects`` entries: ``"<metric>:decrease"`` /
    ``"<metric>:increase"`` optionally with ``:<min_frac>`` (e.g.
    ``"scan_bytes:decrease:0.5"`` = after <= baseline*0.5).
    ``protected_constraints`` entries: ``"<metric>:<=:<value>"`` /
    ``"<metric>:>=:<value>"`` evaluated on the after bundle.
    ``acceptance`` is the fraction of expected effects that must hold
    (default 1.0).
    """

    hypothesis: str
    target: str = ""
    change: str = ""
    workload: str = ""
    baseline_metrics: tuple[str, ...] = ()
    expected_effects: tuple[str, ...] = ()
    protected_constraints: tuple[str, ...] = ()
    measured_metrics: tuple[str, ...] = ()
    acceptance: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis": self.hypothesis,
            "target": self.target,
            "change": self.change,
            "workload": self.workload,
            "baseline_metrics": list(self.baseline_metrics),
            "expected_effects": list(self.expected_effects),
            "protected_constraints": list(self.protected_constraints),
            "measured_metrics": list(self.measured_metrics),
            "acceptance": self.acceptance,
        }


@dataclass(frozen=True)
class MetricComparison:
    metric: str
    before: float | None
    after: float | None
    direction_ok: bool | None  # None when unmeasurable

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "before": self.before,
            "after": self.after,
            "direction_ok": self.direction_ok,
        }


@dataclass
class ExperimentReportV2:
    """Verdict + per-metric comparisons + reasons."""

    plan: ExperimentPlanV2
    verdict: ExperimentVerdict
    comparisons: list[MetricComparison] = field(default_factory=list)
    constraint_results: list[dict[str, Any]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan.to_dict(),
            "verdict": self.verdict.value,
            "comparisons": [c.to_dict() for c in self.comparisons],
            "constraint_results": self.constraint_results,
            "reasons": self.reasons,
        }


# ---------------------------------------------------------------------------
# Measurement — aggregate execution bundles into declared metrics
# ---------------------------------------------------------------------------


def measure_bundle(executions: list[QueryExecution]) -> dict[str, float]:
    """Aggregate declared metrics over a bundle. Absent = not measured."""
    out: dict[str, float] = {}
    durations = [e.duration_ms for e in executions if e.duration_ms is not None]
    if durations:
        out[ExperimentMetric.DURATION.value] = float(sum(durations)) / len(durations)
    scans = [e.bytes_read for e in executions if e.bytes_read is not None]
    if scans:
        out[ExperimentMetric.SCAN_BYTES.value] = float(sum(scans))
    shuffle = [
        sum(s.shuffle_bytes for s in e.stages if s.shuffle_bytes is not None)
        for e in executions
        if any(s.shuffle_bytes is not None for s in e.stages)
    ]
    if shuffle:
        out[ExperimentMetric.SHUFFLE_BYTES.value] = float(sum(shuffle))
    spills = [e.spill_bytes for e in executions if e.spill_bytes is not None]
    if spills:
        out[ExperimentMetric.SPILL_BYTES.value] = float(sum(spills))
    queues = [e.queue_time_ms for e in executions if e.queue_time_ms is not None]
    if queues:
        out[ExperimentMetric.QUEUE_TIME.value] = float(sum(queues)) / len(queues)
    files = [
        sc.files_scanned
        for e in executions
        for s in e.stages
        for sc in s.scans
        if sc.files_scanned is not None
    ]
    if files:
        out[ExperimentMetric.FILE_COUNT.value] = float(sum(files))
        file_sizes = []
        for e in executions:
            for s in e.stages:
                for sc in s.scans:
                    if sc.files_scanned and sc.bytes_scanned:
                        file_sizes.append(sc.bytes_scanned / sc.files_scanned)
        if file_sizes:
            file_sizes.sort()
            out[ExperimentMetric.MEDIAN_FILE_SIZE.value] = file_sizes[len(file_sizes) // 2]
    rows = [e.rows_read for e in executions if e.rows_read is not None]
    if rows and durations:
        secs = sum(durations) / 1000.0
        if secs > 0:
            out[ExperimentMetric.THROUGHPUT.value] = sum(rows) / secs
    errors = sum(1 for e in executions if e.status is ExecutionStatus.FAILED)
    if executions:
        out[ExperimentMetric.ERROR_COUNT.value] = float(errors)
    return out


# ---------------------------------------------------------------------------
# Verdict — measured metrics vs declared expectations
# ---------------------------------------------------------------------------

_OP_CHARS = ("<=", ">=", "<", ">", "=")


def _parse_effect(spec: str) -> tuple[str, str, float | None]:
    """``"<metric>:decrease[:frac]"`` / ``"<metric>:increase[:frac]"``."""
    parts = spec.split(":")
    metric = parts[0].strip()
    direction = parts[1].strip().lower() if len(parts) > 1 else "decrease"
    frac = None
    if len(parts) > 2:
        try:
            frac = float(parts[2])
        except ValueError:
            frac = None
    return metric, direction, frac


def _parse_constraint(spec: str) -> tuple[str, str, float] | None:
    """``"<metric>:<=:<value>"`` etc.; None when unparseable."""
    for op in _OP_CHARS:
        token = f":{op}:"
        if token in spec:
            metric, _, raw = spec.partition(token)
            try:
                return metric.strip(), op, float(raw)
            except ValueError:
                return None
    return None


def _holds(op: str, after: float, bound: float) -> bool:
    return {
        "<=": after <= bound,
        ">=": after >= bound,
        "<": after < bound,
        ">": after > bound,
        "=": after == bound,
    }[op]


def evaluate(
    plan: ExperimentPlanV2,
    before: list[QueryExecution],
    after: list[QueryExecution],
) -> ExperimentReportV2:
    """Compare measured bundles against declared expectations."""
    mb = measure_bundle(before)
    ma = measure_bundle(after)
    report = ExperimentReportV2(plan=plan, verdict=ExperimentVerdict.INCONCLUSIVE)

    # Protected constraints first — a breach overrides any benefit.
    violated = False
    for spec in plan.protected_constraints:
        parsed = _parse_constraint(spec)
        if parsed is None:
            report.constraint_results.append({"spec": spec, "status": "unparseable"})
            continue
        metric, op, bound = parsed
        observed = ma.get(metric)
        if observed is None:
            report.constraint_results.append(
                {"spec": spec, "status": "unmeasured", "observed": None}
            )
            continue
        holds = _holds(op, observed, bound)
        report.constraint_results.append(
            {"spec": spec, "status": "ok" if holds else "violated", "observed": observed}
        )
        if not holds:
            violated = True
            report.reasons.append(f"protected constraint violated: {spec} (observed {observed})")

    satisfied = 0
    measured = 0
    for spec in plan.expected_effects:
        metric, direction, frac = _parse_effect(spec)
        b = mb.get(metric)
        a = ma.get(metric)
        ok: bool | None = None
        if b is not None and a is not None:
            measured += 1
            if direction == "decrease":
                bound = b * frac if frac is not None else b
                ok = a < b and a <= bound if frac is not None else a < b
            else:
                bound = b * frac if frac is not None else b
                ok = a > b and a >= bound if frac is not None else a > b
            if ok:
                satisfied += 1
        report.comparisons.append(
            MetricComparison(metric=metric, before=b, after=a, direction_ok=ok)
        )

    if violated:
        report.verdict = ExperimentVerdict.CONSTRAINT_VIOLATED
    elif measured == 0:
        report.verdict = ExperimentVerdict.INCONCLUSIVE
        report.reasons.append("no expected metric was measurable in both bundles")
    else:
        needed = max(1, int(len(plan.expected_effects) * plan.acceptance + 0.999))
        if satisfied >= needed:
            report.verdict = ExperimentVerdict.SUPPORTED
        else:
            report.verdict = ExperimentVerdict.NOT_SUPPORTED
            report.reasons.append(
                f"{satisfied}/{len(plan.expected_effects)} expected effects held "
                f"(acceptance={plan.acceptance})"
            )
    return report


# ---------------------------------------------------------------------------
# Evidence bundles — directories of exported artifacts, never executed
# ---------------------------------------------------------------------------


def load_bundle(root: Path) -> list[QueryExecution]:
    """Ingest every recognized execution artifact under ``root``."""
    from forge_doctor_data.analyzers.execution_adapters import ingest_executions

    out: list[QueryExecution] = []
    for f in sorted(root.rglob("*")):
        if not f.is_file():
            continue
        try:
            _, exs = ingest_executions(f)
        except (OSError, ValueError):
            continue
        out.extend(exs)
    return out


def compare_bundles(
    plan: ExperimentPlanV2,
    before_dir: Path,
    after_dir: Path,
) -> ExperimentReportV2:
    """Load two artifact bundles and evaluate the plan."""
    return evaluate(plan, load_bundle(before_dir), load_bundle(after_dir))


# ---------------------------------------------------------------------------
# Synthetic workloads — deterministic (seeded), fixture-level
# ---------------------------------------------------------------------------

WORKLOAD_KINDS: tuple[str, ...] = (
    "uniform",
    "skewed",
    "burst",
    "late_data",
    "high_cardinality",
    "many_small_files",
    "hot_key",
    "wide_row",
    "deep_nesting",
    "high_fanout",
)


def synthetic_workload(
    kind: str,
    n: int = 100,
    seed: int = 0,
    engine: str = "synthetic",
) -> list[QueryExecution]:
    """Deterministic fixture-level executions for a named workload shape.

    Only distributions differ per kind; the output is plain
    ``QueryExecution`` rows so downstream metrics/signals behave
    identically to real exports.
    """
    if kind not in WORKLOAD_KINDS:
        raise KeyError(f"unknown workload '{kind}' (known: {', '.join(WORKLOAD_KINDS)})")
    rng = random.Random(f"{kind}:{seed}:{n}")
    out: list[QueryExecution] = []
    for i in range(n):
        duration = _workload_duration(kind, rng, i)
        scan_b = _workload_scan_bytes(kind, rng, i)
        shuffle = _workload_shuffle(kind, rng, i)
        spill = _workload_spill(kind, rng, i)
        files = _workload_files(kind, rng, i)
        queue = _workload_queue(kind, rng, i)
        stage = ExecutionStage(
            id=f"s{i}",
            kind=StageKind.SCAN if shuffle is None else StageKind.EXCHANGE,
            duration_ms=duration,
            input_bytes=scan_b,
            shuffle_bytes=shuffle,
            spill_bytes=spill,
            scans=(
                _scan_for(kind, scan_b, files, rng)
                if scan_b is not None and files is not None
                else ()
            ),
        )
        ex = QueryExecution(
            execution_id=f"{kind}-{seed}-{i}",
            engine=engine,
            query_id=f"q{i}",
            duration_ms=duration,
            queue_time_ms=queue,
            status=ExecutionStatus.COMPLETED,
            stages=(stage,),
            bytes_read=scan_b,
            spill_bytes=spill,
            evidence=(f"synthetic:{kind}:{seed}",),
        )
        out.append(_attach_metrics(ex))
    return out


def _attach_metrics(ex: QueryExecution) -> QueryExecution:
    import dataclasses

    return dataclasses.replace(ex, metrics=derive_metrics(ex))


def _scan_for(
    kind: str, scan_b: float | None, files: float | None, rng: random.Random
) -> tuple[Any, ...]:
    from forge_doctor_data.core.execution_model import ExecutionScan

    return (
        ExecutionScan(
            source=f"{kind}-source",
            bytes_scanned=scan_b,
            files_scanned=files,
        ),
    )


def _workload_duration(kind: str, rng: random.Random, i: int) -> float:
    if kind == "burst":
        return rng.uniform(50, 200) if i % 10 else rng.uniform(2000, 5000)
    if kind == "skewed":
        return rng.lognormvariate(7.0, 1.2)
    return rng.uniform(200, 800)


def _workload_scan_bytes(kind: str, rng: random.Random, i: int) -> float:
    if kind == "wide_row":
        return rng.uniform(5e8, 2e9)
    return rng.uniform(1e7, 1e8)


def _workload_shuffle(kind: str, rng: random.Random, i: int) -> float | None:
    if kind in ("high_fanout", "skewed"):
        return rng.uniform(1e8, 1e9)
    return None


def _workload_spill(kind: str, rng: random.Random, i: int) -> float | None:
    if kind == "hot_key":
        return rng.uniform(1e8, 5e8)
    return None


def _workload_files(kind: str, rng: random.Random, i: int) -> float | None:
    if kind == "many_small_files":
        return rng.uniform(200, 2000)
    return rng.uniform(4, 40)


def _workload_queue(kind: str, rng: random.Random, i: int) -> float | None:
    if kind == "burst":
        return rng.uniform(500, 3000)
    return None
