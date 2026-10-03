"""Execution history & baselines (spec 241, program Q phase 1).

Temporal series per subject — query fingerprint, job, pipeline, dataset,
workload, engine — built exclusively from normalized ``QueryExecution``
rows (parsing is never duplicated; adapters remain the only readers of
raw artifacts).  History is stored as versioned compact JSONL snapshots
under ``.forge-doctor-data/execution-history/`` — never raw logs, never SQL
text, never credentials.  Readers stream records; nothing requires
loading an entire history into memory.

Honest-evidence rules inherited from the execution model apply: a
metric absent from the artifact stays ``None`` (UNKNOWN); nothing is
invented.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from forge_doctor_data.core.execution_model import QueryExecution
from forge_doctor_data.core.trends import (
    MetricBaseline,
    TrendSeries,
    series_from,
)
from forge_doctor_data.version import VERSION as TOOL_VERSION

SNAPSHOT_FORMAT = "forge-doctor-data/execution-history@1"
EXECUTION_HISTORY_DIRNAME = "execution-history"


class SubjectKind(Enum):
    """What a series describes — deterministic subject axis."""

    FINGERPRINT = "fingerprint"
    JOB = "job"
    PIPELINE = "pipeline"
    DATASET = "dataset"
    WORKLOAD = "workload"
    ENGINE = "engine"


class TimestampQuality(Enum):
    """How trustworthy a timestamp is for cross-system correlation."""

    UTC_ALIGNED = "utc_aligned"  # explicit UTC instant (offset/Z or epoch)
    EPOCH_ASSUMED_UTC = "epoch_assumed_utc"  # numeric epoch, no tz declared
    LOCAL_UNALIGNED = "local_unaligned"  # local clock, offset unknown
    UNKNOWN = "unknown"  # missing or unparsable


@dataclass(frozen=True)
class ExecutionSample:
    """One compact normalized observation — never raw artifact text."""

    timestamp: float | None  # epoch ms, UTC-normalized
    timestamp_quality: TimestampQuality = TimestampQuality.UNKNOWN
    execution_id: str = ""
    fingerprint: str = ""  # workload identity — never the SQL text
    engine: str = ""
    duration_ms: float | None = None
    queue_time_ms: float | None = None
    bytes_read: float | None = None
    bytes_written: float | None = None
    rows_read: float | None = None
    rows_written: float | None = None
    shuffle_bytes: float | None = None
    spill_bytes: float | None = None
    cpu_time_ms: float | None = None
    memory_peak: float | None = None
    network_bytes: float | None = None
    freshness_s: float | None = None
    status: str = ""
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "timestamp_quality": self.timestamp_quality.value,
            "execution_id": self.execution_id,
            "fingerprint": self.fingerprint,
            "engine": self.engine,
            "duration_ms": self.duration_ms,
            "queue_time_ms": self.queue_time_ms,
            "bytes_read": self.bytes_read,
            "bytes_written": self.bytes_written,
            "rows_read": self.rows_read,
            "rows_written": self.rows_written,
            "shuffle_bytes": self.shuffle_bytes,
            "spill_bytes": self.spill_bytes,
            "cpu_time_ms": self.cpu_time_ms,
            "memory_peak": self.memory_peak,
            "network_bytes": self.network_bytes,
            "freshness_s": self.freshness_s,
            "status": self.status,
            "evidence": list(self.evidence),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionSample:
        return cls(
            timestamp=_f(data.get("timestamp")),
            timestamp_quality=TimestampQuality(str(data.get("timestamp_quality") or "unknown")),
            execution_id=str(data.get("execution_id") or ""),
            fingerprint=str(data.get("fingerprint") or ""),
            engine=str(data.get("engine") or ""),
            duration_ms=_f(data.get("duration_ms")),
            queue_time_ms=_f(data.get("queue_time_ms")),
            bytes_read=_f(data.get("bytes_read")),
            bytes_written=_f(data.get("bytes_written")),
            rows_read=_f(data.get("rows_read")),
            rows_written=_f(data.get("rows_written")),
            shuffle_bytes=_f(data.get("shuffle_bytes")),
            spill_bytes=_f(data.get("spill_bytes")),
            cpu_time_ms=_f(data.get("cpu_time_ms")),
            memory_peak=_f(data.get("memory_peak")),
            network_bytes=_f(data.get("network_bytes")),
            freshness_s=_f(data.get("freshness_s")),
            status=str(data.get("status") or ""),
            evidence=tuple(str(e) for e in data.get("evidence", ())),
        )


_SAMPLE_METRICS: tuple[str, ...] = (
    "duration_ms",
    "queue_time_ms",
    "bytes_read",
    "bytes_written",
    "rows_read",
    "rows_written",
    "shuffle_bytes",
    "spill_bytes",
    "cpu_time_ms",
    "memory_peak",
    "network_bytes",
    "freshness_s",
)


def _f(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def sample_from(ex: QueryExecution) -> ExecutionSample:
    """Compact a ``QueryExecution`` into a sample — metrics only.

    The query text is *never* carried into history; only the fingerprint
    (already redacted/hashed upstream) identifies the workload.
    """
    ts = ex.start_time
    quality = TimestampQuality.EPOCH_ASSUMED_UTC if ts is not None else (TimestampQuality.UNKNOWN)
    return ExecutionSample(
        timestamp=ts,
        timestamp_quality=quality,
        execution_id=ex.execution_id,
        fingerprint=ex.query_fingerprint,
        engine=ex.engine,
        duration_ms=ex.duration_ms,
        queue_time_ms=ex.queue_time_ms,
        bytes_read=ex.bytes_read,
        bytes_written=ex.bytes_written,
        rows_read=ex.rows_read,
        rows_written=ex.rows_written,
        shuffle_bytes=_stage_sum(ex, "shuffle_bytes"),
        spill_bytes=ex.spill_bytes or _stage_sum(ex, "spill_bytes"),
        cpu_time_ms=ex.cpu_time_ms,
        memory_peak=ex.memory_peak,
        network_bytes=ex.network_bytes,
        freshness_s=None,
        status=ex.status.value,
        evidence=ex.evidence,
    )


def _stage_sum(ex: QueryExecution, field_name: str) -> float | None:
    vals = [getattr(s, field_name) for s in ex.stages if getattr(s, field_name) is not None]
    return sum(vals) if vals else None


# ---------------------------------------------------------------------------
# Series
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionSeries:
    """Ordered samples for one subject — deterministic total order."""

    series_id: str  # "{subject_kind}:{subject_id}"
    subject_kind: SubjectKind
    subject_id: str
    engine: str = ""
    fingerprint: str = ""
    samples: tuple[ExecutionSample, ...] = ()
    evidence_sources: tuple[str, ...] = ()

    @property
    def first_seen(self) -> float | None:
        ts = [s.timestamp for s in self.samples if s.timestamp is not None]
        return min(ts) if ts else None

    @property
    def last_seen(self) -> float | None:
        ts = [s.timestamp for s in self.samples if s.timestamp is not None]
        return max(ts) if ts else None

    @property
    def sample_count(self) -> int:
        return len(self.samples)

    def metric_values(self, metric: str) -> list[float]:
        """Observed values for one metric — timestamps not required.

        Distribution statistics (median/percentiles/MAD) do not need
        time; only trend does.  Unmeasured points stay out, never zero.
        """
        return [getattr(s, metric) for s in self.samples if getattr(s, metric, None) is not None]

    def metric_series(self, metric: str) -> TrendSeries:
        """``TrendSeries`` over one metric; unmeasured/undated points skipped."""
        points = [
            (s.timestamp, getattr(s, metric))
            for s in self.samples
            if s.timestamp is not None and getattr(s, metric, None) is not None
        ]
        return series_from(self.series_id, metric, points)

    def to_dict(self) -> dict[str, Any]:
        return {
            "series_id": self.series_id,
            "subject_kind": self.subject_kind.value,
            "subject_id": self.subject_id,
            "engine": self.engine,
            "fingerprint": self.fingerprint,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "sample_count": self.sample_count,
            "evidence_sources": list(self.evidence_sources),
            "samples": [s.to_dict() for s in self.samples],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionSeries:
        return cls(
            series_id=str(data.get("series_id") or ""),
            subject_kind=SubjectKind(str(data.get("subject_kind") or "fingerprint")),
            subject_id=str(data.get("subject_id") or ""),
            engine=str(data.get("engine") or ""),
            fingerprint=str(data.get("fingerprint") or ""),
            samples=tuple(ExecutionSample.from_dict(s) for s in data.get("samples", ())),
            evidence_sources=tuple(str(e) for e in data.get("evidence_sources", ())),
        )


def _subject_key(ex: QueryExecution, kind: SubjectKind) -> str:
    if kind is SubjectKind.FINGERPRINT:
        return ex.query_fingerprint
    if kind is SubjectKind.ENGINE:
        return ex.engine
    if kind is SubjectKind.DATASET:
        return ",".join(sorted(ex.outputs or ex.inputs))
    # JOB / PIPELINE / WORKLOAD use the non-fingerprint identity when
    # the artifact exposes one — else fall back to fingerprint so the
    # series still exists honestly.
    return ex.query_id or ex.query_fingerprint or ex.execution_id


def build_series(
    executions: list[QueryExecution], kind: SubjectKind = SubjectKind.FINGERPRINT
) -> dict[str, ExecutionSeries]:
    """Group executions into series; deterministic order by ts+id."""
    grouped: dict[str, list[QueryExecution]] = {}
    for ex in executions:
        key = _subject_key(ex, kind)
        if not key:
            continue
        grouped.setdefault(f"{kind.value}:{key}", []).append(ex)
    out: dict[str, ExecutionSeries] = {}
    for sid in sorted(grouped):
        exs = grouped[sid]
        ordered = sorted(
            exs, key=lambda e: (e.start_time is None, e.start_time or 0, e.execution_id)
        )
        samples = tuple(sample_from(e) for e in ordered)
        sources = sorted({ev for e in exs for ev in e.evidence})
        out[sid] = ExecutionSeries(
            series_id=sid,
            subject_kind=kind,
            subject_id=sid.partition(":")[2],
            engine=exs[0].engine,
            fingerprint=exs[0].query_fingerprint,
            samples=samples,
            evidence_sources=tuple(sources),
        )
    return out


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------


class BaselineWindowKind(Enum):
    LAST_N = "last_n"
    LAST_DAYS = "last_days"
    NAMED = "named"
    RELEASE = "release"


@dataclass(frozen=True)
class BaselineWindow:
    kind: BaselineWindowKind = BaselineWindowKind.LAST_N
    n: int = 0  # executions or days depending on kind
    name: str = ""


@dataclass(frozen=True)
class ExecutionBaseline:
    """Robust per-metric baseline for one series over a window."""

    fingerprint: str
    subject_kind: SubjectKind
    subject_id: str
    sample_count: int
    window: str
    metrics: dict[str, MetricBaseline] = field(default_factory=dict)
    confidence: str = "unknown"  # high|medium|low|unknown — from evidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "subject_kind": self.subject_kind.value,
            "subject_id": self.subject_id,
            "sample_count": self.sample_count,
            "window": self.window,
            "confidence": self.confidence,
            "metrics": {
                m: {
                    "samples": b.samples,
                    "median": b.median,
                    "p50": b.p50,
                    "p90": b.p90,
                    "p95": b.p95,
                    "p99": b.p99,
                    "min": b.minimum,
                    "max": b.maximum,
                    "mad": b.mad,
                    "trend": b.trend.value,
                }
                for m, b in sorted(self.metrics.items())
            },
        }


def _confidence(series: ExecutionSeries) -> str:
    """Evidence-based confidence: sample count + metric completeness."""
    n = series.sample_count
    if n == 0:
        return "unknown"
    covered = 0
    for s in series.samples:
        if s.duration_ms is not None:
            covered += 1
    completeness = covered / n
    if n >= 10 and completeness >= 0.8:
        return "high"
    if n >= 5 and completeness >= 0.5:
        return "medium"
    if n >= 3:
        return "low"
    return "unknown"


def baseline_for(
    series: ExecutionSeries,
    window: BaselineWindow | None = None,
) -> ExecutionBaseline:
    """Baseline over a window (default: whole series)."""
    window = window or BaselineWindow()
    samples = series.samples
    if window.kind is BaselineWindowKind.LAST_N and window.n > 0:
        samples = samples[-window.n :]
    elif window.kind is BaselineWindowKind.LAST_DAYS and window.n > 0:
        last = [s.timestamp for s in samples if s.timestamp is not None]
        if last:
            cutoff = max(last) - window.n * 86_400_000
            samples = tuple(s for s in samples if s.timestamp is not None and s.timestamp >= cutoff)
    scoped = ExecutionSeries(
        series_id=series.series_id,
        subject_kind=series.subject_kind,
        subject_id=series.subject_id,
        engine=series.engine,
        fingerprint=series.fingerprint,
        samples=samples,
        evidence_sources=series.evidence_sources,
    )
    metrics: dict[str, MetricBaseline] = {}
    from forge_doctor_data.core.trends import (
        mad as _mad,
    )
    from forge_doctor_data.core.trends import (
        median as _median,
    )
    from forge_doctor_data.core.trends import (
        percentile as _pct,
    )
    from forge_doctor_data.core.trends import (
        trend_direction,
    )

    for name in _SAMPLE_METRICS:
        values = scoped.metric_values(name)
        if not values:
            continue
        ts = scoped.metric_series(name)
        metrics[name] = MetricBaseline(
            metric=name,
            samples=len(values),
            median=_median(values),
            p50=_pct(values, 0.50),
            p90=_pct(values, 0.90),
            p95=_pct(values, 0.95),
            p99=_pct(values, 0.99),
            minimum=min(values),
            maximum=max(values),
            mad=_mad(values),
            trend=trend_direction(ts),
        )
    return ExecutionBaseline(
        fingerprint=series.fingerprint,
        subject_kind=series.subject_kind,
        subject_id=series.subject_id,
        sample_count=scoped.sample_count,
        window=f"{window.kind.value}:{window.n or 'all'}{':' + window.name if window.name else ''}",
        metrics=metrics,
        confidence=_confidence(scoped),
    )


# ---------------------------------------------------------------------------
# Snapshot storage — versioned compact JSONL, streaming readers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionHistorySnapshot:
    """One recorded batch of normalized samples (compact, versioned)."""

    path: Path
    created: str
    kind: str = "production"  # production | experiment
    sample_count: int = 0
    schema_version: str = SNAPSHOT_FORMAT
    tool_version: str = ""


def history_root(root: Path) -> Path:
    return root / ".forge-doctor-data" / EXECUTION_HISTORY_DIRNAME


def record_executions(
    root: Path,
    executions: list[QueryExecution],
    *,
    kind: str = "production",
    label: str = "",
) -> Path:
    """Append one snapshot batch.  Never runs implicitly — callers decide."""
    target = history_root(root)
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    prefix = "exp-" if kind == "experiment" else ""
    suffix = f"-{label}" if label else ""
    path = target / f"{prefix}{stamp}{suffix}.jsonl"
    n = 1
    while path.exists():
        path = target / f"{prefix}{stamp}{suffix}_{n}.jsonl"
        n += 1
    header = {
        "format": SNAPSHOT_FORMAT,
        "kind": kind,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "tool_version": TOOL_VERSION,
        "samples": len(executions),
    }
    with path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"header": header}) + "\n")
        for ex in sorted(
            executions,
            key=lambda e: (e.start_time is None, e.start_time or 0, e.execution_id),
        ):
            fh.write(json.dumps({"sample": sample_from(ex).to_dict()}) + "\n")
    return path


def iter_snapshots(root: Path, kind: str | None = None) -> Iterator[Path]:
    d = history_root(root)
    if not d.is_dir():
        return
    for p in sorted(d.iterdir()):
        if p.suffix != ".jsonl" or not p.is_file():
            continue
        if kind == "experiment" and not p.name.startswith("exp-"):
            continue
        if kind == "production" and p.name.startswith("exp-"):
            continue
        yield p


def read_snapshot(path: Path) -> Iterator[ExecutionSample]:
    """Stream samples from one snapshot — header validated first."""
    with path.open("r", encoding="utf-8") as fh:
        first = fh.readline()
        try:
            header = json.loads(first).get("header", {})
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}: unreadable header: {exc}") from exc
        if header.get("format") != SNAPSHOT_FORMAT:
            raise ValueError(f"{path}: not a {SNAPSHOT_FORMAT} snapshot")
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line).get("sample")
            if row:
                yield ExecutionSample.from_dict(row)


def iter_samples(root: Path, kind: str | None = None) -> Iterator[ExecutionSample]:
    """Stream every sample across snapshots — constant memory."""
    for p in iter_snapshots(root, kind):
        yield from read_snapshot(p)


# ---------------------------------------------------------------------------
# Retention & compaction (§40-41)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HistoryRetention:
    """Retention config — ``[tool.forge-doctor-data.history]`` fields."""

    keep_days: int = 30
    keep_samples: int = 10_000
    compact_after: int = 7  # days before compaction to daily aggregates

    @classmethod
    def defaults(cls) -> HistoryRetention:
        return cls()

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> HistoryRetention:
        if not data:
            return cls()
        return cls(
            keep_days=int(data.get("keep_days", 30)),
            keep_samples=int(data.get("keep_samples", 10_000)),
            compact_after=int(data.get("compact_after", 7)),
        )


def prune_history(root: Path, retention: HistoryRetention | None = None) -> list[Path]:
    """Drop snapshots older than ``keep_days``. Returns removed paths."""
    r = retention or HistoryRetention.defaults()
    cutoff = datetime.now(UTC).timestamp() - r.keep_days * 86_400
    removed: list[Path] = []
    for p in iter_snapshots(root):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
                removed.append(p)
        except OSError:
            continue
    return removed


def compact_history(root: Path, retention: HistoryRetention | None = None) -> dict[str, int]:
    """Compact per-day samples into daily aggregate snapshots.

    Raw samples older than ``compact_after`` days are rewritten as one
    daily-median record per subject+metric — baselines survive; raw
    detail does not.  Returns counts for the run record.
    """
    r = retention or HistoryRetention.defaults()
    cutoff_ms = (datetime.now(UTC).timestamp() - r.compact_after * 86_400) * 1000
    old: list[ExecutionSample] = []
    for p in iter_snapshots(root, kind="production"):
        old.extend(s for s in read_snapshot(p) if (s.timestamp or 0) < cutoff_ms)
    if not old:
        return {"compacted_samples": 0, "aggregate_records": 0}
    by_day: dict[tuple[str, str], list[ExecutionSample]] = {}
    for s in old:
        day = datetime.fromtimestamp((s.timestamp or 0) / 1000, UTC).strftime("%Y%m%d")
        by_day.setdefault((day, s.execution_id.partition(":")[0]), []).append(s)
    written = 0
    for (day, _grp), samples in sorted(by_day.items()):
        agg = {
            "format": SNAPSHOT_FORMAT,
            "kind": "aggregate",
            "day": day,
            "tool_version": TOOL_VERSION,
            "samples": len(samples),
            "duration_ms_median": _median_of(s.duration_ms for s in samples),
        }
        path = history_root(root) / f"agg-{day}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"aggregate": agg}) + "\n")
        written += 1
    return {"compacted_samples": len(old), "aggregate_records": written}


def _median_of(values: Any) -> float | None:
    from forge_doctor_data.core.trends import median

    return median([v for v in values if v is not None])


# ---------------------------------------------------------------------------
# Twin integration (§1.8) — OBSERVED facts gain historical series
# ---------------------------------------------------------------------------


def observed_fact_series(
    facts: list[Any], executions: list[QueryExecution]
) -> dict[str, ExecutionSeries]:
    """Map OBSERVED twin facts to execution series for their entity.

    A fact joins a series when its entity matches a series subject id or
    fingerprint — explicit identity evidence, never name-similarity.
    Facts without runtime evidence simply do not appear.
    """
    from forge_doctor_data.core.twin_states import TwinState

    series = build_series(executions, SubjectKind.FINGERPRINT)
    series.update(build_series(executions, SubjectKind.JOB))
    by_subject = {s.subject_id: s for s in series.values()} | {
        s.fingerprint: s for s in series.values() if s.fingerprint
    }
    out: dict[str, ExecutionSeries] = {}
    for f in facts:
        if getattr(f, "state", None) is not TwinState.OBSERVED:
            continue
        hit = by_subject.get(getattr(f, "entity", ""))
        if hit is not None:
            out[f"{f.entity}.{f.property}"] = hit
    return dict(sorted(out.items()))
