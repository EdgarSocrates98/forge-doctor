"""Performance regression intelligence (spec 242, program Q phase 2).

Detects when behavior worsens relative to a baseline — deterministic
rules only:

- current > historical p95
- current median > baseline median * configured factor
- |current - median| > k * MAD (robust deviation)

Threshold precedence: explicit contract > named baseline > historical
baseline > knowledge pack (``knowledge/performance/regression.json``).
No global magic number fires anywhere.

correlation != causation.  A single slow run is a *candidate*
regression — persistence classes decide whether the pattern is
ONE_OFF, BURST, PERSISTENT, RECOVERED or FLAPPING.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from forge_doctor_data.core.execution_history import (
    ExecutionSeries,
    baseline_for,
)
from forge_doctor_data.core.models import Confidence, Severity
from forge_doctor_data.core.performance import (
    PerformanceFinding,
    RegressionClass,
)
from forge_doctor_data.core.trends import median


class RegressionDimension(Enum):
    """One comparable metric axis (spec §2.2)."""

    DURATION = "duration"
    QUEUE = "queue"
    SCAN = "scan"
    SHUFFLE = "shuffle"
    SPILL = "spill"
    MEMORY = "memory"
    CPU = "cpu"
    FRESHNESS = "freshness"
    ERROR_RATE = "error_rate"
    THROUGHPUT = "throughput"


# dimension -> ExecutionSample attribute (None = derived below)
_DIM_FIELD: dict[RegressionDimension, str] = {
    RegressionDimension.DURATION: "duration_ms",
    RegressionDimension.QUEUE: "queue_time_ms",
    RegressionDimension.SCAN: "bytes_read",
    RegressionDimension.SHUFFLE: "shuffle_bytes",
    RegressionDimension.SPILL: "spill_bytes",
    RegressionDimension.MEMORY: "memory_peak",
    RegressionDimension.CPU: "cpu_time_ms",
    RegressionDimension.FRESHNESS: "freshness_s",
}

# lower value = better (direction-aware detection)
_LOWER_IS_BETTER: frozenset[RegressionDimension] = frozenset({RegressionDimension.THROUGHPUT})


class RegressionConfidence(Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class PersistenceClass(Enum):
    """Episode shape over the flag sequence (spec §2.9)."""

    ONE_OFF = "one_off"  # single breached run — candidate only
    BURST = "burst"  # short breach streak still open
    PERSISTENT = "persistent"  # 3+ consecutive breaches at the tail
    RECOVERED = "recovered"  # breached earlier, returned to baseline
    FLAPPING = "flapping"  # bad/good oscillation


@dataclass(frozen=True)
class RegressionSignal:
    """One dimension of one series vs its baseline."""

    subject: str
    dimension: RegressionDimension
    klass: RegressionClass
    current: float | None
    baseline_value: float | None
    basis: str  # which rule fired / why not
    confidence: RegressionConfidence = RegressionConfidence.UNKNOWN
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class RegressionEpisode:
    """A bounded regression window in a series (spec §2.7)."""

    subject: str
    dimension: RegressionDimension
    start: float | None
    end: float | None
    persistence: PersistenceClass
    breaches: int
    baseline_median: float | None
    current_median: float | None
    confidence: RegressionConfidence = RegressionConfidence.UNKNOWN
    related_changes: tuple[str, ...] = ()  # filled by spec-243 layer

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "dimension": self.dimension.value,
            "start": self.start,
            "end": self.end,
            "persistence": self.persistence.value,
            "breaches": self.breaches,
            "baseline_median": self.baseline_median,
            "current_median": self.current_median,
            "confidence": self.confidence.value,
            "related_changes": list(self.related_changes),
        }


@dataclass(frozen=True)
class RegressionPolicy:
    """Detection bounds — all explicit, pack-backed (spec §2.3-2.4)."""

    median_factor: float = 1.5  # current median > baseline median * f
    mad_k: float = 3.5  # |current - median| > k * MAD
    volatility_ratio: float = 0.5  # MAD/median above -> VOLATILE
    improve_factor: float = 0.7  # current < median * f -> IMPROVED
    min_baseline_samples: int = 3
    persistent_tail: int = 3  # consecutive breaches -> PERSISTENT

    @classmethod
    def defaults(cls) -> RegressionPolicy:
        try:
            from forge_doctor_data.core.knowledge import load_pack

            pack = load_pack("performance", "regression")
            thresholds = pack.get("thresholds", {})
            if isinstance(thresholds, dict) and thresholds:
                fields = {
                    "median_factor",
                    "mad_k",
                    "volatility_ratio",
                    "improve_factor",
                    "min_baseline_samples",
                    "persistent_tail",
                }
                vals: dict[str, float] = {}
                for k, v in thresholds.items():
                    if k not in fields or not isinstance(v, dict):
                        continue
                    n = _num(v.get("value"))
                    if n is not None:
                        vals[k] = n
                int_keys = {"min_baseline_samples", "persistent_tail"}
                kwargs: dict[str, float | int] = {
                    k: (int(v) if k in int_keys else v) for k, v in vals.items()
                }
                return cls(**kwargs)  # type: ignore[arg-type]
        except Exception:
            pass
        return cls()


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _confidence_for(series: ExecutionSeries, samples_used: int) -> RegressionConfidence:
    b = baseline_for(series)
    base = {"high": "high", "medium": "medium", "low": "low"}.get(b.confidence, "unknown")
    if samples_used < 2 and base != "unknown":
        return RegressionConfidence.LOW
    try:
        return RegressionConfidence(base)
    except ValueError:
        return RegressionConfidence.UNKNOWN


def _dimension_values(series: ExecutionSeries, dim: RegressionDimension) -> list[float | None]:
    """Ordered observed values for a dimension — derived dims computed."""
    if dim is RegressionDimension.ERROR_RATE:
        # fraction of failed executions over trailing window — per-sample
        # value is 1.0 (failed) or 0.0 (ok); baseline uses the mean.
        return [1.0 if s.status in ("failed", "error") else 0.0 for s in series.samples]
    if dim is RegressionDimension.THROUGHPUT:
        out: list[float | None] = []
        for s in series.samples:
            rows = s.rows_written or s.rows_read
            if rows is not None and s.duration_ms and s.duration_ms > 0:
                out.append(rows / (s.duration_ms / 1000.0))
            else:
                out.append(None)
        return out
    field_name = _DIM_FIELD[dim]
    return [getattr(s, field_name) for s in series.samples]


def detect_dimension(
    series: ExecutionSeries,
    dim: RegressionDimension,
    policy: RegressionPolicy | None = None,
) -> RegressionSignal:
    """Latest-window value vs baseline for one dimension."""
    pol = policy or RegressionPolicy.defaults()
    evidence = (f"series:{series.series_id}",)
    values = _dimension_values(series, dim)
    observed = [v for v in values if v is not None]

    # Baseline over all-but-latest samples; latest window is the tail.
    baseline_vals = observed[:-1]
    current_vals = observed[-3:]
    if len(baseline_vals) < pol.min_baseline_samples:
        return RegressionSignal(
            subject=series.subject_id,
            dimension=dim,
            klass=RegressionClass.INSUFFICIENT_DATA,
            current=median(current_vals) if current_vals else None,
            baseline_value=None,
            basis=f"baseline samples={len(baseline_vals)} < {pol.min_baseline_samples}",
            confidence=RegressionConfidence.UNKNOWN,
            evidence=evidence,
        )

    b_med = median(baseline_vals)
    b_p95 = _pct(baseline_vals, 0.95)
    b_mad = _mad(baseline_vals)
    cur = median(current_vals)
    if b_med is None or cur is None:
        return RegressionSignal(
            subject=series.subject_id,
            dimension=dim,
            klass=RegressionClass.INSUFFICIENT_DATA,
            current=cur,
            baseline_value=b_med,
            basis="no measured values for dimension",
            confidence=RegressionConfidence.UNKNOWN,
            evidence=evidence,
        )

    # Volatility first: high relative spread is its own signal.
    if b_mad is not None and b_med and b_mad / abs(b_med) > pol.volatility_ratio:
        klass = RegressionClass.VOLATILE
        basis = f"mad/median={b_mad / abs(b_med):.2f} > {pol.volatility_ratio}"
        return RegressionSignal(
            subject=series.subject_id,
            dimension=dim,
            klass=klass,
            current=cur,
            baseline_value=b_med,
            basis=basis,
            confidence=_confidence_for(series, len(observed)),
            evidence=evidence,
        )

    worse = cur > b_med if dim not in _LOWER_IS_BETTER else cur < b_med
    if worse:
        if dim in _LOWER_IS_BETTER:
            breached = (
                (b_p95 is not None and cur < b_p95)
                or cur < b_med * (1 / pol.median_factor if pol.median_factor else 1)
                or (b_mad is not None and b_med - cur > pol.mad_k * b_mad and b_mad > 0)
            )
            basis = f"current {cur:.3g} << baseline median {b_med:.3g}"
        else:
            breached = (
                (b_p95 is not None and cur > b_p95)
                or cur > b_med * pol.median_factor
                or (b_mad is not None and cur - b_med > pol.mad_k * b_mad and b_mad > 0)
            )
            basis = f"current {cur:.3g} > baseline (median {b_med:.3g}, p95 {b_p95})"
        if breached:
            return RegressionSignal(
                subject=series.subject_id,
                dimension=dim,
                klass=RegressionClass.REGRESSED,
                current=cur,
                baseline_value=b_med,
                basis=basis,
                confidence=_confidence_for(series, len(observed)),
                evidence=evidence,
            )
    if dim not in _LOWER_IS_BETTER and cur < b_med * pol.improve_factor:
        return RegressionSignal(
            subject=series.subject_id,
            dimension=dim,
            klass=RegressionClass.IMPROVED,
            current=cur,
            baseline_value=b_med,
            basis=f"current {cur:.3g} < {pol.improve_factor}x median {b_med:.3g}",
            confidence=_confidence_for(series, len(observed)),
            evidence=evidence,
        )
    return RegressionSignal(
        subject=series.subject_id,
        dimension=dim,
        klass=RegressionClass.STABLE,
        current=cur,
        baseline_value=b_med,
        basis="within baseline bounds",
        confidence=_confidence_for(series, len(observed)),
        evidence=evidence,
    )


def _pct(xs: list[float], q: float) -> float | None:
    from forge_doctor_data.core.trends import percentile

    return percentile(xs, q)


def _mad(xs: list[float]) -> float | None:
    from forge_doctor_data.core.trends import mad

    return mad(xs)


def detect_regressions(
    series_map: dict[str, ExecutionSeries],
    policy: RegressionPolicy | None = None,
    dimensions: tuple[RegressionDimension, ...] | None = None,
) -> list[RegressionSignal]:
    """All series x dimensions — deterministic order."""
    dims = dimensions or tuple(RegressionDimension)
    out: list[RegressionSignal] = []
    for sid in sorted(series_map):
        for dim in dims:
            sig = detect_dimension(series_map[sid], dim, policy)
            if sig.klass is not RegressionClass.INSUFFICIENT_DATA:
                out.append(sig)
    return sorted(out, key=lambda s: (s.subject, s.dimension.value))


# ---------------------------------------------------------------------------
# Episodes & persistence (§2.7-2.9, §78-79)
# ---------------------------------------------------------------------------


def _breach_flags(
    series: ExecutionSeries, dim: RegressionDimension, pol: RegressionPolicy
) -> list[bool]:
    """Per-sample breach flags vs the all-sample baseline median."""
    values = _dimension_values(series, dim)
    observed = [v for v in values if v is not None]
    b_med = median(observed)
    if b_med is None or b_med == 0:
        return []
    flags: list[bool] = []
    for v in values:
        if v is None:
            flags.append(False)
            continue
        if dim in _LOWER_IS_BETTER:
            flags.append(v < b_med / pol.median_factor)
        else:
            flags.append(v > b_med * pol.median_factor)
    return flags


def classify_persistence(flags: list[bool]) -> PersistenceClass | None:
    """Breach sequence -> episode shape.

    Requires a trailing-run rule so a single slow run is a candidate
    (ONE_OFF), never a persistent regression.
    """
    if not flags or not any(flags):
        return None
    trailing = 0
    for f in reversed(flags):
        if not f:
            break
        trailing += 1
    from itertools import pairwise

    transitions = sum(1 for a, b in pairwise(flags) if a != b)
    if flags[-1]:
        if trailing >= 3:
            return PersistenceClass.PERSISTENT
        if sum(flags) == 1:
            return PersistenceClass.ONE_OFF
        return PersistenceClass.BURST
    # ends inside baseline
    if transitions >= 3:
        return PersistenceClass.FLAPPING
    return PersistenceClass.RECOVERED


def episodes(
    series: ExecutionSeries,
    dim: RegressionDimension,
    policy: RegressionPolicy | None = None,
) -> list[RegressionEpisode]:
    """Regression episode(s) over one series+dimension."""
    pol = policy or RegressionPolicy.defaults()
    flags = _breach_flags(series, dim, pol)
    persistence = classify_persistence(flags)
    if persistence is None:
        return []
    values = _dimension_values(series, dim)
    observed = [v for v in values if v is not None]
    b_med = median(observed[:-3] if len(observed) > 3 else observed)
    tail = observed[-3:]
    stamps = [s.timestamp for s in series.samples]
    first_breach = next(i for i, f in enumerate(flags) if f)
    last_breach = max(i for i, f in enumerate(flags) if f)
    return [
        RegressionEpisode(
            subject=series.subject_id,
            dimension=dim,
            start=stamps[first_breach],
            end=stamps[last_breach],
            persistence=persistence,
            breaches=sum(flags),
            baseline_median=b_med,
            current_median=median(tail) if tail else None,
            confidence=_confidence_for(series, len(observed)),
        )
    ]


# ---------------------------------------------------------------------------
# PERFREG findings (§2.6) — PerformanceFinding carrier
# ---------------------------------------------------------------------------

_DIM_FINDING: dict[RegressionDimension, tuple[str, str]] = {
    RegressionDimension.DURATION: ("PERFREG001", "Duration regression"),
    RegressionDimension.QUEUE: ("PERFREG002", "Queue regression"),
    RegressionDimension.SCAN: ("PERFREG003", "Scan regression"),
    RegressionDimension.SHUFFLE: ("PERFREG004", "Shuffle regression"),
    RegressionDimension.SPILL: ("PERFREG005", "Spill regression"),
    RegressionDimension.MEMORY: ("PERFREG006", "Memory regression"),
    RegressionDimension.FRESHNESS: ("PERFREG007", "Freshness regression"),
    RegressionDimension.THROUGHPUT: ("PERFREG008", "Throughput regression"),
}

VOLATILITY_FINDING = ("PERFREG009", "Volatility increase")


def perfreg_findings(
    series_map: dict[str, ExecutionSeries],
    policy: RegressionPolicy | None = None,
) -> list[PerformanceFinding]:
    """PERFREG001-009 — warnings only for PERSISTENT episodes.

    Candidate/one-off regressions surface as INFO so a single slow run
    never pages anyone (§2.8).
    """
    pol = policy or RegressionPolicy.defaults()
    out: list[PerformanceFinding] = []
    for sid in sorted(series_map):
        series = series_map[sid]
        for dim in RegressionDimension:
            eps = episodes(series, dim, pol)
            if not eps:
                sig = detect_dimension(series, dim, pol)
                if sig.klass is RegressionClass.VOLATILE:
                    cid, title = VOLATILITY_FINDING
                    out.append(
                        PerformanceFinding(
                            check_id=cid,
                            title=title,
                            severity=Severity.INFO,
                            message=(
                                f"{series.subject_id}: {dim.value} dispersion rising ({sig.basis})"
                            ),
                            observed=f"current median={sig.current}",
                            derived=f"baseline median={sig.baseline_value}",
                            threshold=f"volatility_ratio={pol.volatility_ratio}",
                            evidence=sig.evidence,
                            confidence=_conf(sig.confidence),
                        )
                    )
                continue
            for ep in eps:
                fid = _DIM_FINDING.get(dim)
                if fid is None:
                    continue
                cid, title = fid
                sev = (
                    Severity.WARNING
                    if ep.persistence is PersistenceClass.PERSISTENT
                    else Severity.INFO
                )
                out.append(
                    PerformanceFinding(
                        check_id=cid,
                        title=title,
                        severity=sev,
                        message=(
                            f"{ep.subject}: {dim.value} {ep.persistence.value} "
                            f"({ep.breaches} breached runs)"
                        ),
                        observed=f"current median={ep.current_median}",
                        derived=f"baseline median={ep.baseline_median}",
                        threshold=(
                            f"median_factor={pol.median_factor} "
                            f"persistent_tail={pol.persistent_tail}"
                        ),
                        evidence=(f"series:{sid}",),
                        confidence=_conf(ep.confidence),
                    )
                )
    return sorted(out, key=lambda f: (f.severity is not Severity.WARNING, f.check_id, f.message))


def _conf(c: RegressionConfidence) -> Confidence:
    return {
        RegressionConfidence.HIGH: Confidence.HIGH,
        RegressionConfidence.MEDIUM: Confidence.MEDIUM,
        RegressionConfidence.LOW: Confidence.LOW,
    }.get(c, Confidence.LOW)


# re-exported for callers that only know this module
__all__ = [
    "PersistenceClass",
    "RegressionConfidence",
    "RegressionDimension",
    "RegressionEpisode",
    "RegressionPolicy",
    "RegressionSignal",
    "classify_persistence",
    "detect_dimension",
    "detect_regressions",
    "episodes",
    "perfreg_findings",
]
