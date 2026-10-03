"""Vendor-neutral query execution model (spec 235, program P phase 1).

One normalized spine for analytic execution. Engine adapters in
``analyzers/execution_adapters.py`` map exported artifacts (Spark event
logs, Snowflake QUERY_HISTORY, BigQuery JOBS, Redshift STL/SVL, Trino
query JSON, ClickHouse query_log) onto ``QueryExecution`` — only the
fields the artifact demonstrably contains.

Everything is deterministic and offline: artifacts are files the user
hands over, never fetched. Derived metrics exist only when both the
numerator evidence and a defined denominator exist; otherwise the
metric is absent (``None`` = UNKNOWN), never invented.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar

SCHEMA_VERSION = "1.0.0"


class StageKind(Enum):
    SCAN = "scan"
    FILTER = "filter"
    JOIN = "join"
    AGGREGATION = "aggregation"
    SORT = "sort"
    WINDOW = "window"
    EXCHANGE = "exchange"
    SHUFFLE = "shuffle"
    WRITE = "write"
    READ = "read"
    REMOTE_READ = "remote_read"
    REMOTE_WRITE = "remote_write"
    MATERIALIZE = "materialize"
    UNKNOWN = "unknown"


class JoinStrategy(Enum):
    BROADCAST = "broadcast"
    SHUFFLE_HASH = "shuffle_hash"
    SORT_MERGE = "sort_merge"
    NESTED_LOOP = "nested_loop"
    REMOTE = "remote"
    UNKNOWN = "unknown"


class ExecutionStatus(Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RUNNING = "running"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionJoin:
    join_type: str = ""  # inner | left | full | cross | ...
    strategy: JoinStrategy = JoinStrategy.UNKNOWN
    left_rows: float | None = None
    right_rows: float | None = None
    output_rows: float | None = None
    redistribution: bool | None = None
    broadcast: bool | None = None
    skew: float | None = None  # max/median task ratio when distributions exist
    spill: float | None = None  # bytes


@dataclass(frozen=True)
class ExecutionScan:
    source: str = ""  # table/file/location when declared
    bytes_total: float | None = None
    bytes_scanned: float | None = None
    rows_total: float | None = None
    rows_scanned: float | None = None
    partitions_total: float | None = None
    partitions_scanned: float | None = None
    files_scanned: float | None = None  # exported file count when present
    predicate: str = ""  # redacted predicate text when exported
    pushdown: str = ""  # pushdown evidence kind, free-form
    pruning: float | None = None
    """Derived only when partitions_total>0: scanned/total share."""


@dataclass(frozen=True)
class ExecutionStage:
    id: str
    kind: StageKind = StageKind.UNKNOWN
    duration_ms: float | None = None
    input_rows: float | None = None
    output_rows: float | None = None
    input_bytes: float | None = None
    output_bytes: float | None = None
    cpu_ms: float | None = None
    memory_peak: float | None = None
    spill_bytes: float | None = None
    shuffle_bytes: float | None = None
    remote_io_bytes: float | None = None
    children: tuple[str, ...] = ()
    joins: tuple[ExecutionJoin, ...] = ()
    scans: tuple[ExecutionScan, ...] = ()


# ---------------------------------------------------------------------------
# Derived metrics — value + basis, absent when evidence is missing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricValue:
    value: float
    basis: str
    """``measured`` | ``derived`` | ``proxy:<detail>`` — how the number was
    obtained; carried so consumers never mistake a proxy for a measure."""


@dataclass(frozen=True)
class ExecutionMetrics:
    scan_amplification: MetricValue | None = None
    shuffle_amplification: MetricValue | None = None
    output_amplification: MetricValue | None = None
    spill_ratio: MetricValue | None = None
    queue_ratio: MetricValue | None = None
    cpu_efficiency: MetricValue | None = None
    remote_io_ratio: MetricValue | None = None

    _FIELDS: ClassVar[tuple[str, ...]] = (
        "scan_amplification",
        "shuffle_amplification",
        "output_amplification",
        "spill_ratio",
        "queue_ratio",
        "cpu_efficiency",
        "remote_io_ratio",
    )

    def known(self) -> dict[str, MetricValue]:
        return {n: v for n in self._FIELDS if (v := getattr(self, n)) is not None}

    def unknown(self) -> tuple[str, ...]:
        return tuple(n for n in self._FIELDS if getattr(self, n) is None)


# ---------------------------------------------------------------------------
# Top-level execution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QueryExecution:
    """One normalized analytic execution, engine-agnostic."""

    execution_id: str
    engine: str  # spark | snowflake | bigquery | redshift | trino | clickhouse
    query_id: str = ""
    query_fingerprint: str = ""
    start_time: float | None = None  # epoch ms when present
    end_time: float | None = None
    duration_ms: float | None = None
    queue_time_ms: float | None = None
    status: ExecutionStatus = ExecutionStatus.UNKNOWN
    stages: tuple[ExecutionStage, ...] = ()
    inputs: tuple[str, ...] = ()  # table/source names demonstrably read
    outputs: tuple[str, ...] = ()
    bytes_read: float | None = None
    bytes_written: float | None = None
    rows_read: float | None = None
    rows_written: float | None = None
    cpu_time_ms: float | None = None
    memory_peak: float | None = None
    spill_bytes: float | None = None
    network_bytes: float | None = None
    evidence: tuple[str, ...] = ()  # adapter name, artifact path, row ids
    metrics: ExecutionMetrics = field(default_factory=ExecutionMetrics)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "execution_id": self.execution_id,
            "engine": self.engine,
            "query_id": self.query_id,
            "query_fingerprint": self.query_fingerprint,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "queue_time_ms": self.queue_time_ms,
            "status": self.status.value,
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "bytes_read": self.bytes_read,
            "bytes_written": self.bytes_written,
            "rows_read": self.rows_read,
            "rows_written": self.rows_written,
            "cpu_time_ms": self.cpu_time_ms,
            "memory_peak": self.memory_peak,
            "spill_bytes": self.spill_bytes,
            "network_bytes": self.network_bytes,
            "metrics": {
                n: {"value": v.value, "basis": v.basis}
                for n, v in sorted(self.metrics.known().items())
            },
            "unknown_metrics": list(self.metrics.unknown()),
            "stages": [
                {
                    "id": s.id,
                    "kind": s.kind.value,
                    "duration_ms": s.duration_ms,
                    "input_rows": s.input_rows,
                    "output_rows": s.output_rows,
                    "input_bytes": s.input_bytes,
                    "output_bytes": s.output_bytes,
                    "cpu_ms": s.cpu_ms,
                    "memory_peak": s.memory_peak,
                    "spill_bytes": s.spill_bytes,
                    "shuffle_bytes": s.shuffle_bytes,
                    "remote_io_bytes": s.remote_io_bytes,
                    "children": list(s.children),
                }
                for s in self.stages
            ],
            "evidence": list(self.evidence),
        }


# ---------------------------------------------------------------------------
# Metric derivation — denominators required, proxies labeled
# ---------------------------------------------------------------------------


def _ratio(num: float | None, den: float | None, basis: str) -> MetricValue | None:
    if num is None or den is None or den <= 0:
        return None
    return MetricValue(round(num / den, 6), basis)


def derive_metrics(ex: QueryExecution) -> ExecutionMetrics:
    """Compute normalized ratios for one execution.

    Every ratio names its basis; anything without evidence stays None
    (UNKNOWN) rather than an invented number.
    """
    shuffle_bytes = _sum_stage(ex, "shuffle_bytes")
    remote_io = _sum_stage(ex, "remote_io_bytes")
    rows_in = ex.rows_read if ex.rows_read is not None else _sum_stage(ex, "input_rows")
    rows_out = ex.rows_written if ex.rows_written is not None else _sum_stage(ex, "output_rows")
    scan_amp = _ratio(
        ex.bytes_read,
        ex.bytes_written,
        "derived:bytes_read/bytes_written",
    ) or _ratio(rows_in, rows_out, "proxy:rows_read/rows_written")
    return ExecutionMetrics(
        scan_amplification=scan_amp,
        shuffle_amplification=_ratio(
            shuffle_bytes, ex.bytes_read, "derived:shuffle_bytes/bytes_read"
        ),
        output_amplification=_ratio(
            ex.bytes_written, ex.bytes_read, "derived:bytes_written/bytes_read"
        ),
        spill_ratio=_ratio(
            ex.spill_bytes,
            ex.memory_peak,
            "derived:spill_bytes/memory_peak",
        )
        or _ratio(ex.spill_bytes, ex.bytes_read, "proxy:spill_bytes/bytes_read"),
        queue_ratio=_ratio(ex.queue_time_ms, ex.duration_ms, "derived:queue_time/duration"),
        cpu_efficiency=_ratio(ex.cpu_time_ms, ex.duration_ms, "derived:cpu_time/duration"),
        remote_io_ratio=_ratio(
            remote_io,
            ex.bytes_read,
            "derived:remote_io_bytes/bytes_read",
        ),
    )


def _sum_stage(ex: QueryExecution, field_name: str) -> float | None:
    vals = [getattr(s, field_name) for s in ex.stages]
    have = [v for v in vals if v is not None]
    if not have:
        return None
    return float(sum(have))


# ---------------------------------------------------------------------------
# Fingerprinting + redaction
# ---------------------------------------------------------------------------

_LINE_COMMENT = re.compile(r"--[^\n]*|#[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRING = re.compile(r"'(?:''|[^'])*'|\$[^$]*\$[^$]*\$[^$]*\$")
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?![\w.])")
_WS = re.compile(r"\s+")
_IDENT_Q = re.compile(r"`([^`]*)`|\[([^\]]*)\]")


def _normalize_sql(sql: str) -> str:
    """Literal-insensitive normalization for fingerprinting.

    Keeps identifiers, operators and keyword structure; strips comments,
    string literals, numeric literals, identifier quoting style, and
    whitespace/case. ``WHERE id = 10`` and ``WHERE id = 20`` normalize
    identically; ``WHERE id = x`` does not collapse into it.
    """
    text = _BLOCK_COMMENT.sub(" ", sql)
    text = _LINE_COMMENT.sub(" ", text)
    text = _STRING.sub("?", text)
    text = _NUMBER.sub("?", text)
    text = _IDENT_Q.sub(lambda m: f'"{m.group(1) or m.group(2)}"', text)
    text = _WS.sub(" ", text).strip().lower()
    return text


def fingerprint_sql(sql: str) -> str:
    """Stable structural fingerprint — literals vary, fingerprint stays."""
    return hashlib.sha256(_normalize_sql(sql).encode("utf-8")).hexdigest()[:16]


def redact_sql(sql: str) -> str:
    """Public-safe form: literals removed, structure preserved."""
    return _normalize_sql(sql)


_SECRET = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key|aws_secret_access_key|"
    r"private[_-]?key)\s*[:=]\s*['\"]?[^\s'\",;]+"
)


def sanitize_text(text: str) -> str:
    """Remove credential-looking key/value pairs from any output text.

    Runtime artifacts can carry SQL literals, user identifiers, paths,
    and credentials — public surfaces (JSON, handoff bundles, MCP, logs)
    pass through this first. Fingerprint input is unaffected.
    """
    return _SECRET.sub(lambda m: m.group(1) + "=<redacted>", text)
