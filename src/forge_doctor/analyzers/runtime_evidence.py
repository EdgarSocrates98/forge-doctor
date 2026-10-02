"""Runtime evidence adapters - offline parsing of exported artifacts.

Each adapter owns one artifact shape and emits the normalized
``RuntimeEvidenceModel``. Detection is ordered and first-match-wins:
an artifact claims exactly one adapter. All input is untrusted - parsers
never execute, never network, and degrade to empty models on malformed
content rather than guessing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from forge_doctor.core.runtime_evidence import (
    ExecutionError,
    ExecutionMetric,
    ExecutionThroughput,
    ExecutionTiming,
    RuntimeEvidenceAdapter,
    RuntimeEvidenceModel,
    RuntimeExecution,
)


def _json_lines(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return out if out else []
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _json_doc(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --- Spark event log ---------------------------------------------------------

_ACCUM_NAMES = {
    "internal.metrics.shuffle.read.bytesread": "shuffle_read_bytes",
    "internal.metrics.shuffle.write.byteswritten": "shuffle_write_bytes",
    "internal.metrics.memorybytesspilled": "spill_memory_bytes",
    "internal.metrics.diskbytesspilled": "spill_disk_bytes",
    "internal.metrics.input.bytesread": "input_bytes",
    "internal.metrics.output.byteswritten": "output_bytes",
    "internal.metrics.jvmgctime": "gc_time_ms",
    "internal.metrics.peakexecutionmemory": "peak_memory_bytes",
}


class SparkEventLogAdapter:
    name = "spark_eventlog"

    def matches(self, path: Path, text: str) -> bool:
        return '"Event"' in text and '"SparkListener' in text[:20000]

    def parse(self, path: Path, text: str) -> RuntimeEvidenceModel:
        model = RuntimeEvidenceModel(source=self.name, artifact=path)
        events = _json_lines(text)
        jobs: dict[str, RuntimeExecution] = {}
        stages: dict[str, RuntimeExecution] = {}
        task_durations: dict[str, list[float]] = {}
        for ev in events:
            kind = ev.get("Event", "")
            if kind == "SparkListenerApplicationStart":
                model.identifiers["app_id"] = str(ev.get("App ID") or "")
                model.identifiers["app_name"] = str(ev.get("App Name") or "")
            elif kind == "SparkListenerJobStart":
                jid = str(ev.get("Job ID", ""))
                jobs[jid] = RuntimeExecution(id=f"job-{jid}", kind="job", state="started")
            elif kind == "SparkListenerJobEnd":
                jid = str(ev.get("Job ID", ""))
                result = ev.get("Job Result") or {}
                state = "failed" if "Fail" in str(result.get("Result", "")) else "completed"
                ex = jobs.get(jid) or RuntimeExecution(id=f"job-{jid}", kind="job")
                jobs[jid] = RuntimeExecution(
                    id=ex.id,
                    kind=ex.kind,
                    state=state,
                    duration_ms=ex.duration_ms,
                    attempts=ex.attempts,
                )
                if state == "failed":
                    model.errors.append(
                        ExecutionError(code="JobFailed", message=f"job {jid} failed")
                    )
            elif kind == "SparkListenerStageCompleted":
                info = ev.get("Stage Info") or {}
                sid = str(info.get("Stage ID", ""))
                stage = RuntimeExecution(
                    id=f"stage-{sid}",
                    kind="stage",
                    state="completed"
                    if str(info.get("Completion Reason", "")) == "" or "Failure Reason" not in info
                    else "failed",
                )
                stages[sid] = stage
                for acc in info.get("Accumulables", []) or []:
                    metric = _ACCUM_NAMES.get(str(acc.get("Name", "")).lower())
                    val = _num(acc.get("Value"))
                    if metric and val is not None:
                        model.metrics.append(ExecutionMetric(metric, val, "bytes", scope=stage.id))
            elif kind == "SparkListenerExecutorRemoved":
                model.errors.append(
                    ExecutionError(
                        code="ExecutorLost",
                        message=str(ev.get("Removed Reason") or "executor removed"),
                        execution_id=str(ev.get("Executor ID", "")),
                    )
                )
            elif kind == "SparkListenerTaskEnd":
                info = ev.get("Task Info") or {}
                sid = str(info.get("Stage ID", ""))
                metrics = ev.get("Task Metrics") or {}
                run_ms = _num(metrics.get("Executor Run Time"))
                if run_ms is not None:
                    task_durations.setdefault(sid, []).append(run_ms)
                    if metrics.get("JVM GC Time"):
                        model.resource_usage.append(
                            ExecutionMetric(
                                "gc_time_ms",
                                float(metrics["JVM GC Time"]),
                                "ms",
                                scope=f"task-{info.get('Task ID', '')}",
                            )
                        )
                if ev.get("Task End Reason", {}).get("Reason", "") in {"TaskFailed", "FetchFailed"}:
                    model.retries += 1
        model.executions.extend(sorted(jobs.values(), key=lambda e: e.id))
        model.executions.extend(sorted(stages.values(), key=lambda e: e.id))
        # Skew signal: max/median task duration per stage.
        for sid, durations in sorted(task_durations.items()):
            if len(durations) < 4:
                continue
            durations.sort()
            median = durations[len(durations) // 2]
            worst = durations[-1]
            model.metrics.append(ExecutionMetric("task_max_ms", worst, "ms", scope=f"stage-{sid}"))
            model.metrics.append(
                ExecutionMetric("task_median_ms", median, "ms", scope=f"stage-{sid}")
            )
            if median > 0 and worst / median >= 3.0:
                model.events.append(
                    f"stage-{sid} skew: max task {worst:.0f}ms vs median {median:.0f}ms"
                )
        return model


# --- Structured Streaming progress -------------------------------------------

_RATE_FIELDS = ("inputRowsPerSecond", "processedRowsPerSecond")


class StreamingProgressAdapter:
    name = "spark_ss_progress"

    def matches(self, path: Path, text: str) -> bool:
        return all(f'"{k}"' in text for k in _RATE_FIELDS) and '"sink"' in text

    def parse(self, path: Path, text: str) -> RuntimeEvidenceModel:
        model = RuntimeEvidenceModel(source=self.name, artifact=path)
        data = _json_doc(text)
        if not isinstance(data, dict):
            return model
        model.identifiers["stream"] = str(data.get("name") or "")
        model.identifiers["execution_id"] = str(data.get("id") or data.get("runId") or "")
        batch = RuntimeExecution(
            id=f"batch-{data.get('batchId', '?')}",
            kind="batch",
            state="completed",
        )
        duration = data.get("durationMs") or {}
        if isinstance(duration, dict):
            batch.duration_ms = _num(sum(v for v in duration.values() if _num(v)))
            for phase, ms in sorted(duration.items()):
                ms_v = _num(ms)
                if ms_v is not None:
                    model.timings.append(
                        ExecutionTiming(phase=str(phase), duration_ms=ms_v, execution_id=batch.id)
                    )
        elif _num(duration):
            batch.duration_ms = _num(duration)
        model.executions.append(batch)
        model.throughput.append(
            ExecutionThroughput(
                name=str(data.get("name") or "stream"),
                input_rps=_num(data.get("inputRowsPerSecond")),
                output_rps=_num(data.get("processedRowsPerSecond")),
                input_rows=_num(data.get("numInputRows")),
                duration_ms=batch.duration_ms,
            )
        )
        for op in data.get("stateOperators") or []:
            if not isinstance(op, dict):
                continue
            rows = _num(op.get("numRowsTotal"))
            if rows is not None:
                model.state.append(f"{op.get('operatorName', 'state')} rows={rows:.0f}")
                model.metrics.append(
                    ExecutionMetric("state_rows_total", rows, "rows", scope=batch.id)
                )
        watermark = data.get("eventTime") or {}
        if isinstance(watermark, dict) and watermark.get("watermark"):
            model.state.append(f"watermark={watermark['watermark']}")
        for src in data.get("sources") or []:
            if not isinstance(src, dict):
                continue
            desc = str(src.get("description") or "source")
            for key in ("latestOffset", "endOffset"):
                off = src.get(key)
                if not isinstance(off, dict):
                    continue
                # shape: {topic: {partition: offset}} or {partition: offset}
                flat: dict[str, float] = {}
                for topic, val in sorted(off.items()):
                    if isinstance(val, dict):
                        for part, offv in sorted(val.items()):
                            if (num := _num(offv)) is not None:
                                flat[f"{topic}-{part}"] = num
                    elif (num := _num(val)) is not None:
                        flat[str(topic)] = num
                for name, num in flat.items():
                    model.lag.append(ExecutionMetric(f"offset_{name}", num, "offset", scope=desc))
        return model


# --- Athena query statistics ---------------------------------------------------

_ATHENA_TIMING = {
    "QueryQueueTimeMillis": "queue",
    "QueryPlanningTimeMillis": "planning",
    "EngineExecutionTimeMillis": "execution",
    "ServiceProcessingTimeMillis": "service_processing",
    "TotalExecutionTimeMillis": "total",
}


class AthenaStatsAdapter:
    name = "athena_stats"

    def matches(self, path: Path, text: str) -> bool:
        return '"DataScannedInBytes"' in text or (
            '"EngineExecutionTimeMillis"' in text and '"QueryExecution' in text
        )

    def parse(self, path: Path, text: str) -> RuntimeEvidenceModel:
        model = RuntimeEvidenceModel(source=self.name, artifact=path)
        data = _json_doc(text)
        if not isinstance(data, dict):
            return model
        stats = data.get("QueryExecution", {}).get("Statistics") or data
        qe = data.get("QueryExecution") or data
        model.identifiers["query_id"] = str(
            qe.get("QueryExecutionId") or data.get("QueryExecutionId") or ""
        )
        model.identifiers["workgroup"] = str(qe.get("WorkGroup") or "")
        scanned = _num(stats.get("DataScannedInBytes"))
        if scanned is not None:
            model.metrics.append(ExecutionMetric("data_scanned_bytes", scanned, "bytes"))
        for key, phase in sorted(_ATHENA_TIMING.items()):
            ms = _num(stats.get(key))
            if ms is not None:
                model.timings.append(ExecutionTiming(phase, ms))
        return model


# --- Lambda REPORT -------------------------------------------------------------

_LAMBDA_REPORT_RE = re.compile(
    r"REPORT\s+RequestId:\s+(?P<req>[\w-]+)\s+Duration:\s+(?P<dur>[\d.]+)\s*ms"
    r"(?:\s+Billed Duration:\s+(?P<billed>[\d.]+)\s*ms)?"
    r"(?:\s+Memory Size:\s+(?P<mem>\d+)\s*MB)?"
    r"(?:\s+Max Memory Used:\s+(?P<used>\d+)\s*MB)?"
    r"(?:\s+Init Duration:\s+(?P<init>[\d.]+)\s*ms)?"
)


class LambdaReportAdapter:
    name = "lambda_report"

    def matches(self, path: Path, text: str) -> bool:
        return "REPORT" in text and "RequestId:" in text and "Duration:" in text

    def parse(self, path: Path, text: str) -> RuntimeEvidenceModel:
        model = RuntimeEvidenceModel(source=self.name, artifact=path)
        for m in _LAMBDA_REPORT_RE.finditer(text):
            req = m.group("req")
            model.identifiers.setdefault("request_id", req)
            model.executions.append(
                RuntimeExecution(
                    id=req,
                    kind="request",
                    state="completed",
                    duration_ms=_num(m.group("dur")),
                )
            )
            for key, metric, unit in (
                ("billed", "billed_duration_ms", "ms"),
                ("mem", "memory_size_mb", "MB"),
                ("used", "max_memory_used_mb", "MB"),
                ("init", "init_duration_ms", "ms"),
            ):
                val = _num(m.group(key))
                if val is not None:
                    model.metrics.append(ExecutionMetric(metric, val, unit, execution_id=req))
        for line in text.splitlines():
            if "Task timed out" in line:
                model.errors.append(ExecutionError(code="Timeout", message=line.strip()))
            elif "Runtime exited" in line or "OutOfMemory" in line:
                model.errors.append(ExecutionError(code="RuntimeError", message=line.strip()))
        return model


# --- Step Functions execution history ---------------------------------------------

_SFN_FAILURE_TYPES = {
    "ExecutionFailed",
    "TaskFailed",
    "LambdaFunctionFailed",
    "StateFailed",
    "MapRunFailed",
}


class StepFunctionsHistoryAdapter:
    name = "sfn_history"

    def matches(self, path: Path, text: str) -> bool:
        return '"events"' in text and ("StateEntered" in text or "ExecutionStarted" in text)

    def parse(self, path: Path, text: str) -> RuntimeEvidenceModel:
        model = RuntimeEvidenceModel(source=self.name, artifact=path)
        data = _json_doc(text)
        events = data.get("events") if isinstance(data, dict) else data
        if not isinstance(events, list):
            return model
        stamps: list[float] = []
        for ev in events:
            if not isinstance(ev, dict):
                continue
            etype = str(ev.get("type") or "")
            ts = _num(ev.get("timestamp"))
            if ts is not None:
                stamps.append(ts)
            details = ev.get("stateEnteredEventDetails") or {}
            name = details.get("name") or ev.get("stateExitedEventDetails", {}).get("name") or ""
            if etype in _SFN_FAILURE_TYPES:
                cause = (
                    details.get("error")
                    or ev.get("executionFailedEventDetails", {}).get("cause")
                    or ev.get("taskFailedEventDetails", {}).get("cause")
                    or ""
                )
                model.errors.append(
                    ExecutionError(code=etype, message=str(cause or etype), execution_id=name)
                )
                model.executions.append(
                    RuntimeExecution(id=str(name or etype), kind="state", state="failed")
                )
            elif etype == "TaskStateEntered" and name:
                model.executions.append(
                    RuntimeExecution(id=str(name), kind="state", state="entered")
                )
            retry = details.get("retryCount") or (ev.get("taskScheduledEventDetails") or {}).get(
                "retryCount"
            )
            if (rv := _num(retry)) is not None:
                model.retries += int(rv)
        if len(stamps) >= 2:
            span = max(stamps) - min(stamps)
            # SFN exports epoch seconds (floats); some snapshots use ms.
            model.executions.append(
                RuntimeExecution(
                    id=model.identifiers.get("execution_id") or "execution",
                    kind="execution",
                    duration_ms=span * 1000 if max(stamps) < 10**12 else span,
                )
            )
        return model


# --- Glue job log -------------------------------------------------------------

_GLUE_ERR_RE = re.compile(
    r"(OutOfMemoryError|Lost executor|SparkException|FetchFailed|GlueException|"
    r"AnalysisException|ERROR\b.*(?:failed|exception))",
    re.IGNORECASE,
)


class GlueLogAdapter:
    """Conservative Glue job-log facts: identifiers + known error kinds."""

    name = "glue_logs"

    def matches(self, path: Path, text: str) -> bool:
        return "JobRunId" in text or ("Glue" in text and ("ERROR" in text or "Job Name" in text))

    def parse(self, path: Path, text: str) -> RuntimeEvidenceModel:
        model = RuntimeEvidenceModel(source=self.name, artifact=path)
        for pattern, key in (
            (r"JobRunId:\s*([\w-]+)", "execution_id"),
            (r"Job Name:\s*([\w./-]+)", "job_name"),
            (r"Attempt Number:\s*(\d+)", "attempt"),
        ):
            m = re.search(pattern, text)
            if m:
                model.identifiers[key] = m.group(1)
        for line in text.splitlines():
            m = _GLUE_ERR_RE.search(line)
            if m:
                code = m.group(1).split()[0].rstrip(":")
                model.errors.append(ExecutionError(code=code, message=line.strip()[:240], count=1))
        if model.errors:
            model.executions.append(
                RuntimeExecution(
                    id=model.identifiers.get("execution_id", "run"),
                    kind="job",
                    state="failed",
                )
            )
        return model


# --- Neptune explain/profile ----------------------------------------------------


class NeptuneExplainAdapter:
    """Reuses the phase-4 explain/profile parser; maps to runtime facts."""

    name = "neptune_explain"

    def matches(self, path: Path, text: str) -> bool:
        return "Neptune" in text or '"cardinality"' in text or '"steps"' in text

    def parse(self, path: Path, text: str) -> RuntimeEvidenceModel:
        from forge_doctor.analyzers.neptune_explain import analyze_explain

        model = RuntimeEvidenceModel(source=self.name, artifact=path)
        report = analyze_explain(path)
        if not report.parsed:
            return model
        model.identifiers["language"] = report.language
        model.events.extend(report.steps)
        if report.max_cardinality is not None:
            model.metrics.append(ExecutionMetric("max_cardinality", report.max_cardinality, "rows"))
        for flag in report.flags:
            model.events.append(f"{flag.kind}: {flag.detail}")
        return model


ADAPTERS: tuple[RuntimeEvidenceAdapter, ...] = (
    SparkEventLogAdapter(),
    AthenaStatsAdapter(),
    StepFunctionsHistoryAdapter(),
    StreamingProgressAdapter(),
    LambdaReportAdapter(),
    GlueLogAdapter(),
    NeptuneExplainAdapter(),
)


def ingest_artifact(path: Path, adapter: str | None = None) -> RuntimeEvidenceModel:
    """Parse ``path`` with the first matching adapter (or a named one).

    Unknown input returns an empty model with ``source='unknown'`` -
    honest degradation, never a guessed adapter.
    """
    resolved = Path(path)
    try:
        text = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return RuntimeEvidenceModel(source="unreadable", artifact=resolved)
    if len(text) > 50_000_000:  # 50 MB cap - untrusted input stays cheap
        text = text[:50_000_000]
    for candidate in ADAPTERS:
        if adapter and candidate.name != adapter:
            continue
        if candidate.matches(resolved, text):
            return candidate.parse(resolved, text)
    return RuntimeEvidenceModel(source="unknown", artifact=resolved)
