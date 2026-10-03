"""Streaming runtime diagnostics over Structured Streaming progress dumps.

Parses one or more ``StreamingQueryProgress`` JSON artifacts into a
deterministic batch series, then derives facts — rate imbalance,
backlog growth, state growth, watermark lag, checkpoint (walCommit /
commit) instability, kafka partition lag — without any cloud calls.
"""

from __future__ import annotations

import itertools
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProgressBatch:
    """One normalized micro-batch record from a progress artifact."""

    batch_id: int
    name: str
    input_rps: float | None
    proc_rps: float | None
    input_rows: float | None
    duration_ms: float | None
    add_batch_ms: float | None
    wal_commit_ms: float | None
    commit_ms: float | None
    state_rows: float | None
    watermark_ms: float | None
    max_event_ms: float | None
    backlog_offsets: int  # sum(latestOffset - startOffset) across partitions
    source_desc: str = ""


@dataclass(frozen=True)
class StreamDiagnosis:
    """One deterministic runtime finding."""

    code: str  # SRATE001 | SSTATE002 | SWM003 | SCKPT004 | SKFK005 | SDUR006
    severity: str  # warn | info
    message: str
    evidence: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class StreamRuntimeReport:
    """Batched progress facts + derived diagnostics."""

    artifact_count: int = 0
    batches: list[ProgressBatch] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)  # artifact names
    diagnoses: list[StreamDiagnosis] = field(default_factory=list)
    stream_name: str = ""


_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2}):(\d{2})")


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ts_ms(v: Any) -> float | None:
    """Epoch-ms from 'yyyy-MM-ddTHH:mm:ss.SSS' style strings."""
    if not isinstance(v, str):
        return None
    m = re.search(r"(\d{2}):(\d{2}):(\d{2})\.(\d+)", v)
    if not m:
        return None
    return (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))) * 1000.0 + int(
        m.group(4)[:3]
    )


def _offset_lag(sources: Any) -> tuple[int, str]:
    """Sum(latest - start) over all source partitions + source description."""
    total = 0
    desc = ""
    if not isinstance(sources, list) or not sources:
        return 0, desc
    desc = str(sources[0].get("description") or "")[:60]
    for src in sources:
        if not isinstance(src, dict):
            continue
        start, latest = src.get("startOffset"), src.get("latestOffset") or src.get("endOffset")
        if not isinstance(start, dict) or not isinstance(latest, dict):
            continue
        # kafka: {topic: {partition: n}}; kinesis: {stream: {shard: seq}}
        for topic, parts in latest.items():
            if isinstance(parts, dict):
                for part, lv in parts.items():
                    sv = (start.get(topic) or {}).get(part)
                    if isinstance(sv, (int, float)) and isinstance(lv, (int, float)):
                        total += int(lv - sv)
            else:
                sv = start.get(topic)
                if isinstance(sv, (int, float)) and isinstance(parts, (int, float)):
                    total += int(parts - sv)
    return total, desc


def parse_progress(path: Path) -> ProgressBatch | None:
    """Parse one StreamingQueryProgress JSON file (deterministic)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or "inputRowsPerSecond" not in data:
        return None
    duration = data.get("durationMs") or {}
    d_ms: float | None = None
    add_ms = wal_ms = commit_ms = None
    if isinstance(duration, dict):
        vals = [_num(v) for v in duration.values()]
        d_ms = sum(v for v in vals if v is not None) if any(v is not None for v in vals) else None
        add_ms = _num(duration.get("addBatch"))
        wal_ms = _num(duration.get("walCommit"))
        commit_ms = _num(duration.get("commit"))
    elif _num(duration) is not None:
        d_ms = _num(duration)
    state_rows: float | None = None
    for op in data.get("stateOperators") or []:
        if isinstance(op, dict):
            rows = _num(op.get("numRowsTotal"))
            if rows is not None:
                state_rows = (state_rows or 0.0) + rows
    event = data.get("eventTime") or {}
    wm_ms = mx_ms = None
    if isinstance(event, dict):
        wm_ms = _ts_ms(event.get("watermark"))
        mx_ms = _ts_ms(event.get("max"))
    backlog, desc = _offset_lag(data.get("sources"))
    try:
        batch_id = int(data.get("batchId", -1))
    except (TypeError, ValueError):
        batch_id = -1
    return ProgressBatch(
        batch_id=batch_id,
        name=str(data.get("name") or data.get("id") or path.stem),
        input_rps=_num(data.get("inputRowsPerSecond")),
        proc_rps=_num(data.get("processedRowsPerSecond")),
        input_rows=_num(data.get("numInputRows")),
        duration_ms=d_ms,
        add_batch_ms=add_ms,
        wal_commit_ms=wal_ms,
        commit_ms=commit_ms,
        state_rows=state_rows,
        watermark_ms=wm_ms,
        max_event_ms=mx_ms,
        backlog_offsets=backlog,
        source_desc=desc,
    )


def _diag(code: str, severity: str, message: str, *evidence: str) -> StreamDiagnosis:
    return StreamDiagnosis(code, severity, message, tuple(evidence))


def _diagnose(batches: list[ProgressBatch]) -> list[StreamDiagnosis]:
    """Deterministic rules over an ordered batch series."""
    out: list[StreamDiagnosis] = []
    ordered = sorted(batches, key=lambda b: (b.batch_id, b.name))

    # SRATE001: input rate exceeds processing rate → sustained backlog.
    imbalanced = [
        b
        for b in ordered
        if b.input_rps is not None and b.proc_rps is not None and b.input_rps > b.proc_rps > 0
    ]
    if len(imbalanced) >= max(1, len(ordered) // 2 or 1):
        rates = [
            f"batch {b.batch_id}: in={b.input_rps:.0f} proc={b.proc_rps:.0f}"
            for b in imbalanced[:3]
        ]
        out.append(
            _diag(
                "SRATE001",
                "warn",
                f"input rate exceeds processing rate in {len(imbalanced)}/{len(ordered)} "
                "batches — backlog will grow unbounded",
                *rates,
            )
        )

    # SKFK005: non-zero kafka/kinesis partition backlog.
    lagged = [b for b in ordered if b.backlog_offsets > 0]
    if lagged:
        worst_lag = max(lagged, key=lambda b: b.backlog_offsets)
        out.append(
            _diag(
                "SKFK005",
                "warn",
                f"source backlog: {worst_lag.backlog_offsets} offsets remain "
                f"unprocessed at batch {worst_lag.batch_id} "
                f"({worst_lag.source_desc or 'source'})",
                f"batch {worst_lag.batch_id} backlog={worst_lag.backlog_offsets}",
            )
        )

    # SSTATE002: monotonically growing state rows across ≥3 batches.
    rows = [(b.batch_id, b.state_rows) for b in ordered if b.state_rows is not None]
    if len(rows) >= 3:
        increasing = all(b[1] > a[1] for a, b in itertools.pairwise(rows))
        growth = (rows[-1][1] - rows[0][1]) / max(rows[0][1], 1.0)
        if increasing and growth > 0.5:
            out.append(
                _diag(
                    "SSTATE002",
                    "warn",
                    f"state rows grew monotonically {rows[0][1]:.0f}→{rows[-1][1]:.0f} "
                    f"(+{growth:.0%}) — unbounded keyed state risk",
                    f"series: {[int(r[1]) for r in rows[:6]]}",
                )
            )

    # SWM003: watermark far behind latest event time.
    lag_ms = [
        (b.batch_id, (b.max_event_ms - b.watermark_ms))
        for b in ordered
        if b.max_event_ms is not None and b.watermark_ms is not None
    ]
    worst_wm = max(lag_ms, key=lambda t: t[1]) if lag_ms else None
    if worst_wm and worst_wm[1] > 60_000:
        out.append(
            _diag(
                "SWM003",
                "warn",
                f"watermark trails newest event by {worst_wm[1] / 1000:.0f}s "
                f"at batch {worst_wm[0]} — late-data or source stall",
                f"batch {worst_wm[0]} lag={worst_wm[1] / 1000:.0f}s",
            )
        )

    # SCKPT004: walCommit/commit duration instability across batches.
    commits = [
        (b.batch_id, (b.wal_commit_ms or 0.0) + (b.commit_ms or 0.0))
        for b in ordered
        if b.wal_commit_ms is not None or b.commit_ms is not None
    ]
    if len(commits) >= 3:
        baseline = min(c[1] for c in commits) or 1.0
        # spike = >3x the fastest batch AND at least 1s absolute
        spikes = [c for c in commits if c[1] > 3 * baseline and c[1] >= 1000]
        if spikes:
            worst = max(spikes, key=lambda t: t[1])
            out.append(
                _diag(
                    "SCKPT004",
                    "warn",
                    f"checkpoint/commit phase unstable: {len(spikes)} batches exceed "
                    f"3x the fastest commit (worst {worst[1]:.0f}ms at batch {worst[0]})",
                    f"baseline={baseline:.0f}ms worst={worst[1]:.0f}ms",
                )
            )

    # SDUR006: batch duration close to/exceeding a regular trigger interval.
    durs = [b.duration_ms for b in ordered if b.duration_ms]
    if len(durs) >= 2:
        worst_dur = max(durs)
        assert worst_dur is not None
        if worst_dur > 30_000:
            out.append(
                _diag(
                    "SDUR006",
                    "info",
                    f"slowest batch took {worst_dur / 1000:.1f}s — verify trigger interval and "
                    "micro-batch work fit",
                    f"max_duration={worst_dur:.0f}ms over {len(durs)} batches",
                )
            )
    return out


def diagnose_progress(paths: list[Path]) -> StreamRuntimeReport:
    """Build a report from one or more progress artifacts."""
    report = StreamRuntimeReport()
    for p in sorted(paths, key=lambda p: p.as_posix()):
        batch = parse_progress(p)
        if batch is None:
            report.unparsed.append(p.name)
            continue
        report.artifact_count += 1
        report.batches.append(batch)
        if not report.stream_name and batch.name:
            report.stream_name = batch.name
    report.batches.sort(key=lambda b: b.batch_id)
    report.diagnoses = _diagnose(report.batches)
    return report
