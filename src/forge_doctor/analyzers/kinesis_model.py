"""Kinesis deep-intelligence model.

Fuses Terraform ``aws_kinesis_*`` / ``aws_kinesisanalyticsv2_*`` /
``aws_kinesis_firehose_*`` resources, CloudFormation ``AWS::Kinesis::*``
/ ``AWS::KinesisFirehose::*`` / ``AWS::KinesisAnalyticsV2::*``, Spark
Structured Streaming ``kinesis`` options, and boto3 ``kinesis`` client
calls into one deterministic offline model.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forge_doctor.analyzers.hcl_lite import project_iac
from forge_doctor.analyzers.index import project_index
from forge_doctor.analyzers.streaming_model import streaming_model
from forge_doctor.analyzers.terraform_model import terraform_model
from forge_doctor.core.context import ProjectContext


@dataclass(frozen=True)
class KinesisStream:
    name: str
    file: Path
    line: int
    source: str = "terraform"
    shard_count: int = 0  # 0 → unknown/on-demand
    mode: str = ""  # PROVISIONED|ON_DEMAND
    retention_hours: int = 0
    encryption_type: str = ""
    efo_consumers: tuple[str, ...] = ()
    tf_label: str = ""  # resource label for event-source correlation


@dataclass(frozen=True)
class KinesisOption:
    key: str  # spark-ss kinesis option name
    value: str
    file: Path
    line: int


@dataclass(frozen=True)
class KinesisApiCall:
    api: str  # boto3 kinesis method
    file: Path
    line: int
    stream: str = ""  # StreamName literal when resolvable
    consumer: str = ""  # ConsumerARN/Name literal when resolvable


@dataclass(frozen=True)
class ManagedFlinkApp:
    name: str
    file: Path
    line: int
    source: str = "terraform"
    runtime_env: str = ""  # FLINK-1.xx
    parallelism: int = 0
    autoscaling: bool = False


@dataclass
class KinesisProjectModel:
    streams: list[KinesisStream] = field(default_factory=list)
    consumers: list[dict[str, Any]] = field(default_factory=list)  # EFO consumers w/ stream ref
    firehose_streams: list[dict[str, Any]] = field(default_factory=list)
    flink_apps: list[ManagedFlinkApp] = field(default_factory=list)
    options: list[KinesisOption] = field(default_factory=list)
    api_calls: list[KinesisApiCall] = field(default_factory=list)
    referenced_streams: set[str] = field(default_factory=set)
    has_kinesis: bool = False
    has_enhanced_fanout: bool = False

    def options_named(self, key: str) -> list[KinesisOption]:
        return [o for o in self.options if o.key.lower() == key.lower()]

    def stream_named(self, name: str) -> list[KinesisStream]:
        return [s for s in self.streams if s.name == name or s.tf_label == name]


_KINESIS_APIS = {
    "put_record",
    "put_records",
    "get_records",
    "get_shard_iterator",
    "describe_stream",
    "list_shards",
    "register_stream_consumer",
    "deregister_stream_consumer",
    "subscribe_to_shard",
    "update_shard_count",
    "increase_stream_retention_period",
    "decrease_stream_retention_period",
    "add_tags_to_stream",
    "split_shard",
    "merge_shards",
}
_KIN_OPTION_RE = re.compile(
    r"[\"'](streamName|stream_arn|endpointUrl|awsUseInstanceProfile|"
    r"startingPosition|describeShardInterval|maxFetchRate|maxFetchTimeInMs|"
    r"maxNumberOfMessagesPerFetch|maxNumberOfRecordsPerFetch|"
    r"consumerArn|consumerName|efo|kinesis\.[\w.]+)[\"']\s*[=:,]",
    re.IGNORECASE,
)


def _int(raw: Any) -> int:
    try:
        return int(str(raw or "0"))
    except ValueError:
        return 0


def _tf_stream(b: Any) -> KinesisStream:
    mode = ""
    m = re.search(r'stream_mode_details[^}]*?stream_mode\s*=\s*"?([A-Z_]+)', b.body, re.DOTALL)
    if m:
        mode = m.group(1)
    return KinesisStream(
        name=str(b.attrs.get("name") or (b.labels[-1] if b.labels else "")),
        file=Path(b.file),
        line=b.line,
        tf_label=b.labels[-1] if b.labels else "",
        shard_count=_int(b.attrs.get("shard_count")),
        mode=mode,
        retention_hours=_int(b.attrs.get("retention_period")),
        encryption_type=str(b.attrs.get("encryption_type") or ""),
    )


def kinesis_model(ctx: ProjectContext) -> KinesisProjectModel:
    """Fuse Kinesis evidence (TF/CFN/spark-ss/boto3) into one model."""
    model = KinesisProjectModel()

    for b in terraform_model(ctx).resources:
        rtype = b.labels[0] if b.labels else ""
        file = Path(b.file)
        if rtype == "aws_kinesis_stream":
            model.streams.append(_tf_stream(b))
        elif rtype == "aws_kinesis_stream_consumer":
            model.has_enhanced_fanout = True
            model.consumers.append(
                {
                    "name": str(b.attrs.get("name") or b.labels[-1]),
                    "stream_arn": str(b.attrs.get("stream_arn") or ""),
                    "file": file,
                    "line": b.line,
                    "source": "terraform",
                }
            )
        elif rtype == "aws_kinesis_firehose_delivery_stream":
            model.firehose_streams.append(
                {
                    "name": str(b.attrs.get("name") or b.labels[-1]),
                    "file": file,
                    "line": b.line,
                    "destinations": tuple(sorted(set(re.findall(r"(\w+)\s*\{", b.body)))),
                }
            )
        elif rtype in ("aws_kinesisanalyticsv2_application",):
            pm = re.search(r"parallelism\s*=\s*(\d+)", b.body)
            model.flink_apps.append(
                ManagedFlinkApp(
                    name=str(b.attrs.get("name") or b.labels[-1]),
                    file=file,
                    line=b.line,
                    runtime_env=str(b.attrs.get("runtime_environment") or ""),
                    parallelism=_int(pm.group(1)) if pm else 0,
                    autoscaling=bool(
                        re.search(r'auto_scaling_enabled\s*=\s*"?true', b.body, re.IGNORECASE)
                    ),
                )
            )
        elif rtype == "aws_kinesis_stream_policy":
            pass  # IAM policy presence is captured by the principal/governance layer

    for res in project_iac(ctx.files, ctx.root):
        if res.source != "cloudformation":
            continue
        a, file = res.attrs, Path(res.file)
        if res.type == "AWS::Kinesis::Stream":
            model.streams.append(
                KinesisStream(
                    name=str(a.get("Name") or res.name),
                    file=file,
                    line=res.line,
                    source="cloudformation",
                    shard_count=_int(a.get("ShardCount")),
                    retention_hours=_int(a.get("RetentionPeriodHours")),
                )
            )
        elif res.type == "AWS::Kinesis::StreamConsumer":
            model.has_enhanced_fanout = True
            model.consumers.append(
                {
                    "name": str(a.get("ConsumerName") or res.name),
                    "stream_arn": str(a.get("StreamARN") or ""),
                    "file": file,
                    "line": res.line,
                    "source": "cloudformation",
                }
            )
        elif res.type == "AWS::KinesisFirehose::DeliveryStream":
            model.firehose_streams.append(
                {
                    "name": str(a.get("DeliveryStreamName") or res.name),
                    "file": file,
                    "line": res.line,
                    "destinations": tuple(
                        sorted(k for k in a if k.endswith("DestinationConfiguration"))
                    ),
                }
            )
        elif res.type == "AWS::KinesisAnalyticsV2::Application":
            model.flink_apps.append(
                ManagedFlinkApp(
                    name=str(a.get("ApplicationName") or res.name),
                    file=file,
                    line=res.line,
                    source="cloudformation",
                    runtime_env=str(a.get("RuntimeEnvironment") or ""),
                )
            )

    sm = streaming_model(ctx)
    for q in sm.queries:
        if q.source == "kinesis" and q.source_identifier:
            model.referenced_streams.add(q.source_identifier)
        if q.sink == "kinesis" and q.sink_identifier:
            model.referenced_streams.add(q.sink_identifier)

    index = project_index(ctx)
    for module in index.modules.values():
        text = ctx.read_text(module.file) or ""
        if module.tree is not None:
            for node in ast.walk(module.tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not isinstance(func, ast.Attribute) or func.attr not in _KINESIS_APIS:
                    continue
                stream = consumer = ""
                for kw in node.keywords:
                    if not isinstance(kw.value, ast.Constant):
                        continue
                    if kw.arg == "StreamName":
                        stream = str(kw.value.value)
                    elif kw.arg in ("ConsumerARN", "ConsumerName"):
                        consumer = str(kw.value.value)
                model.api_calls.append(
                    KinesisApiCall(
                        api=func.attr,
                        file=module.file,
                        line=node.lineno,
                        stream=stream,
                        consumer=consumer,
                    )
                )
        for m in _KIN_OPTION_RE.finditer(text):
            model.options.append(
                KinesisOption(
                    key=m.group(1),
                    value="",
                    file=module.file,
                    line=text.count("\n", 0, m.start()) + 1,
                )
            )
        if re.search(r"[\"']consumerArn[\"']|register_stream_consumer|subscribe_to_shard", text):
            model.has_enhanced_fanout = True

    model.streams.sort(key=lambda s: (s.file.as_posix(), s.line, s.name))
    model.options.sort(key=lambda o: (o.file.as_posix(), o.line, o.key))
    model.api_calls.sort(key=lambda c: (c.file.as_posix(), c.line, c.api))
    model.flink_apps.sort(key=lambda f: (f.file.as_posix(), f.line, f.name))
    model.referenced_streams = set(sorted(model.referenced_streams))
    model.has_kinesis = bool(
        model.streams
        or model.consumers
        or model.options
        or model.api_calls
        or model.referenced_streams
    )
    return model
