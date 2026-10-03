"""Runtime evidence model + adapter protocol (phase 2, offline-first).

Runtime artifacts are *exported files* the user hands to Forge Doctor Data -
never fetched, never executed. Each adapter parses one artifact shape
into the shared ``RuntimeEvidenceModel`` so downstream phases (finding
promotion, root cause, drift) consume one normalized fact surface.

Identity joins happen only on demonstrable identifiers (ARN, job name,
query id, execution id) collected in ``identifiers`` - never fuzzy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ExecutionMetric:
    """One numeric runtime measurement."""

    name: str  # duration_ms | shuffle_read_bytes | input_rows_per_second ...
    value: float
    unit: str = ""  # ms | bytes | rows/s | count | MB
    execution_id: str = ""
    scope: str = ""  # job-3 | stage-5 | task-12 | workgroup...


@dataclass(frozen=True)
class ExecutionError:
    """One error observed in the artifact."""

    code: str  # OutOfMemoryError | ExecutorLost | TaskFailed | ...
    message: str
    count: int = 1
    execution_id: str = ""


@dataclass(frozen=True)
class ExecutionTiming:
    """Wall-clock for one phase of an execution."""

    phase: str  # queue | planning | execution | init | state | ...
    duration_ms: float
    execution_id: str = ""


@dataclass(frozen=True)
class ExecutionThroughput:
    """Rate/volume facts for a streaming or batch leg."""

    name: str  # stream/batch name or sink
    input_rps: float | None = None
    output_rps: float | None = None
    input_rows: float | None = None
    output_rows: float | None = None
    duration_ms: float | None = None


@dataclass
class RuntimeExecution:
    """One execution spine events/metrics/timings attach to."""

    id: str  # job-N | stage-N | request id | execution arn | query id
    kind: str = ""  # job | stage | task | batch | request | state
    state: str = ""  # started | completed | failed | timed_out
    duration_ms: float | None = None
    attempts: int = 0


@dataclass
class RuntimeEvidenceModel:
    """Normalized facts extracted from one exported artifact."""

    source: str  # adapter name (spark_eventlog | spark_ss_progress | ...)
    artifact: Path | None = None
    identifiers: dict[str, str] = field(default_factory=dict)
    executions: list[RuntimeExecution] = field(default_factory=list)
    events: list[str] = field(default_factory=list)  # human-readable lines
    metrics: list[ExecutionMetric] = field(default_factory=list)
    errors: list[ExecutionError] = field(default_factory=list)
    timings: list[ExecutionTiming] = field(default_factory=list)
    throughput: list[ExecutionThroughput] = field(default_factory=list)
    lag: list[ExecutionMetric] = field(default_factory=list)
    retries: int = 0
    resource_usage: list[ExecutionMetric] = field(default_factory=list)
    state: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return bool(self.errors) or any(e.state == "failed" for e in self.executions)

    def identity_keys(self) -> list[str]:
        """Canonical join keys for graph entities - demonstrable only."""
        keys: list[str] = []
        for field_name, prefix in (
            ("arn", "arn"),
            ("job_name", "job"),
            ("query_id", "query"),
            ("execution_id", "exec"),
            ("function_name", "function"),
            ("request_id", "request"),
        ):
            value = self.identifiers.get(field_name)
            if value:
                keys.append(f"{prefix}:{value}")
        return keys


class RuntimeEvidenceAdapter(Protocol):
    """Artifact parser protocol. ``matches`` must be cheap and strict -
    ambiguous input must not claim an adapter."""

    name: str

    def matches(self, path: Path, text: str) -> bool: ...

    def parse(self, path: Path, text: str) -> RuntimeEvidenceModel: ...
