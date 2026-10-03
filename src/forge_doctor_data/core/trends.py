"""Reusable temporal-series statistics (program Q cross-phase, §8).

One deterministic ``TrendSeries`` shared by performance, cost drivers,
capacity, freshness and reliability analysis — so five modules never
grow five implementations.  All statistics are robust (median / MAD /
percentiles) and fully deterministic: nearest-rank percentiles, total
ordering on (timestamp, index), no floating randomness.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TrendDirection(Enum):
    """Direction of change across a series, evidence-bounded."""

    RISING = "rising"
    FALLING = "falling"
    STABLE = "stable"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True)
class SeriesPoint:
    """One normalized temporal observation (epoch-ms timestamp)."""

    timestamp: float
    value: float
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class TrendSeries:
    """Ordered (timestamp, value) observations for one subject+metric.

    Points sort by ``(timestamp, sequence)`` — equal timestamps keep
    input order so output is fully deterministic.
    """

    subject: str
    metric: str
    points: tuple[SeriesPoint, ...] = ()

    @property
    def count(self) -> int:
        return len(self.points)

    @property
    def values(self) -> list[float]:
        return [p.value for p in self.points]

    @property
    def first_seen(self) -> float | None:
        return self.points[0].timestamp if self.points else None

    @property
    def last_seen(self) -> float | None:
        return self.points[-1].timestamp if self.points else None

    def window_last_n(self, n: int) -> TrendSeries:
        return TrendSeries(self.subject, self.metric, self.points[-max(0, n) :])

    def window_last_ms(self, ms: float) -> TrendSeries:
        if not self.points:
            return self
        cutoff = self.points[-1].timestamp - ms
        return TrendSeries(
            self.subject,
            self.metric,
            tuple(p for p in self.points if p.timestamp >= cutoff),
        )


def series_from(subject: str, metric: str, points: list[tuple[float, float]]) -> TrendSeries:
    """Build a deterministically-ordered series from (ts, value) pairs."""
    return TrendSeries(
        subject=subject,
        metric=metric,
        points=tuple(
            SeriesPoint(timestamp=ts, value=v) for ts, v in sorted(points, key=lambda p: p[0])
        ),
    )


# ---------------------------------------------------------------------------
# Robust statistics — deterministic nearest-rank, no external deps
# ---------------------------------------------------------------------------


def median(xs: list[float]) -> float | None:
    if not xs:
        return None
    o = sorted(xs)
    n = len(o)
    return o[n // 2] if n % 2 else (o[n // 2 - 1] + o[n // 2]) / 2


def percentile(xs: list[float], q: float) -> float | None:
    """Nearest-rank percentile (0 < q <= 1), deterministic."""
    if not xs or not 0 < q <= 1:
        return None
    o = sorted(xs)
    import math

    return o[max(0, math.ceil(q * len(o)) - 1)]


def mad(xs: list[float]) -> float | None:
    """Median absolute deviation — robust spread around the median."""
    med = median(xs)
    if med is None:
        return None
    return median([abs(x - med) for x in xs])


def trend_direction(
    series: TrendSeries,
    min_samples: int = 4,
    tolerance: float = 0.05,
) -> TrendDirection:
    """First-half vs second-half median comparison.

    ``tolerance`` is a relative deadband around the earlier median so
    noise-level drift stays STABLE.  Fewer than ``min_samples`` points
    never produces a direction claim.
    """
    xs = series.values
    if len(xs) < max(2, min_samples):
        return TrendDirection.INSUFFICIENT_DATA
    mid = len(xs) // 2
    a = median(xs[:mid])
    b = median(xs[mid:])
    if a is None or b is None or a == 0:
        return TrendDirection.INSUFFICIENT_DATA
    rel = (b - a) / abs(a)
    if abs(rel) <= tolerance:
        return TrendDirection.STABLE
    return TrendDirection.RISING if rel > 0 else TrendDirection.FALLING


@dataclass(frozen=True)
class MetricBaseline:
    """Robust distribution summary for one metric over a window."""

    metric: str
    samples: int
    median: float | None = None
    p50: float | None = None
    p90: float | None = None
    p95: float | None = None
    p99: float | None = None
    minimum: float | None = None
    maximum: float | None = None
    mad: float | None = None
    trend: TrendDirection = TrendDirection.INSUFFICIENT_DATA


def metric_baseline(series: TrendSeries, trend_min_samples: int = 4) -> MetricBaseline:
    """Full baseline for a series window — all fields from evidence."""
    xs = series.values
    return MetricBaseline(
        metric=series.metric,
        samples=len(xs),
        median=median(xs),
        p50=percentile(xs, 0.50),
        p90=percentile(xs, 0.90),
        p95=percentile(xs, 0.95),
        p99=percentile(xs, 0.99),
        minimum=min(xs) if xs else None,
        maximum=max(xs) if xs else None,
        mad=mad(xs),
        trend=trend_direction(series, min_samples=trend_min_samples),
    )
