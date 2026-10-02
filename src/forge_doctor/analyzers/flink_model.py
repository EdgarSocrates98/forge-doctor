"""Flink deep-intelligence model.

Detects PyFlink/Java Flink jobs and their building blocks — sources,
operators, keyed state, windows, timers, checkpointing, savepoints,
parallelism, sinks — plus managed deployments (KinesisAnalyticsV2 /
``ManagedServiceForApacheFlink``). Offline only.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forge_doctor.analyzers.index import project_index
from forge_doctor.analyzers.kinesis_model import kinesis_model
from forge_doctor.analyzers.terraform_model import terraform_model
from forge_doctor.core.context import ProjectContext


@dataclass(frozen=True)
class FlinkEvidence:
    # env|source|keyed_op|window|timer|checkpoint|savepoint|parallelism|
    # sink|delivery_mode|state_backend|watermark
    kind: str
    detail: str  # API name / option value
    file: Path
    line: int


@dataclass(frozen=True)
class FlinkJob:
    name: str  # entrypoint or managed-app name
    file: Path
    line: int
    source: str = "code"  # code|terraform|cloudformation
    runtime_env: str = ""  # FLINK-1.xx (managed)
    parallelism: int = 0
    autoscaling: bool = False


@dataclass
class FlinkProjectModel:
    """All Flink evidence in a project, deterministically ordered."""

    jobs: list[FlinkJob] = field(default_factory=list)
    evidence: list[FlinkEvidence] = field(default_factory=list)
    has_flink: bool = False
    has_env: bool = False
    has_keyed_state: bool = False
    has_window: bool = False
    has_timer: bool = False
    has_checkpoint: bool = False
    has_savepoint: bool = False
    checkpoint_mode: str = ""  # EXACTLY_ONCE|AT_LEAST_ONCE|""
    checkpoint_interval_ms: int = 0
    watermark_strategy: bool = False

    def evidence_of(self, kind: str) -> list[FlinkEvidence]:
        return [e for e in self.evidence if e.kind == kind]


_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    # (regex, kind, kind-detail-extractor key) — detail is match group 1 or matched token
    ("env", re.compile(r"StreamExecutionEnvironment\.", re.MULTILINE), ""),
    (
        "env",
        re.compile(r"from\s+pyflink\.datastream|import\s+org\.apache\.flink", re.MULTILINE),
        "",
    ),
    ("source", re.compile(r"\.add_source\(|\.addSource\(", re.MULTILINE), ""),
    ("source", re.compile(r"\.from_source\(|\.fromSource\(", re.MULTILINE), ""),
    ("keyed_op", re.compile(r"\.key_by\(|\.keyBy\(", re.MULTILINE), ""),
    (
        "keyed_op",
        re.compile(
            r"(KeyedProcessFunction|ProcessFunction|KeyedBroadcastProcessFunction|"
            r"ValueState|MapState|ListState|ReducingState|AggregatingState|"
            r"getRuntimeContext|\.getState\()"
        ),
        "",
    ),
    ("window", re.compile(r"\.window(?:_all)?\(|\.windowAll\(", re.MULTILINE), ""),
    (
        "window",
        re.compile(
            r"(TumblingEventTimeWindows|TumblingProcessingTimeWindows|"
            r"SlidingEventTimeWindows|SlidingProcessingTimeWindows|"
            r"SessionWindow|GlobalWindows)"
        ),
        "",
    ),
    ("timer", re.compile(r"register_timer|registerTimer|on_timer\(|onTimer\(", re.MULTILINE), ""),
    (
        "checkpoint",
        re.compile(
            r"enable_checkpointing\((\d+)\)|enableCheckpointing\((\d+)\)|"
            r"checkpoint_interval|execution\.checkpointing|env\.getCheckpointConfig"
        ),
        "interval",
    ),
    ("savepoint", re.compile(r"savepoint|Savepoint", re.MULTILINE), ""),
    (
        "parallelism",
        re.compile(r"set_parallelism\((\d+)\)|setParallelism\((\d+)\)", re.MULTILINE),
        "parallelism",
    ),
    ("sink", re.compile(r"\.add_sink\(|\.addSink\(|\.sink_to\(|\.sinkTo\(", re.MULTILINE), ""),
    (
        "delivery_mode",
        re.compile(r"(EXACTLY_ONCE|AT_LEAST_ONCE|AT_MOST_ONCE|DeliveryGuarantee)", re.MULTILINE),
        "mode",
    ),
    (
        "state_backend",
        re.compile(r"StateBackend|RocksDBStateBackend|HashMapStateBackend", re.MULTILINE),
        "",
    ),
    (
        "watermark",
        re.compile(
            r"WatermarkStrategy|assign_timestamps|assignTimestampsAndWatermarks", re.MULTILINE
        ),
        "",
    ),
]

_TF_MANAGED = {"aws_kinesisanalyticsv2_application"}


def _line_no(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _tf_job(b: Any) -> FlinkJob:
    body = getattr(b, "body", "")
    attrs = getattr(b, "attrs", {})
    pm = re.search(r"parallelism\s*=\s*(\d+)", body)
    try:
        parallelism = int(pm.group(1)) if pm else 0
    except ValueError:
        parallelism = 0
    return FlinkJob(
        name=str(attrs.get("name") or (b.labels[-1] if getattr(b, "labels", []) else "")),
        file=Path(getattr(b, "file", "")),
        line=getattr(b, "line", 0),
        source="terraform",
        runtime_env=str(attrs.get("runtime_environment") or ""),
        parallelism=parallelism,
        autoscaling=bool(re.search(r'auto_scaling_enabled\s*=\s*"?true', body, re.IGNORECASE)),
    )


def flink_model(ctx: ProjectContext) -> FlinkProjectModel:
    """Collect Flink evidence across python/java sources + deployments."""
    model = FlinkProjectModel()
    seen: set[tuple[str, str, int, str]] = set()

    index = project_index(ctx)
    for module in index.modules.values():
        text = ctx.read_text(module.file) or ""
        if not (
            "flink" in text.lower()
            or "pyflink" in text.lower()
            or "StreamExecutionEnvironment" in text
            or "WatermarkStrategy" in text
            or "enable_checkpointing" in text
            or "enableCheckpointing" in text
            or "add_source(" in text
            or "addSource(" in text
        ):
            continue
        for kind_name, pat, extractor in _PATTERNS:
            for m in pat.finditer(text):
                detail = ""
                if extractor == "interval":
                    detail = m.group(1) or m.group(2) or ""
                    with contextlib.suppress(ValueError):
                        model.checkpoint_interval_ms = int(detail) or model.checkpoint_interval_ms
                elif extractor == "parallelism":
                    detail = m.group(1) or m.group(2) or ""
                elif extractor == "mode":
                    detail = m.group(1)
                    if detail.startswith("DeliveryGuarantee"):
                        detail = ""
                    elif detail:
                        model.checkpoint_mode = model.checkpoint_mode or detail
                else:
                    detail = m.group(0)[:60]
                key = (kind_name, module.file.as_posix(), _line_no(text, m.start()), detail)
                if key in seen:
                    continue
                seen.add(key)
                model.evidence.append(
                    FlinkEvidence(
                        kind=kind_name,
                        detail=detail,
                        file=module.file,
                        line=_line_no(text, m.start()),
                    )
                )
        if re.search(
            r"StreamExecutionEnvironment\.get_execution_environment|"
            r"StreamExecutionEnvironment\.getExecutionEnvironment",
            text,
        ):
            model.jobs.append(FlinkJob(name=module.file.stem, file=module.file, line=1))

    for b in terraform_model(ctx).resources:
        rtype = b.labels[0] if b.labels else ""
        if rtype in _TF_MANAGED:
            model.jobs.append(_tf_job(b))

    # Managed flink apps discovered via the kinesis layer (CFN included).
    for app in kinesis_model(ctx).flink_apps:
        model.jobs.append(
            FlinkJob(
                name=app.name,
                file=app.file,
                line=app.line,
                source=app.source,
                runtime_env=app.runtime_env,
                parallelism=app.parallelism,
                autoscaling=app.autoscaling,
            )
        )

    # Dedupe managed apps (terraform + kinesis paths both scan TF).
    uniq: dict[tuple[str, str, int], FlinkJob] = {}
    for j in model.jobs:
        uniq[(j.source, j.file.as_posix(), j.line)] = j
    model.jobs = sorted(uniq.values(), key=lambda j: (j.file.as_posix(), j.line, j.name))

    model.evidence.sort(key=lambda e: (e.file.as_posix(), e.line, e.kind))
    model.has_env = bool(model.evidence_of("env"))
    model.has_keyed_state = bool(model.evidence_of("keyed_op"))
    model.has_window = bool(model.evidence_of("window"))
    model.has_timer = bool(model.evidence_of("timer"))
    model.has_checkpoint = bool(model.evidence_of("checkpoint"))
    model.has_savepoint = bool(model.evidence_of("savepoint"))
    model.watermark_strategy = bool(model.evidence_of("watermark"))
    for e in model.evidence_of("delivery_mode"):
        if e.detail and e.detail.isupper() and not model.checkpoint_mode:
            model.checkpoint_mode = e.detail
    model.has_flink = bool(model.jobs or model.evidence)
    return model
