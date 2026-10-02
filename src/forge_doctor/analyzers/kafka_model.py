"""Kafka / MSK deep-intelligence model.

Fuses Terraform ``aws_msk_*`` (+ generic ``kafka_*``) resources,
CloudFormation ``AWS::MSK::*``, Spark Structured Streaming ``kafka``
options, and Python client evidence (``KafkaConsumer``/``KafkaProducer``,
``confluent_kafka``, schema registry) into one deterministic model.
Offline only.
"""

from __future__ import annotations

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
class KafkaCluster:
    name: str
    file: Path
    line: int
    source: str = "terraform"
    serverless: bool = False
    broker_nodes: int = 0
    broker_instance: str = ""
    kafka_version: str = ""
    encryption_in_transit: str = ""  # TLS|PLAINTEXT|TLS_PLAINTEXT
    client_auth: str = ""  # sasl_iam|sasl_scram|tls|unauthenticated|""
    public_access: bool = False
    logging: bool = False


@dataclass(frozen=True)
class KafkaTopic:
    name: str
    file: Path
    line: int
    source: str = "terraform"
    partitions: int = 0
    replication: int = 0
    config_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class KafkaOption:
    """One kafka streaming option with provenance."""

    key: str  # e.g. subscribe|assign|startingOffsets|maxOffsetsPerTrigger
    value: str
    file: Path
    line: int
    query: str = ""  # owning streaming query name


@dataclass(frozen=True)
class KafkaClientCall:
    """A python kafka/confluent client call (consumer/producer/admin)."""

    api: str  # KafkaConsumer|KafkaProducer|AdminClient|SchemaRegistryClient
    file: Path
    line: int
    arg0: str = ""  # first string literal arg (topic/config)


@dataclass
class KafkaProjectModel:
    """Everything the project evidences about Kafka, in sorted order."""

    clusters: list[KafkaCluster] = field(default_factory=list)
    topics: list[KafkaTopic] = field(default_factory=list)
    options: list[KafkaOption] = field(default_factory=list)
    client_calls: list[KafkaClientCall] = field(default_factory=list)
    consumer_groups: set[str] = field(default_factory=set)
    subscribed_topics: set[str] = field(default_factory=set)
    schema_registry: bool = False
    secure_transport: bool = False  # TLS/SASL evidence anywhere
    has_kafka: bool = False

    def options_named(self, key: str) -> list[KafkaOption]:
        return [o for o in self.options if o.key.lower() == key.lower()]

    @property
    def has_fail_on_data_loss(self) -> bool:
        return any(o.key == "failOnDataLoss" for o in self.options)


_MSK_TYPES = {
    "aws_msk_cluster": False,
    "aws_msk_serverless_cluster": True,
}
_MSK_CFG_RE = {
    "encryption": re.compile(
        r"encryption_in_transit[^}]*?client_broker[\"']?\s*=\s*\"?([A-Z_]+)", re.DOTALL
    ),
    "auth": re.compile(
        r"client_authentication[^}]*?(sasl|tls|unauthenticated)\s*[=]",
        re.DOTALL | re.IGNORECASE,
    ),
}

_KAFKA_PY_APIS = {
    "KafkaConsumer",
    "KafkaProducer",
    "AdminClient",
    "SchemaRegistryClient",
    "SerializingProducer",
    "DeserializingConsumer",
}


def _int(raw: Any) -> int:
    try:
        return int(str(raw or "0"))
    except ValueError:
        return 0


def _tf_cluster(b: Any, serverless: bool) -> KafkaCluster:
    attrs, body = b.attrs, b.body
    enc = _MSK_CFG_RE["encryption"].search(body)
    auth = _MSK_CFG_RE["auth"].search(body)
    name = str(attrs.get("cluster_name") or attrs.get("name") or "")
    if not name and b.labels:
        name = b.labels[-1]
    return KafkaCluster(
        name=name,
        file=Path(b.file),
        line=b.line,
        serverless=serverless,
        broker_nodes=_int(attrs.get("number_of_broker_nodes")),
        broker_instance=str(attrs.get("instance_type") or ""),
        kafka_version=str(attrs.get("kafka_version") or ""),
        encryption_in_transit=enc.group(1).upper() if enc else "",
        client_auth=auth.group(1).lower() if auth else "",
        public_access=bool(
            re.search(r'public_access[^}]*?type\s*=\s*"?SERVICE_PROVIDED_EIPS', body)
        ),
        logging=bool(re.search(r"logging_info\s*\{", body)),
    )


def kafka_model(ctx: ProjectContext) -> KafkaProjectModel:
    """Fuse Kafka evidence (TF/CFN/spark-ss/python) into one model."""
    model = KafkaProjectModel()

    for b in terraform_model(ctx).resources:
        rtype = b.labels[0] if b.labels else ""
        file = Path(b.file)
        if rtype in _MSK_TYPES:
            model.clusters.append(_tf_cluster(b, _MSK_TYPES[rtype]))
        elif rtype in ("aws_msk_topic", "kafka_topic"):
            cfg_keys: list[str] = []
            for cfg_body in re.findall(r"config\s*=\s*\{([^}]*)\}", b.body):
                cfg_keys.extend(re.findall(r'["\']?([a-z.]+)["\']?\s*=', cfg_body))
            model.topics.append(
                KafkaTopic(
                    name=str(b.attrs.get("name") or (b.labels[-1] if b.labels else "")),
                    file=file,
                    line=b.line,
                    partitions=_int(b.attrs.get("partitions")),
                    replication=_int(b.attrs.get("replication_factor")),
                    config_keys=tuple(sorted(set(cfg_keys))),
                )
            )
        elif rtype == "aws_msk_scram_secret_association" or rtype == "aws_msk_replicator":
            model.secure_transport = True

    for res in project_iac(ctx.files, ctx.root):
        if res.source != "cloudformation":
            continue
        a, file = res.attrs, Path(res.file)
        if res.type == "AWS::MSK::Cluster":
            enc_cfg = a.get("EncryptionInfo") or {}
            transit = (
                (enc_cfg.get("EncryptionInTransit") or {}).get("ClientBroker")
                if isinstance(enc_cfg, dict)
                else ""
            )
            model.clusters.append(
                KafkaCluster(
                    name=str(a.get("ClusterName") or res.name),
                    file=file,
                    line=res.line,
                    source="cloudformation",
                    broker_nodes=_int(a.get("NumberOfBrokerNodes")),
                    broker_instance=str(a.get("BrokerNodeGroupInfo", {}).get("InstanceType") or "")
                    if isinstance(a.get("BrokerNodeGroupInfo"), dict)
                    else "",
                    kafka_version=str(a.get("KafkaVersion") or ""),
                    encryption_in_transit=str(transit or "").upper(),
                    logging=bool(a.get("LoggingInfo")),
                )
            )
        elif res.type == "AWS::MSK::ServerlessCluster":
            model.clusters.append(
                KafkaCluster(
                    name=str(a.get("ClusterName") or res.name),
                    file=file,
                    line=res.line,
                    source="cloudformation",
                    serverless=True,
                )
            )
        elif res.type == "AWS::MSK::Topic":
            model.topics.append(
                KafkaTopic(
                    name=str(a.get("TopicName") or res.name),
                    file=file,
                    line=res.line,
                    source="cloudformation",
                    partitions=_int(a.get("PartitionCount")),
                    replication=_int(a.get("ReplicationFactor")),
                )
            )

    # Spark SS kafka endpoints: subscribe/assign/topics + kafka.* options.
    sm = streaming_model(ctx)
    for q in sm.queries:
        for which, ident in (
            ("source", q.source_identifier if q.source == "kafka" else ""),
            ("sink", q.sink_identifier if q.sink == "kafka" else ""),
        ):
            if ident:
                model.subscribed_topics.add(ident)
                model.options.append(
                    KafkaOption(
                        key="subscribe" if which == "source" else "topic",
                        value=ident,
                        file=q.file,
                        line=q.line,
                        query=q.name,
                    )
                )

    # Python: kafka clients, options, schema registry, security flags.
    index = project_index(ctx)
    for module in index.modules.values():
        text = ctx.read_text(module.file) or ""
        if module.tree is not None:
            import ast

            for node in ast.walk(module.tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    dotted = ""
                    while isinstance(func, ast.Attribute):
                        dotted = "." + func.attr + dotted
                        func = func.value
                    if isinstance(func, ast.Name):
                        dotted = func.id + dotted
                    short = dotted.rsplit(".", 1)[-1]
                    is_kafka = short in _KAFKA_PY_APIS
                    if is_kafka:
                        arg0 = next(
                            (
                                a.value
                                for a in node.args
                                if isinstance(a, ast.Constant) and isinstance(a.value, str)
                            ),
                            "",
                        )
                        model.client_calls.append(
                            KafkaClientCall(
                                api=short, file=module.file, line=node.lineno, arg0=arg0
                            )
                        )
        # option mining: kafka.* / group.id / schema registry strings
        for m in re.finditer(
            r"[\"']([\w.]*kafka[\w.]*|group\.id|schema\.registry\.url|"
            r"subscribe|assign|startingOffsets|endingOffsets|maxOffsetsPerTrigger|"
            r"minOffsetsPerTrigger|failOnDataLoss|maxTriggerDelay|"
            r"key\.deserializer|value\.deserializer|key\.serializer|value\.serializer|"
            r"security\.protocol|sasl\.mechanism|ssl\.[\w.]+)[\"']\s*[=:,]",
            text,
            re.IGNORECASE,
        ):
            key = m.group(1)
            model.options.append(
                KafkaOption(
                    key=key,
                    value="",
                    file=module.file,
                    line=text.count("\n", 0, m.start()) + 1,
                )
            )
            low = key.lower()
            if "schema.registry" in low or "schemaregistry" in low:
                model.schema_registry = True
            if "security.protocol" in low or "sasl" in low or "ssl." in low:
                model.secure_transport = True
        for m in re.finditer(
            r"(?:[\"'](?:kafka\.)?group\.id[\"']|\bgroup_id)\s*[=:,]\s*[\"']([^\"']+)[\"']",
            text,
            re.IGNORECASE,
        ):
            model.consumer_groups.add(m.group(1))
        if "schema.registry.url" in text.lower() or "SchemaRegistryClient" in text:
            model.schema_registry = True
        if re.search(r"security\.protocol\s*['\"]?\s*[=:,]", text, re.IGNORECASE) or re.search(
            r"sasl_mechanism|sasl\.mechanism", text, re.IGNORECASE
        ):
            model.secure_transport = True

    model.clusters.sort(key=lambda c: (c.file.as_posix(), c.line, c.name))
    model.topics.sort(key=lambda t: (t.file.as_posix(), t.line, t.name))
    model.options.sort(key=lambda o: (o.file.as_posix(), o.line, o.key))
    model.client_calls.sort(key=lambda c: (c.file.as_posix(), c.line, c.api))
    model.consumer_groups = set(sorted(model.consumer_groups))
    model.subscribed_topics = set(sorted(model.subscribed_topics))
    model.has_kafka = bool(
        model.clusters
        or model.topics
        or model.options
        or model.client_calls
        or model.subscribed_topics
    )
    return model
