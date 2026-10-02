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
                submitted = _num(info.get("Submission Time"))
                completed = _num(info.get("Completion Time"))
                stage = RuntimeExecution(
                    id=f"stage-{sid}",
                    kind="stage",
                    state="completed"
                    if str(info.get("Completion Reason", "")) == "" or "Failure Reason" not in info
                    else "failed",
                    duration_ms=(completed - submitted)
                    if submitted is not None and completed is not None
                    else None,
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
            model.metrics.append(
                ExecutionMetric("task_count", float(len(durations)), "tasks", scope=f"stage-{sid}")
            )
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


# --- Flink checkpoint history -------------------------------------------------


class FlinkCheckpointAdapter:
    """Flink REST checkpoint history JSON (``/jobs/:id/checkpoints``)."""

    name = "flink_checkpoints"

    def matches(self, path: Path, text: str) -> bool:
        return (
            '"checkpoints"' in text
            and ('"history"' in text or '"latest"' in text or '"counts"' in text)
            and '"status"' in text
        )

    def parse(self, path: Path, text: str) -> RuntimeEvidenceModel:
        model = RuntimeEvidenceModel(source=self.name, artifact=path)
        data = _json_doc(text)
        if not isinstance(data, dict):
            return model
        cp = data.get("checkpoints") or {}
        if not isinstance(cp, dict):
            cp = data
        counts = cp.get("counts") or {}
        if isinstance(counts, dict):
            for key in ("completed", "failed", "in_progress", "restored", "total"):
                num = _num(counts.get(key))
                if num is not None:
                    model.metrics.append(ExecutionMetric(f"checkpoints_{key}", num, "count"))
        history = cp.get("history") or []
        failed = completed = 0
        for entry in history:
            if not isinstance(entry, dict):
                continue
            status = str(entry.get("status") or "").upper()
            cid = str(entry.get("id") or "?")
            state = {"COMPLETED": "completed", "FAILED": "failed"}.get(
                status, status.lower() or "unknown"
            )
            if status == "FAILED":
                failed += 1
            elif status == "COMPLETED":
                completed += 1
            model.executions.append(
                RuntimeExecution(
                    id=f"checkpoint-{cid}",
                    kind="checkpoint",
                    state=state,
                    duration_ms=_num(entry.get("end_to_end_duration")),
                )
            )
            model.events.append(f"checkpoint {cid}: {state}")
        if failed:
            model.errors.append(
                ExecutionError(
                    code="CheckpointFailed",
                    message=f"{failed} checkpoint(s) failed in checkpoint history",
                    count=failed,
                )
            )
        model.identifiers["checkpoints_total"] = str(len(history))
        model.identifiers["checkpoints_failed"] = str(failed)
        model.identifiers["checkpoints_completed"] = str(completed)
        return model


# --- Kinesis / kafka runtime metrics (cloudwatch exports) ---------------------


class StreamMetricsAdapter:
    """Generic streaming metric exports (iterator age, lag, records in/out).

    Recognizes JSON lists/maps of ``{name, value, unit}`` metric points —
    e.g. ``MillisBehindLatest`` (kinesis iterator age) or kafka
    ``records-lag`` exports — so runtime findings can name the lag.
    """

    name = "stream_metrics"

    _KNOWN = re.compile(
        r"millisbehindlatest|iteratorage|records?[-_ ]?lag|"
        r"consumerlag|readprovisioned|writeprovisioned|incomingrecords",
        re.IGNORECASE,
    )

    def matches(self, path: Path, text: str) -> bool:
        return bool(self._KNOWN.search(text)) and '"value"' in text.lower()

    def parse(self, path: Path, text: str) -> RuntimeEvidenceModel:
        model = RuntimeEvidenceModel(source=self.name, artifact=path)
        data = _json_doc(text)
        points: list[Any] = []
        if isinstance(data, list):
            points = data
        elif isinstance(data, dict):
            for key in ("metrics", "datapoints", "results"):
                if isinstance(data.get(key), list):
                    points = data[key]
                    break
            if not points:
                points = [{"name": k, "value": v} for k, v in data.items() if _num(v) is not None]
        for p in points:
            if not isinstance(p, dict):
                continue
            name = str(p.get("name") or p.get("metric") or p.get("metricName") or "")
            if not self._KNOWN.search(name):
                continue
            value = _num(p.get("value"))
            if value is None:
                continue
            unit = str(p.get("unit") or ("ms" if "millis" in name.lower() else "count"))
            scope = str(p.get("stream") or p.get("consumer") or p.get("scope") or "")
            model.metrics.append(ExecutionMetric(name, value, unit, scope=scope))
            model.events.append(f"{name}={value:g}{unit}")
        return model


# --- Snowflake query history exports ------------------------------------------


class SnowflakeHistoryAdapter:
    """Snowflake QUERY_HISTORY / INFORMATION_SCHEMA export rows.

    Recognizes JSON arrays (or objects with ``rows``/``data``) or CSV with
    ``QUERY_ID``/``QUERY_TEXT``-style headers — deterministic, offline.
    """

    name = "snowflake_query_history"

    _FIELDS = re.compile(
        r'"(query_id|query_text|warehouse_name|execution_time|bytes_scanned)"',
        re.IGNORECASE,
    )

    def matches(self, path: Path, text: str) -> bool:
        head = text[:4000].lower()
        return (
            ("query_id" in head or "queryid" in head)
            and ("query_text" in head or "execution_time" in head)
        ) or bool(self._FIELDS.search(text[:4000]))

    def parse(self, path: Path, text: str) -> RuntimeEvidenceModel:
        model = RuntimeEvidenceModel(source=self.name, artifact=path)
        rows: list[dict[str, Any]] = []
        data = _json_doc(text)
        if isinstance(data, list):
            rows = [r for r in data if isinstance(r, dict)]
        elif isinstance(data, dict):
            for key in ("rows", "data", "results"):
                if isinstance(data.get(key), list):
                    rows = [r for r in data[key] if isinstance(r, dict)]
                    break
        if not rows:
            import csv
            import io

            try:
                reader = csv.DictReader(io.StringIO(text))
                rows = [dict(r) for r in reader if r]
            except csv.Error:
                rows = []
        for row in rows:
            low = {str(k).lower(): v for k, v in row.items()}
            qid = str(low.get("query_id") or low.get("queryid") or "")
            scope = str(low.get("warehouse_name") or low.get("warehouse") or "")
            ex_ms = _num(low.get("execution_time") or low.get("total_elapsed_time"))
            scanned = _num(low.get("bytes_scanned") or low.get("bytes"))
            if qid:
                model.executions.append(
                    RuntimeExecution(id=qid, kind="query", state="completed", duration_ms=ex_ms)
                )
            if ex_ms is not None:
                model.metrics.append(
                    ExecutionMetric("execution_time", ex_ms, "ms", scope=qid or scope)
                )
            if scanned is not None:
                model.metrics.append(
                    ExecutionMetric("bytes_scanned", scanned, "bytes", scope=qid or scope)
                )
            err = str(low.get("error_code") or low.get("error_message") or "")
            if err and err.lower() not in {"0", "none", "null"}:
                model.errors.append(
                    ExecutionError(
                        code=err,
                        message=str(low.get("error_message") or err),
                        execution_id=qid,
                    )
                )
        if rows:
            model.identifiers["rows"] = str(len(rows))
        return model


# --- BigQuery job history exports ----------------------------------------------


class BigQueryJobsAdapter:
    """BigQuery INFORMATION_SCHEMA.JOBS_BY_* export rows.

    Recognizes JSON arrays (or objects with ``rows``/``data``/``jobs``) or
    CSV with ``job_id``/``total_bytes_processed``-style headers —
    deterministic, offline.
    """

    name = "bigquery_jobs"

    _FIELDS = re.compile(
        r'"(job_id|total_bytes_processed|total_slot_ms|statement_type|'
        r'creation_time|project_id)"',
        re.IGNORECASE,
    )

    def matches(self, path: Path, text: str) -> bool:
        head = text[:4000].lower()
        return (
            "job_id" in head
            and (
                "total_bytes_processed" in head
                or "total_slot_ms" in head
                or "statement_type" in head
            )
        ) or bool(self._FIELDS.search(text[:4000]))

    def parse(self, path: Path, text: str) -> RuntimeEvidenceModel:
        model = RuntimeEvidenceModel(source=self.name, artifact=path)
        rows: list[dict[str, Any]] = []
        data = _json_doc(text)
        if isinstance(data, list):
            rows = [r for r in data if isinstance(r, dict)]
        elif isinstance(data, dict):
            for key in ("rows", "data", "results", "jobs"):
                if isinstance(data.get(key), list):
                    rows = [r for r in data[key] if isinstance(r, dict)]
                    break
        if not rows:
            import csv
            import io

            try:
                reader = csv.DictReader(io.StringIO(text))
                rows = [dict(r) for r in reader if r]
            except csv.Error:
                rows = []
        for row in rows:
            low = {str(k).lower(): v for k, v in row.items()}
            jid = str(low.get("job_id") or low.get("jobid") or "")
            scope = str(low.get("project_id") or low.get("project") or "")
            duration = _num(low.get("duration_ms") or low.get("total_ms"))
            if duration is None:
                # creation_time -> end_time delta if both present (ms epoch)
                start = _num(low.get("start_time") or low.get("creation_time_ms"))
                end = _num(low.get("end_time"))
                if start is not None and end is not None and end >= start:
                    duration = end - start
            state = str(low.get("state") or "completed").lower()
            if jid:
                model.executions.append(
                    RuntimeExecution(id=jid, kind="query", state=state, duration_ms=duration)
                )
            processed = _num(low.get("total_bytes_processed") or low.get("bytes_processed"))
            if processed is not None:
                model.metrics.append(
                    ExecutionMetric("bytes_processed", processed, "bytes", scope=jid or scope)
                )
            billed = _num(low.get("total_bytes_billed"))
            if billed is not None:
                model.metrics.append(
                    ExecutionMetric("bytes_billed", billed, "bytes", scope=jid or scope)
                )
            slot_ms = _num(low.get("total_slot_ms") or low.get("slot_ms"))
            if slot_ms is not None:
                model.metrics.append(ExecutionMetric("slot_ms", slot_ms, "ms", scope=jid or scope))
            err = low.get("error_result") or low.get("error")
            err_text = str(err) if err else ""
            if err_text and err_text.lower() not in {"0", "none", "null", "{}"}:
                model.errors.append(
                    ExecutionError(code="job_error", message=err_text[:500], execution_id=jid)
                )
        if rows:
            model.identifiers["rows"] = str(len(rows))
        return model


# --- Redshift query log exports -------------------------------------------------


class RedshiftQueryLogAdapter:
    """Redshift STL_QUERY / SVV_QUERY export rows.

    Recognizes JSON arrays (or objects with ``rows``/``data``) or CSV with
    ``querytxt``/``total_exec_time``/``service_class``-style headers —
    deterministic, offline.
    """

    name = "redshift_query_log"

    _FIELDS = re.compile(
        r'"(query|querytxt|query_text|total_exec_time|service_class|'
        r'starttime|endtime|label|userid)"',
        re.IGNORECASE,
    )

    def matches(self, path: Path, text: str) -> bool:
        head = text[:4000].lower()
        return (
            (
                ("querytxt" in head or "query_text" in head)
                and ("total_exec_time" in head or "starttime" in head or "service_class" in head)
            )
            or ('"service_class"' in head and '"query"' in head)
            or bool(self._FIELDS.search(text[:4000]) and "service_class" in head)
        )

    def parse(self, path: Path, text: str) -> RuntimeEvidenceModel:
        model = RuntimeEvidenceModel(source=self.name, artifact=path)
        rows: list[dict[str, Any]] = []
        data = _json_doc(text)
        if isinstance(data, list):
            rows = [r for r in data if isinstance(r, dict)]
        elif isinstance(data, dict):
            for key in ("rows", "data", "results", "queries"):
                if isinstance(data.get(key), list):
                    rows = [r for r in data[key] if isinstance(r, dict)]
                    break
        if not rows:
            import csv
            import io

            try:
                reader = csv.DictReader(io.StringIO(text))
                rows = [dict(r) for r in reader if r]
            except csv.Error:
                rows = []
        for row in rows:
            low = {str(k).lower(): v for k, v in row.items()}
            qid = str(low.get("query") or low.get("query_id") or "")
            scope = str(low.get("database") or low.get("label") or "")
            ex_ms = _num(low.get("total_exec_time") or low.get("elapsed"))
            queue_ms = _num(low.get("queue_time") or low.get("wlm_queue_time"))
            aborted = str(low.get("aborted") or "0")
            state = "failed" if aborted not in {"", "0", "false"} else "completed"
            if qid:
                model.executions.append(
                    RuntimeExecution(id=qid, kind="query", state=state, duration_ms=ex_ms)
                )
            if ex_ms is not None:
                model.metrics.append(
                    ExecutionMetric("execution_time", ex_ms, "ms", scope=qid or scope)
                )
            if queue_ms is not None:
                model.metrics.append(
                    ExecutionMetric("wlm_queue_time", queue_ms, "ms", scope=qid or scope)
                )
            if state == "failed":
                model.errors.append(
                    ExecutionError(
                        code="aborted",
                        message=str(low.get("querytxt") or "")[:200],
                        execution_id=qid,
                    )
                )
        if rows:
            model.identifiers["rows"] = str(len(rows))
        return model


ADAPTERS: tuple[RuntimeEvidenceAdapter, ...] = (
    SnowflakeHistoryAdapter(),
    BigQueryJobsAdapter(),
    RedshiftQueryLogAdapter(),
    SparkEventLogAdapter(),
    AthenaStatsAdapter(),
    StepFunctionsHistoryAdapter(),
    StreamingProgressAdapter(),
    FlinkCheckpointAdapter(),
    StreamMetricsAdapter(),
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
