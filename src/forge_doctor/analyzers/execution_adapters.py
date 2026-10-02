"""Engine adapters: exported artifacts -> normalized QueryExecution spine.

Each adapter recognizes one engine's export shape (event log, query
history CSV/JSON, query JSON) and emits ``QueryExecution`` objects
carrying only fields the artifact demonstrably contains — a missing
metric stays ``None`` rather than being guessed.

All input is untrusted: parsers never execute, never network, and
degrade to an empty list on malformed content. Detection is ordered and
first-match-wins; an artifact claims at most one adapter.
"""

from __future__ import annotations

import json
import re
from dataclasses import fields as _dc_fields
from pathlib import Path
from typing import Any, Protocol

from forge_doctor.core.execution_model import (
    ExecutionJoin,
    ExecutionScan,
    ExecutionStage,
    ExecutionStatus,
    JoinStrategy,
    QueryExecution,
    StageKind,
    derive_metrics,
    fingerprint_sql,
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


def _rows(text: str) -> list[dict[str, Any]]:
    """JSON array / wrapped-array / CSV-DictReader rows, lowercase keys."""
    data = _json_doc(text)
    raw: list[dict[str, Any]] = []
    if isinstance(data, list):
        raw = [r for r in data if isinstance(r, dict)]
    elif isinstance(data, dict):
        for key in ("rows", "data", "results", "queries", "jobs", "query_log"):
            if isinstance(data.get(key), list):
                raw = [r for r in data[key] if isinstance(r, dict)]
                break
    if not raw:
        import csv
        import io

        try:
            raw = [dict(r) for r in csv.DictReader(io.StringIO(text)) if r]
        except csv.Error:
            raw = []
    return [{str(k).lower(): v for k, v in r.items()} for r in raw]


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ints(text: str | None) -> float | None:
    if not text:
        return None
    return _num(re.sub(r"[^\d.eE+-]", "", str(text)))


_TRINO_DUR = re.compile(r"^\s*([\d.eE+-]+)\s*(ms|s|m|h|us|ns)\s*$", re.IGNORECASE)
_TRINO_MS = {"us": 1e-3, "ns": 1e-6, "ms": 1.0, "s": 1e3, "m": 6e4, "h": 36e5}


def _trino_ms(value: Any) -> float | None:
    """Trino duration strings ('2.5s', '300ms') -> ms; bare numbers -> as-is."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = _TRINO_DUR.match(str(value))
    if m:
        return float(m.group(1)) * _TRINO_MS[m.group(2).lower()]
    return _ints(str(value))


def _trino_bytes(value: Any) -> float | None:
    """Trino data-size strings ('10MB', '1.4kB') -> bytes."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.match(r"^\s*([\d.eE+-]+)\s*(kb|mb|gb|tb|b|bytes?)?\s*$", str(value), re.IGNORECASE)
    if not m:
        return None
    mult = {
        "b": 1.0,
        "byte": 1.0,
        "bytes": 1.0,
        "kb": 1e3,
        "mb": 1e6,
        "gb": 1e9,
        "tb": 1e12,
    }
    unit = (m.group(2) or "b").lower()
    return float(m.group(1)) * mult.get(unit, 1.0)


def _status(raw: str) -> ExecutionStatus:
    r = raw.lower()
    if r in {"finished", "completed", "success", "succeeded", "ok", "done"}:
        return ExecutionStatus.COMPLETED
    if r in {"failed", "error", "aborted", "job_state_failed"}:
        return ExecutionStatus.FAILED
    if r in {"cancelled", "canceled", "killed", "job_state_cancelled"}:
        return ExecutionStatus.CANCELLED
    if r in {"running", "queued", "started", "active"}:
        return ExecutionStatus.RUNNING
    return ExecutionStatus.UNKNOWN


def _epoch_ms(value: Any) -> float | None:
    """Numeric epoch-ms, or ISO-8601 string -> epoch ms (UTC)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    n = _num(text)
    if n is not None:
        return n
    import datetime as _dt

    iso = text.replace("Z", "+00:00")
    try:
        dt = _dt.datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.UTC)
    return dt.timestamp() * 1000.0


class ExecutionAdapter(Protocol):
    """Artifact parser emitting ``QueryExecution`` spines."""

    name: str
    engine: str

    def matches(self, path: Path, text: str) -> bool: ...

    def parse(self, path: Path, text: str) -> list[QueryExecution]: ...


def _evidence(name: str, path: Path, row: str = "") -> tuple[str, ...]:
    ev = [f"adapter:{name}", f"artifact:{path.as_posix()}"]
    if row:
        ev.append(f"row:{row}")
    return tuple(ev)


# ---------------------------------------------------------------------------
# Spark — event log -> one QueryExecution per SQL execution (or per job)
# ---------------------------------------------------------------------------


class SparkExecutionAdapter:
    """SparkListener event log -> executions with stage-level metrics."""

    name = "spark_execution"
    engine = "spark"

    def matches(self, path: Path, text: str) -> bool:
        return '"Event"' in text and '"SparkListener' in text[:20000]

    def parse(self, path: Path, text: str) -> list[QueryExecution]:
        events = _json_lines(text)
        sql_texts: dict[str, str] = {}
        job_sql: dict[str, str] = {}
        stages: dict[str, dict[str, Any]] = {}
        stage_job: dict[str, str] = {}
        task_durations: dict[str, list[float]] = {}
        app_id = ""
        job_order: list[str] = []

        for ev in events:
            kind = ev.get("Event", "")
            if kind == "SparkListenerApplicationStart":
                app_id = str(ev.get("App ID") or ev.get("App Name") or "")
            elif kind == "SparkListenerSQLExecutionStart":
                eid = str(ev.get("Execution ID", ""))
                sql_texts[eid] = str(ev.get("Description") or ev.get("Spark Plan Info") or "")
            elif kind == "SparkListenerJobStart":
                jid = str(ev.get("Job ID", ""))
                job_order.append(jid)
                props = ev.get("Properties") or {}
                sql_id = str(props.get("spark.sql.execution.id") or "")
                if sql_id:
                    job_sql[jid] = sql_id
                for sid in ev.get("Stage IDs") or []:
                    stage_job[str(sid)] = jid
            elif kind == "SparkListenerStageCompleted":
                info = ev.get("Stage Info") or {}
                sid = str(info.get("Stage ID", ""))
                accs: dict[str, float] = {}
                for acc in info.get("Accumulables", []) or []:
                    aname = str(acc.get("Name", "")).lower()
                    val = _num(acc.get("Value"))
                    if val is not None:
                        accs[aname] = val
                submitted = _num(info.get("Submission Time"))
                completed = _num(info.get("Completion Time"))
                stages[sid] = {
                    "tasks": _num(info.get("Number of Tasks")),
                    "duration": (completed - submitted)
                    if submitted is not None and completed is not None
                    else None,
                    "accs": accs,
                    "details": str(info.get("Details") or ""),
                }
            elif kind == "SparkListenerTaskEnd":
                info = ev.get("Task Info") or {}
                sid = str(info.get("Stage ID", ""))
                metrics = ev.get("Task Metrics") or {}
                run_ms = _num(metrics.get("Executor Run Time"))
                if run_ms is not None:
                    task_durations.setdefault(sid, []).append(run_ms)
                # task metrics into the stage accumulator
                st = stages.setdefault(sid, {"accs": {}})
                sa = st["accs"]
                m = {
                    "internal.metrics.input.bytesread": metrics.get("Input Metrics", {}).get(
                        "Bytes Read"
                    ),
                    "internal.metrics.output.byteswritten": metrics.get("Output Metrics", {}).get(
                        "Bytes Written"
                    ),
                    "internal.metrics.shuffle.read.bytesread": metrics.get(
                        "Shuffle Read Metrics", {}
                    ).get("Total Bytes Read"),
                    "internal.metrics.shuffle.write.byteswritten": metrics.get(
                        "Shuffle Write Metrics", {}
                    ).get("Shuffle Bytes Written")
                    or metrics.get("Shuffle Write Metrics", {}).get("Bytes Written"),
                    "internal.metrics.memorybytesspilled": metrics.get("Memory Bytes Spilled"),
                    "internal.metrics.diskbytesspilled": metrics.get("Disk Bytes Spilled"),
                    "internal.metrics.executorruntime": metrics.get("Executor Run Time"),
                    "internal.metrics.peakexecutionmemory": metrics.get("Peak Execution Memory"),
                }
                for k, v in m.items():
                    val = _num(v)
                    if val is not None:
                        sa[k] = sa.get(k, 0.0) + val

        # Group stages by owning job -> execution
        by_job: dict[str, list[ExecutionStage]] = {}
        skews: dict[str, float] = {}
        for sid, st in sorted(stages.items()):
            accs = st.get("accs", {})
            durations = task_durations.get(sid, [])
            skew_val = None
            if len(durations) >= 4:
                ordered = sorted(durations)
                median = ordered[len(ordered) // 2]
                if median > 0:
                    skew_val = round(ordered[-1] / median, 3)
                    skews[sid] = skew_val
            shuffle_bytes = None
            sr = accs.get("internal.metrics.shuffle.read.bytesread")
            sw = accs.get("internal.metrics.shuffle.write.byteswritten")
            if sr is not None or sw is not None:
                shuffle_bytes = (sr or 0.0) + (sw or 0.0)
            spill = None
            sm = accs.get("internal.metrics.memorybytesspilled")
            sd = accs.get("internal.metrics.diskbytesspilled")
            if sm is not None or sd is not None:
                spill = (sm or 0.0) + (sd or 0.0)
            input_b = accs.get("internal.metrics.input.bytesread")
            output_b = accs.get("internal.metrics.output.byteswritten")
            s_kind = StageKind.UNKNOWN
            if input_b and not shuffle_bytes:
                s_kind = StageKind.SCAN
            elif shuffle_bytes:
                s_kind = StageKind.SHUFFLE
            joins: tuple[ExecutionJoin, ...] = ()
            details = st.get("details") or ""
            if "BroadcastHashJoin" in details or "BroadcastNestedLoop" in details:
                joins = (ExecutionJoin(strategy=JoinStrategy.BROADCAST, skew=skew_val),)
                s_kind = StageKind.JOIN
            elif "SortMergeJoin" in details:
                joins = (ExecutionJoin(strategy=JoinStrategy.SORT_MERGE, skew=skew_val),)
                s_kind = StageKind.JOIN
            elif "ShuffledHashJoin" in details:
                joins = (ExecutionJoin(strategy=JoinStrategy.SHUFFLE_HASH, skew=skew_val),)
                s_kind = StageKind.JOIN
            stage = ExecutionStage(
                id=f"stage-{sid}",
                kind=s_kind,
                duration_ms=st.get("duration"),
                input_bytes=input_b,
                output_bytes=output_b,
                cpu_ms=accs.get("internal.metrics.executorruntime"),
                memory_peak=accs.get("internal.metrics.peakexecutionmemory"),
                spill_bytes=spill,
                shuffle_bytes=shuffle_bytes,
                joins=joins,
            )
            by_job.setdefault(stage_job.get(sid, ""), []).append(stage)

        out: list[QueryExecution] = []
        for jid in sorted(by_job, key=lambda j: (j == "", j)):
            exec_stages = tuple(by_job[jid])
            sql_id = job_sql.get(jid, "")
            sql_text = sql_texts.get(sql_id, "")
            spills = [s.spill_bytes for s in exec_stages if s.spill_bytes is not None]
            input_bytes = [s.input_bytes for s in exec_stages if s.input_bytes is not None]
            cpu = [s.cpu_ms for s in exec_stages if s.cpu_ms is not None]
            ex = QueryExecution(
                execution_id=f"spark:{app_id}:{sql_id or ('job-' + jid)}",
                engine="spark",
                query_id=sql_id or f"job-{jid}",
                query_fingerprint=fingerprint_sql(sql_text) if sql_text else "",
                duration_ms=sum(s.duration_ms for s in exec_stages if s.duration_ms is not None)
                or None,
                status=ExecutionStatus.COMPLETED,
                stages=exec_stages,
                bytes_read=sum(input_bytes) if input_bytes else None,
                cpu_time_ms=sum(cpu) if cpu else None,
                spill_bytes=sum(spills) if spills else None,
                network_bytes=None,
                evidence=_evidence(self.name, path, f"job-{jid}"),
            )
            out.append(_finalize(ex))
        return out


def _finalize(ex: QueryExecution) -> QueryExecution:
    """Attach derived metrics to a completed QueryExecution."""
    return QueryExecution(
        **{f.name: getattr(ex, f.name) for f in _QEX_FIELDS},
        metrics=derive_metrics(ex),
    )


_QEX_FIELDS = tuple(f for f in _dc_fields(QueryExecution) if f.name != "metrics")


# ---------------------------------------------------------------------------
# Snowflake — QUERY_HISTORY / profile export rows
# ---------------------------------------------------------------------------


class SnowflakeExecutionAdapter:
    name = "snowflake_execution"
    engine = "snowflake"

    _FIELDS = re.compile(
        r'"(query_id|query_text|execution_time|bytes_scanned|warehouse_name|'
        r'partitions_scanned|partitions_total|bytes_spilled|queued_overload_time)"',
        re.IGNORECASE,
    )

    def matches(self, path: Path, text: str) -> bool:
        head = text[:4000].lower()
        if ("query_id" in head or "queryid" in head) and (
            "query_text" in head or "execution_time" in head
        ):
            return True
        hits = set(self._FIELDS.findall(text[:4000].lower()))
        return len(hits) >= 2

    def parse(self, path: Path, text: str) -> list[QueryExecution]:
        out: list[QueryExecution] = []
        for row in _rows(text):
            qid = str(row.get("query_id") or row.get("queryid") or "")
            if not qid:
                continue
            sql = str(row.get("query_text") or row.get("querytext") or "")
            scanned = _num(row.get("bytes_scanned") or row.get("bytes"))
            pscan = _num(row.get("partitions_scanned"))
            ptot = _num(row.get("partitions_total"))
            spill = _num(
                row.get("bytes_spilled_to_local_storage")
                or row.get("bytes_spilled")
                or row.get("spill_bytes")
            )
            queue = _num(
                row.get("queued_overload_time")
                or row.get("queued_provisioning_time")
                or row.get("queue_time")
            )
            dur = _num(row.get("execution_time") or row.get("total_elapsed_time"))
            scans: tuple[ExecutionScan, ...] = ()
            if scanned is not None or pscan is not None:
                scans = (
                    ExecutionScan(
                        bytes_scanned=scanned,
                        partitions_scanned=pscan,
                        partitions_total=ptot,
                        pruning=(
                            round(pscan / ptot, 6)
                            if ptot is not None and ptot > 0 and pscan is not None
                            else None
                        ),
                    ),
                )
            stages: tuple[ExecutionStage, ...] = ()
            if scans:
                stages = (
                    ExecutionStage(
                        id="scan-0",
                        kind=StageKind.SCAN,
                        input_bytes=scanned,
                        output_bytes=scanned,
                        scans=scans,
                    ),
                )
            ex = QueryExecution(
                execution_id=f"snowflake:{qid}",
                engine="snowflake",
                query_id=qid,
                query_fingerprint=fingerprint_sql(sql) if sql else "",
                duration_ms=dur,
                queue_time_ms=queue,
                status=_status(str(row.get("execution_status") or "success")),
                stages=stages,
                bytes_read=scanned,
                spill_bytes=spill,
                evidence=_evidence(self.name, path, qid),
            )
            out.append(_finalize(ex))
        return out


# ---------------------------------------------------------------------------
# BigQuery — INFORMATION_SCHEMA.JOBS* exports
# ---------------------------------------------------------------------------


class BigQueryExecutionAdapter:
    name = "bigquery_execution"
    engine = "bigquery"

    def matches(self, path: Path, text: str) -> bool:
        head = text[:4000].lower()
        return "job_id" in head and (
            "total_bytes_processed" in head
            or "total_slot_ms" in head
            or "querystages" in head
            or "statement_type" in head
        )

    def parse(self, path: Path, text: str) -> list[QueryExecution]:
        out: list[QueryExecution] = []
        for row in _rows(text):
            jid = str(row.get("job_id") or row.get("jobid") or "")
            if not jid:
                continue
            sql = str(row.get("query") or row.get("query_text") or "")
            processed = _num(row.get("total_bytes_processed") or row.get("bytes_processed"))
            billed = _num(row.get("total_bytes_billed"))
            slot_ms = _num(row.get("total_slot_ms") or row.get("slot_ms"))
            start = _epoch_ms(row.get("start_time") or row.get("creation_time"))
            end = _epoch_ms(row.get("end_time"))
            dur = _num(row.get("duration_ms") or row.get("total_ms"))
            if dur is None and start is not None and end is not None and end >= start:
                dur = end - start
            shuffle_out = _num(row.get("shuffle_output_bytes") or row.get("shuffle_bytes"))
            stages: list[ExecutionStage] = []
            raw_stages = row.get("querystages") or row.get("query_stages") or []
            if isinstance(raw_stages, str) and raw_stages.strip().startswith("["):
                raw_stages = _json_doc(raw_stages) or []
            if isinstance(raw_stages, list):
                for i, st in enumerate(raw_stages):
                    if not isinstance(st, dict):
                        continue
                    low = {str(k).lower(): v for k, v in st.items()}
                    stages.append(
                        ExecutionStage(
                            id=str(low.get("name") or f"stage-{i}"),
                            kind=_bq_stage_kind(str(low.get("name") or "")),
                            duration_ms=_num(low.get("duration_ms") or low.get("elapsed_ms")),
                            input_bytes=_num(low.get("input_bytes")),
                            output_bytes=_num(low.get("output_bytes")),
                            shuffle_bytes=_num(
                                low.get("shuffle_output_bytes") or low.get("shuffle_bytes")
                            ),
                            cpu_ms=_num(low.get("slot_ms") or low.get("compute_ms")),
                            input_rows=_num(low.get("records_read") or low.get("input_rows")),
                            output_rows=_num(low.get("records_written") or low.get("output_rows")),
                        )
                    )
            stages_tuple = tuple(stages)
            if not stages_tuple and processed is not None:
                stages_tuple = (
                    ExecutionStage(
                        id="scan-0",
                        kind=StageKind.SCAN,
                        input_bytes=processed,
                        output_bytes=processed,
                        scans=(ExecutionScan(bytes_scanned=processed),),
                    ),
                )
            ex = QueryExecution(
                execution_id=f"bigquery:{jid}",
                engine="bigquery",
                query_id=jid,
                query_fingerprint=fingerprint_sql(sql) if sql else "",
                start_time=start,
                end_time=end,
                duration_ms=dur,
                status=_status(str(row.get("state") or "DONE")),
                stages=stages_tuple,
                bytes_read=processed,
                bytes_written=billed,
                cpu_time_ms=slot_ms,
                network_bytes=shuffle_out,
                evidence=_evidence(self.name, path, jid),
            )
            out.append(_finalize(ex))
        return out


def _bq_stage_kind(name: str) -> StageKind:
    low = name.lower()
    if "scan" in low or "read" in low:
        return StageKind.SCAN
    if "join" in low:
        return StageKind.JOIN
    if "agg" in low:
        return StageKind.AGGREGATION
    if "sort" in low or "order" in low:
        return StageKind.SORT
    if "write" in low:
        return StageKind.WRITE
    return StageKind.UNKNOWN


# ---------------------------------------------------------------------------
# Redshift — STL_QUERY / SVL_* / SYS_QUERY_HISTORY exports
# ---------------------------------------------------------------------------


class RedshiftExecutionAdapter:
    name = "redshift_execution"
    engine = "redshift"

    def matches(self, path: Path, text: str) -> bool:
        head = text[:4000].lower()
        return (
            ("querytxt" in head or "query_text" in head)
            and (
                "total_exec_time" in head
                or "starttime" in head
                or "service_class" in head
                or "elapsed_time" in head
            )
        ) or ('"service_class"' in head and '"query"' in head)

    def parse(self, path: Path, text: str) -> list[QueryExecution]:
        out: list[QueryExecution] = []
        for row in _rows(text):
            qid = str(row.get("query") or row.get("query_id") or "")
            if not qid:
                continue
            sql = str(row.get("querytxt") or row.get("query_text") or "")
            dur = _num(
                row.get("total_exec_time")
                or row.get("elapsed")
                or row.get("elapsed_time")
                or row.get("execution_time")
            )
            # STL durations are microseconds; elapsed_time often ms. Heuristic:
            # keep the raw value; unit uncertainty is documented, never
            # normalized beyond the artifact's own column.
            queue = _num(row.get("queue_time") or row.get("wlm_queue_time") or row.get("queue_ms"))
            rows_r = _num(row.get("rows") or row.get("rows_read") or row.get("output_rows"))
            aborted = str(row.get("aborted") or "0").lower()
            err = str(row.get("error_message") or row.get("error") or "")
            state = (
                ExecutionStatus.FAILED
                if aborted not in {"", "0", "false"} or (err and err.lower() != "none")
                else ExecutionStatus.COMPLETED
            )
            spill = _num(
                row.get("spill_bytes") or row.get("bytes_spilled") or row.get("disk_spill")
            )
            stages: tuple[ExecutionStage, ...] = ()
            if rows_r is not None:
                stages = (
                    ExecutionStage(
                        id="scan-0",
                        kind=StageKind.SCAN,
                        output_rows=rows_r,
                        spill_bytes=spill,
                    ),
                )
            ex = QueryExecution(
                execution_id=f"redshift:{qid}",
                engine="redshift",
                query_id=qid,
                query_fingerprint=fingerprint_sql(sql) if sql else "",
                duration_ms=dur,
                queue_time_ms=queue,
                status=state,
                stages=stages,
                rows_read=rows_r,
                spill_bytes=spill,
                evidence=_evidence(self.name, path, qid),
            )
            out.append(_finalize(ex))
        return out


# ---------------------------------------------------------------------------
# Trino — query JSON (v1/query/<id> or event-listener export)
# ---------------------------------------------------------------------------


class TrinoExecutionAdapter:
    name = "trino_execution"
    engine = "trino"

    def matches(self, path: Path, text: str) -> bool:
        head = text[:4000].lower()
        return '"queryid"' in head and (
            '"querystats"' in head or '"outputstageinfos"' in head or '"session"' in head
        )

    _OP_KINDS: tuple[tuple[str, StageKind], ...] = (
        ("tablescan", StageKind.SCAN),
        ("scanfilterproject", StageKind.SCAN),
        ("exchange", StageKind.EXCHANGE),
        ("remote", StageKind.REMOTE_READ),
        ("hashjoin", StageKind.JOIN),
        ("lookupjoin", StageKind.JOIN),
        ("join", StageKind.JOIN),
        ("aggregation", StageKind.AGGREGATION),
        ("partialaggregation", StageKind.AGGREGATION),
        ("sort", StageKind.SORT),
        ("window", StageKind.WINDOW),
        ("tablewrite", StageKind.WRITE),
        ("output", StageKind.WRITE),
    )

    def parse(self, path: Path, text: str) -> list[QueryExecution]:
        doc = _json_doc(text)
        docs = doc if isinstance(doc, list) else [doc] if isinstance(doc, dict) else []
        out: list[QueryExecution] = []
        for item in docs:
            low = {str(k).lower(): v for k, v in item.items()}
            qid = str(low.get("queryid") or low.get("query_id") or "")
            if not qid:
                continue
            stats = low.get("querystats") or low.get("stats") or {}
            stats = {str(k).lower(): v for k, v in stats.items()} if isinstance(stats, dict) else {}
            sql = str(low.get("query") or low.get("preparedquery") or "")
            stages: list[ExecutionStage] = []
            stage_infos = low.get("outputstageinfos") or low.get("stages") or []
            if isinstance(stage_infos, dict):
                stage_infos = stage_infos.get("stages") or []
            if isinstance(stage_infos, list):
                for i, st in enumerate(stage_infos):
                    if not isinstance(st, dict):
                        continue
                    sl = {str(k).lower(): v for k, v in st.items()}
                    sstats = sl.get("stagestats") or sl.get("stats") or {}
                    sstats = (
                        {str(k).lower(): v for k, v in sstats.items()}
                        if isinstance(sstats, dict)
                        else {}
                    )
                    ops = sl.get("operatortypes") or sl.get("operators") or []
                    kind = StageKind.UNKNOWN
                    opnames = [str(o).lower() for o in ops] if isinstance(ops, list) else []
                    for pat, k in self._OP_KINDS:
                        if any(pat in o for o in opnames):
                            kind = k
                            break
                    stages.append(
                        ExecutionStage(
                            id=str(sl.get("stageid") or sl.get("id") or f"stage-{i}"),
                            kind=kind,
                            duration_ms=_trino_ms(
                                sstats.get("totalcputime") or sstats.get("elapsedtime")
                            ),
                            input_rows=_num(sstats.get("rawinputrows")),
                            output_rows=_num(sstats.get("outputrows")),
                            input_bytes=_trino_bytes(sstats.get("rawinputdatasize")),
                            output_bytes=_trino_bytes(sstats.get("outputdatasize")),
                            cpu_ms=_trino_ms(sstats.get("totalcputime")),
                            memory_peak=_trino_bytes(sstats.get("peakusermemoryreservation")),
                            spill_bytes=_trino_bytes(sstats.get("spilleddatasize")),
                            shuffle_bytes=_trino_bytes(sstats.get("shuffledatasize")),
                        )
                    )
            dur = _trino_ms(stats.get("elapsedtime") or stats.get("totaltime"))
            queue = _trino_ms(stats.get("queuedtime"))
            processed = _trino_bytes(
                stats.get("processedinputdatasize") or stats.get("rawinputdatasize")
            )
            ex = QueryExecution(
                execution_id=f"trino:{qid}",
                engine="trino",
                query_id=qid,
                query_fingerprint=fingerprint_sql(sql) if sql else "",
                duration_ms=dur,
                queue_time_ms=queue,
                status=_status(str(low.get("state") or "")),
                stages=tuple(stages),
                bytes_read=processed,
                rows_read=_num(stats.get("processedinputrows") or stats.get("rawinputrows")),
                cpu_time_ms=_trino_ms(stats.get("totalcputime")),
                memory_peak=_trino_bytes(stats.get("peakusermemoryreservation")),
                spill_bytes=_trino_bytes(stats.get("spilleddatasize")),
                evidence=_evidence(self.name, path, qid),
            )
            out.append(_finalize(ex))
        return out


# ---------------------------------------------------------------------------
# ClickHouse — system.query_log / query_thread_log / parts exports
# ---------------------------------------------------------------------------


class ClickHouseExecutionAdapter:
    name = "clickhouse_execution"
    engine = "clickhouse"

    def matches(self, path: Path, text: str) -> bool:
        head = text[:4000].lower()
        return (
            ("query_duration_ms" in head or "read_rows" in head)
            and ("query_id" in head or "query" in head)
            and ("read_bytes" in head or "memory_usage" in head or "result_rows" in head)
        )

    def parse(self, path: Path, text: str) -> list[QueryExecution]:
        out: list[QueryExecution] = []
        for row in _rows(text):
            qid = str(row.get("query_id") or row.get("queryid") or "")
            sql = str(row.get("query") or row.get("normalized_query_hash") or "")
            if not qid and not sql:
                continue
            read_rows = _num(row.get("read_rows"))
            read_bytes = _num(row.get("read_bytes"))
            written_rows = _num(row.get("written_rows") or row.get("result_rows"))
            dur = _num(row.get("query_duration_ms") or row.get("duration_ms"))
            mem = _num(row.get("memory_usage") or row.get("peak_memory_usage"))
            stages: tuple[ExecutionStage, ...] = ()
            if read_bytes is not None or read_rows is not None:
                stages = (
                    ExecutionStage(
                        id="scan-0",
                        kind=StageKind.SCAN,
                        input_bytes=read_bytes,
                        output_rows=written_rows,
                        memory_peak=mem,
                        scans=(
                            ExecutionScan(
                                bytes_scanned=read_bytes,
                                rows_scanned=read_rows,
                            ),
                        ),
                    ),
                )
            ex = QueryExecution(
                execution_id=f"clickhouse:{qid or fingerprint_sql(sql)}",
                engine="clickhouse",
                query_id=qid,
                query_fingerprint=fingerprint_sql(sql) if sql else "",
                duration_ms=dur,
                status=_status(str(row.get("type") or row.get("status") or "QueryFinish")),
                stages=stages,
                bytes_read=read_bytes,
                rows_read=read_rows,
                rows_written=written_rows,
                memory_peak=mem,
                evidence=_evidence(self.name, path, qid or "row"),
            )
            out.append(_finalize(ex))
        return out


# ---------------------------------------------------------------------------
# Registry — first match wins; order matters only for overlap
# ---------------------------------------------------------------------------

EXECUTION_ADAPTERS: tuple[ExecutionAdapter, ...] = (
    SparkExecutionAdapter(),
    SnowflakeExecutionAdapter(),
    BigQueryExecutionAdapter(),
    RedshiftExecutionAdapter(),
    TrinoExecutionAdapter(),
    ClickHouseExecutionAdapter(),
)


def ingest_executions(path: Path, adapter: str | None = None) -> tuple[str, list[QueryExecution]]:
    """Parse an exported artifact into normalized ``QueryExecution`` rows.

    ``adapter`` forces a named adapter; otherwise the first matching
    adapter claims the artifact. Returns ``(adapter_name, executions)``;
    unmatched artifacts return ``("unknown", [])``.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ("unreadable", [])
    for candidate in EXECUTION_ADAPTERS:
        if adapter and candidate.name != adapter:
            continue
        try:
            if candidate.matches(path, text):
                return (candidate.name, candidate.parse(path, text))
        except Exception:  # untrusted input — degrade to no-claim
            continue
    return ("unknown", [])
