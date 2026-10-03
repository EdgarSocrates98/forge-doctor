"""Capacity / saturation intelligence (spec 246).

Detects signals of approaching limits over recorded execution history
and declared topology.  No probabilistic forecasting — a rising
utilization is shown as trend + headroom, and any extrapolation is a
"simple projection", never a "prediction".

Threshold provenance is mandatory and ordered:

    contract/config attr  >  platform pack  >  historical baseline

A dimension without any threshold source reports ``UNKNOWN`` — there is
no global "CPU > 80 = bad".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from statistics import median
from typing import Any

from forge_doctor.core.execution_history import ExecutionSeries
from forge_doctor.core.knowledge import load_pack


class CapacityDimension(Enum):
    CPU = "cpu"
    MEMORY = "memory"
    CONCURRENCY = "concurrency"
    SLOTS = "slots"
    WAREHOUSE_LOAD = "warehouse_load"
    SHARDS = "shards"
    PARTITIONS = "partitions"
    EXECUTORS = "executors"
    WORKERS = "workers"
    THROUGHPUT = "throughput"
    STORAGE = "storage"
    REQUEST_RATE = "request_rate"
    QUEUE = "queue"


class SaturationClass(Enum):
    HEALTHY = "healthy"
    ELEVATED = "elevated"
    SATURATED = "saturated"
    UNKNOWN = "unknown"


class ThresholdProvenance(Enum):
    CONFIG = "config"  # contract/config attr on the entity
    PACK = "pack"  # knowledge/capacity/<domain>.json
    BASELINE = "baseline"  # historical distribution of the series itself
    NONE = "none"


@dataclass(frozen=True)
class CapacityThreshold:
    """warn/p95 saturation cutoffs with mandatory provenance."""

    elevated: float  # usage/capacity ratio -> ELEVATED
    saturated: float  # -> SATURATED
    provenance: ThresholdProvenance
    source: str = ""  # attr name, pack id, or "baseline:p95"


@dataclass(frozen=True)
class CapacitySignal:
    """One resource dimension's current saturation state."""

    resource: str  # series_id / entity id
    dimension: CapacityDimension
    configured_capacity: float | None
    observed_usage: float | None  # latest observed value (native units)
    saturation: SaturationClass
    headroom: float | None  # capacity - usage (native units)
    history: tuple[float, ...]  # observed window (bounded)
    threshold: CapacityThreshold | None
    unit: str = ""
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource": self.resource,
            "dimension": self.dimension.value,
            "configured_capacity": self.configured_capacity,
            "observed_usage": self.observed_usage,
            "saturation": self.saturation.value,
            "headroom": self.headroom,
            "history": list(self.history),
            "unit": self.unit,
            "threshold": (
                {
                    "elevated": self.threshold.elevated,
                    "saturated": self.threshold.saturated,
                    "provenance": self.threshold.provenance.value,
                    "source": self.threshold.source,
                }
                if self.threshold
                else None
            ),
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class CapacityTrend:
    """Rising utilization: trend label + headroom + optional projection."""

    resource: str
    dimension: CapacityDimension
    direction: str  # rising | falling | flat | insufficient_data
    headroom: float | None
    unit: str
    # labelled "simple projection" — never "prediction"
    projection_label: str = "simple_projection"
    projected_saturation_at: float | None = None  # epoch ms estimate
    points: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource": self.resource,
            "dimension": self.dimension.value,
            "direction": self.direction,
            "headroom": self.headroom,
            "unit": self.unit,
            "projection_label": self.projection_label,
            "projected_saturation_at": self.projected_saturation_at,
            "points": self.points,
        }


# ---------------------------------------------------------------------------
# Thresholds — config > pack > baseline, provenance always attached
# ---------------------------------------------------------------------------

# attr names carrying configured capacity per dimension
_CAPACITY_ATTRS: dict[CapacityDimension, tuple[str, ...]] = {
    CapacityDimension.MEMORY: ("memory_mb", "memory_limit_mb", "max_memory_mb"),
    CapacityDimension.CPU: ("cpu_cores", "vcores", "slots"),
    CapacityDimension.CONCURRENCY: ("max_concurrency", "concurrency_limit"),
    CapacityDimension.SLOTS: ("slots", "max_slots"),
    CapacityDimension.EXECUTORS: ("executors", "max_executors"),
    CapacityDimension.WORKERS: ("workers", "instances", "replicas"),
    CapacityDimension.SHARDS: ("shards", "num_shards"),
    CapacityDimension.PARTITIONS: ("partition_count", "num_partitions"),
    CapacityDimension.STORAGE: ("storage_gb", "size_gb"),
    CapacityDimension.THROUGHPUT: ("throughput_limit", "max_throughput"),
    CapacityDimension.REQUEST_RATE: ("rate_limit", "max_rps"),
    CapacityDimension.WAREHOUSE_LOAD: ("max_cluster_count", "warehouse_size"),
    CapacityDimension.QUEUE: ("queue_limit", "max_queue_ms"),
}

# threshold ratio attrs that override the pack, e.g. ``saturation_elevated``
_THRESHOLD_ATTRS = ("saturation_elevated", "saturation_saturated")


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _capacity_pack(engine: str) -> dict[str, Any]:
    """``knowledge/capacity/<engine>.json`` — queue semantics + ratio
    defaults where the engine documents them.  Missing pack -> {}."""
    if not engine:
        return {}
    return load_pack("capacity", engine)


def threshold_for(
    dimension: CapacityDimension,
    attrs: dict[str, str] | None,
    engine: str,
    history_values: list[float],
) -> CapacityThreshold | None:
    """Resolve warn/saturated cutoffs with provenance.

    1. config attrs (``saturation_elevated``/``saturation_saturated`` or a
       ``*_capacity`` + implicit 0.8/0.95 ratios from config)
    2. platform pack ``saturation`` block
    3. baseline: p95/median of the series itself (elevated=1.5x p95 ratio
       over median, saturated=3x — a *distribution* rule, not a global)
    """
    attrs = attrs or {}
    pack = _capacity_pack(engine)
    pack_sat = pack.get("saturation", {}).get(dimension.value, {})

    elev = _num(attrs.get("saturation_elevated")) or _num(pack_sat.get("elevated"))
    sat = _num(attrs.get("saturation_saturated")) or _num(pack_sat.get("saturated"))
    if elev is not None and sat is not None:
        prov = (
            ThresholdProvenance.CONFIG
            if _num(attrs.get("saturation_elevated")) is not None
            else ThresholdProvenance.PACK
        )
        src = (
            "attr:saturation_elevated"
            if prov is ThresholdProvenance.CONFIG
            else f"pack:capacity/{engine}"
        )
        return CapacityThreshold(elev, sat, prov, src)

    # Baseline rule: relative to the series' own distribution.
    if len(history_values) >= 8:
        vals = sorted(history_values)
        med = median(vals)
        p95 = vals[int(0.95 * (len(vals) - 1))]
        if med > 0 and p95 > med:
            ratio = p95 / med
            return CapacityThreshold(
                elevated=1.5 * ratio,
                saturated=3.0 * ratio,
                provenance=ThresholdProvenance.BASELINE,
                source="baseline:p95/median",
            )
    return None


def configured_capacity(
    dimension: CapacityDimension, attrs: dict[str, str] | None
) -> tuple[float | None, str]:
    """Configured capacity for a dimension from entity attrs."""
    for key in _CAPACITY_ATTRS.get(dimension, ()):
        v = _num((attrs or {}).get(key))
        if v is not None:
            return v, f"attr:{key}"
    return None, ""


# ---------------------------------------------------------------------------
# Signals from series metrics
# ---------------------------------------------------------------------------

# dimension -> (series metric, unit)
_SERIES_METRIC: dict[CapacityDimension, tuple[str, str]] = {
    CapacityDimension.QUEUE: ("queue_time_ms", "ms"),
    CapacityDimension.MEMORY: ("memory_peak", "mb"),
    CapacityDimension.CPU: ("cpu_time_ms", "ms"),
    CapacityDimension.THROUGHPUT: ("rows_read", "rows"),
}


def _classify(ratio: float | None, th: CapacityThreshold | None) -> SaturationClass:
    if ratio is None or th is None:
        return SaturationClass.UNKNOWN
    if ratio >= th.saturated:
        return SaturationClass.SATURATED
    if ratio >= th.elevated:
        return SaturationClass.ELEVATED
    return SaturationClass.HEALTHY


def capacity_signals(
    series_map: dict[str, ExecutionSeries],
    attrs_by_resource: dict[str, dict[str, str]] | None = None,
) -> list[CapacitySignal]:
    """Saturation signals over history series + configured attrs.

    ``attrs_by_resource`` maps series subject -> configured attrs
    (capacity + threshold overrides); absent attrs never fabricate
    capacity — the signal stays UNKNOWN.
    """
    out: list[CapacitySignal] = []
    for sid in sorted(series_map):
        series = series_map[sid]
        attrs = (attrs_by_resource or {}).get(series.subject_id) or {}
        for dim, (metric, unit) in sorted(_SERIES_METRIC.items(), key=lambda kv: kv[0].value):
            values = series.metric_values(metric)
            if not values:
                continue
            window = tuple(values[-50:])  # bounded history
            observed = values[-1]
            capacity, cap_src = configured_capacity(dim, attrs)
            th = threshold_for(dim, attrs, series.engine, values)
            ratio: float | None = None
            if capacity and observed is not None:
                ratio = observed / capacity
            elif (
                th is not None
                and th.provenance is ThresholdProvenance.BASELINE
                and observed is not None
                and len(values) >= 8
            ):
                med = median(sorted(values))
                ratio = observed / med if med else None
            out.append(
                CapacitySignal(
                    resource=sid,
                    dimension=dim,
                    configured_capacity=capacity,
                    observed_usage=observed,
                    saturation=_classify(ratio, th),
                    headroom=(capacity - observed)
                    if capacity is not None and observed is not None
                    else None,
                    history=window,
                    threshold=th,
                    unit=unit,
                    evidence=tuple(e for e in (f"series:{sid}", cap_src) if e),
                )
            )
    return out


def capacity_trends(
    signals: list[CapacitySignal],
    series_map: dict[str, ExecutionSeries],
) -> list[CapacityTrend]:
    """Trend + headroom per signal with timestamped history."""
    out: list[CapacityTrend] = []
    for sig in signals:
        series = series_map.get(sig.resource)
        if series is None:
            continue
        metric = _SERIES_METRIC.get(sig.dimension)
        if metric is None:
            continue
        ts = series.metric_series(metric[0])
        pts = [(p.timestamp, p.value) for p in ts.points]
        if len(pts) < 3:
            direction = "insufficient_data"
            proj = None
        else:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            x0, x1 = xs[0], xs[-1]
            y0, y1 = ys[0], ys[-1]
            med = median(ys)
            rising = y1 > y0 * 1.2 and y1 > med * 1.1
            falling = y1 < y0 * 0.8 and y1 < med * 0.9
            direction = "rising" if rising else "falling" if falling else "flat"
            proj = None
            if (
                direction == "rising"
                and sig.configured_capacity
                and sig.observed_usage is not None
                and x1 > x0
                and y1 > y0
            ):
                # linear extrapolation to capacity — "simple projection"
                slope = (y1 - y0) / (x1 - x0)
                if slope > 0:
                    proj = x1 + (sig.configured_capacity - y1) / slope
        out.append(
            CapacityTrend(
                resource=sig.resource,
                dimension=sig.dimension,
                direction=direction,
                headroom=sig.headroom,
                unit=sig.unit,
                projected_saturation_at=proj,
                points=len(pts),
            )
        )
    return out


# ---------------------------------------------------------------------------
# CAP001-007 findings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CapacityFinding:
    check_id: str
    severity: str  # warning | info
    resource: str
    message: str
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "severity": self.severity,
            "resource": self.resource,
            "message": self.message,
            "evidence": list(self.evidence),
        }


# dimension -> saturation finding id
_DIM_FINDING: dict[CapacityDimension, str] = {
    CapacityDimension.QUEUE: "CAP001",  # queue saturation
    CapacityDimension.MEMORY: "CAP002",  # memory saturation
    CapacityDimension.PARTITIONS: "CAP003",  # storage-layout saturation
    CapacityDimension.SHARDS: "CAP003",
    CapacityDimension.STORAGE: "CAP003",
    CapacityDimension.WORKERS: "CAP004",  # worker saturation
    CapacityDimension.EXECUTORS: "CAP004",
    CapacityDimension.CONCURRENCY: "CAP005",  # concurrency saturation
    CapacityDimension.SLOTS: "CAP005",
    CapacityDimension.WAREHOUSE_LOAD: "CAP005",
    CapacityDimension.REQUEST_RATE: "CAP005",
}
CAP006 = "CAP006"  # capacity trend increasing
CAP007 = "CAP007"  # low headroom on critical workload


def capacity_findings(
    signals: list[CapacitySignal],
    trends: list[CapacityTrend],
    critical_resources: set[str] | None = None,
) -> list[CapacityFinding]:
    """CAP001-007 — one finding per saturated/elevated dimension or trend."""
    out: list[CapacityFinding] = []
    critical = critical_resources or set()
    for s in signals:
        fid = _DIM_FINDING.get(s.dimension)
        if (
            fid
            and s.saturation in (SaturationClass.SATURATED, SaturationClass.ELEVATED)
            and s.observed_usage is not None
        ):
            cap = (
                f"{s.configured_capacity}{s.unit}"
                if s.configured_capacity is not None
                else "baseline"
            )
            out.append(
                CapacityFinding(
                    fid,
                    "warning" if s.saturation is SaturationClass.SATURATED else "info",
                    s.resource,
                    f"{s.dimension.value} {s.saturation.value}: "
                    f"{s.observed_usage}{s.unit} vs {cap}"
                    + (f" (headroom {s.headroom}{s.unit})" if s.headroom is not None else ""),
                    s.evidence,
                )
            )
        if fid and s.saturation is SaturationClass.UNKNOWN and s.observed_usage is not None:
            out.append(
                CapacityFinding(
                    fid,
                    "info",
                    s.resource,
                    f"{s.dimension.value} usage observed "
                    f"({s.observed_usage}{s.unit}) but no configured/pack/"
                    f"baseline threshold — saturation unverifiable",
                    s.evidence,
                )
            )
    for t in trends:
        if t.direction == "rising":
            headroom_txt = f" headroom={t.headroom}{t.unit}" if t.headroom is not None else ""
            proj_txt = (
                f" simple projection: saturates ~{t.projected_saturation_at:.0f}"
                if t.projected_saturation_at is not None
                else ""
            )
            out.append(
                CapacityFinding(
                    CAP006,
                    "info",
                    t.resource,
                    f"{t.dimension.value} utilization rising over "
                    f"{t.points} points.{headroom_txt}{proj_txt}",
                    (f"series:{t.resource}",),
                )
            )
    for s in signals:
        if (
            s.resource in critical
            and s.headroom is not None
            and s.configured_capacity
            and s.headroom / s.configured_capacity < 0.2
        ):
            out.append(
                CapacityFinding(
                    CAP007,
                    "warning",
                    s.resource,
                    f"low headroom on critical workload: "
                    f"{s.headroom}{s.unit} of {s.configured_capacity}{s.unit} "
                    f"({s.dimension.value})",
                    s.evidence,
                )
            )
    return out
