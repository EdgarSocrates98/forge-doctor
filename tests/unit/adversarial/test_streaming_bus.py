"""Adversarial tests for Phase-9 streaming-bus intelligence.

FP guards, partial evidence, spoofed identifiers, determinism.
"""

from __future__ import annotations

import json
from pathlib import Path

from forge_doctor_data.analyzers.flink_model import flink_model
from forge_doctor_data.analyzers.kafka_model import kafka_model
from forge_doctor_data.analyzers.kinesis_model import kinesis_model
from forge_doctor_data.analyzers.streaming_runtime import diagnose_progress
from forge_doctor_data.checks.streaming_bus import (
    KafkaNoConsumerGroup,
    KafkaNoSchemaRegistry,
    KinesisFanOut,
    StreamingDelivery,
)
from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.delivery import derive_delivery


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


# --- false positives ---------------------------------------------------------


def test_kafka_word_in_comment_is_not_evidence(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"x.py": "# we do not use kafka here\nprint(1)\n"})
    assert not kafka_model(ctx).has_kafka


def test_kafka_word_in_string_value_only(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"x.py": 'DESC = "kafka-like system"\n'})
    # a string mentioning kafka w/o option shape must not create calls/topics
    m = kafka_model(ctx)
    assert not m.client_calls and not m.topics and not m.clusters


def test_jdbc_client_not_kafka_consumer(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"x.py": "c = NotKafkaConsumer('t')\n"})
    m = kafka_model(ctx)
    # name contains KafkaConsumer but it's not the real API token
    assert not any(c.api == "KafkaConsumer" for c in m.client_calls)


def test_empty_project_all_models(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {})
    assert not kafka_model(ctx).has_kafka
    assert not kinesis_model(ctx).has_kinesis
    assert not flink_model(ctx).has_flink


def test_non_streaming_project_passes_delivery(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"x.py": "import os\n"})
    results = StreamingDelivery().run(ctx)
    assert all(r.severity.value == "pass" for r in results)


def test_no_schema_registry_fp_when_registry_string_exists(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "cfg.py": 'URL = {"schema.registry.url": "https://reg"}\n',
            "job.py": 'c = KafkaConsumer("t", group_id="g")\n',
        },
    )
    results = KafkaNoSchemaRegistry().run(ctx)
    assert results[0].severity.value == "pass"


def test_consumer_group_fp_when_group_declared(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"x.py": 'c = KafkaConsumer("t", group_id="grp1")\n'})
    results = KafkaNoConsumerGroup().run(ctx)
    assert results[0].severity.value == "pass"


def test_kinesis_fanout_single_consumer_passes(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"r.py": 'client.get_records(StreamName="s")\n'},
    )
    results = KinesisFanOut().run(ctx)
    assert results[0].severity.value == "pass"


def test_flink_word_in_string_not_a_job(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"x.py": 'd = "flink tutorial"\n'})
    assert not flink_model(ctx).has_flink


# --- partial evidence --------------------------------------------------------


def test_delivery_missing_sink_unknown() -> None:
    s = derive_delivery(source="kafka", checkpoint=True)
    assert s.certainty == "unknown"
    assert s.level != "exactly-once-claim"


def test_delivery_checkpoint_alone_is_not_exactly_once() -> None:
    # a checkpoint alone must never yield exactly-once
    s = derive_delivery(source="kafka", checkpoint=True, sink="console")
    assert s.level == "at-least-once"
    s2 = derive_delivery(source="", checkpoint=True, sink="", engine="flink")
    assert s2.level != "exactly-once-claim"


def test_partial_progress_file_no_crash(tmp_path: Path) -> None:
    # progress with only the rate fields — no sources/state/eventTime
    p = tmp_path / "b.json"
    p.write_text(
        json.dumps(
            {"inputRowsPerSecond": 5, "processedRowsPerSecond": 5, "batchId": 0, "sink": {}}
        ),
        encoding="utf-8",
    )
    report = diagnose_progress([p])
    assert report.batches and not report.diagnoses


def test_progress_batch_negative_backlog_never_lags(tmp_path: Path) -> None:
    # latest < start (repartition/reset) must not produce a fake backlog
    doc = {
        "inputRowsPerSecond": 1,
        "processedRowsPerSecond": 1,
        "batchId": 0,
        "sink": {},
        "sources": [{"startOffset": {"t": {"0": 100}}, "latestOffset": {"t": {"0": 50}}}],
    }
    p = tmp_path / "b.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    report = diagnose_progress([p])
    assert all(d.code != "SKFK005" for d in report.diagnoses)


# --- spoofed identifiers ------------------------------------------------------


def test_topic_name_does_not_create_stream_entity(tmp_path: Path) -> None:
    # a topic called 'clicks' must not become stream:kinesis:clicks
    tf = 'resource "aws_msk_topic" "t" {\n  name = "clicks"\n  partitions = 3\n}\n'
    ctx = make_context(tmp_path, {"main.tf": tf})
    km, kx = kafka_model(ctx), kinesis_model(ctx)
    assert km.topics[0].name == "clicks"
    assert not kx.streams  # kafka topic never surfaces as kinesis


# --- determinism ----------------------------------------------------------------


def test_model_ordering_deterministic(tmp_path: Path) -> None:
    tf = """
resource "aws_msk_topic" "b" { name = "b" partitions = 3 }
resource "aws_msk_topic" "a" { name = "a" partitions = 3 }
resource "aws_msk_cluster" "z" {
  cluster_name = "z"
  encryption_info { encryption_in_transit { client_broker = "TLS" } }
}
"""
    ctx = make_context(tmp_path, {"main.tf": tf})
    m1 = kafka_model(ctx)
    ctx2 = make_context(tmp_path, {"main.tf": tf})
    m2 = kafka_model(ctx2)
    assert [(t.name, t.partitions) for t in m1.topics] == [
        (t.name, t.partitions) for t in m2.topics
    ]
    assert [(c.name, c.broker_nodes) for c in m1.clusters] == [
        (c.name, c.broker_nodes) for c in m2.clusters
    ]


def test_diagnose_order_independent(tmp_path: Path) -> None:
    paths = []
    for i in range(3):
        p = tmp_path / f"b{i}.json"
        p.write_text(
            json.dumps(
                {
                    "inputRowsPerSecond": 50.0,
                    "processedRowsPerSecond": 10.0,
                    "batchId": i,
                    "sink": {},
                }
            ),
            encoding="utf-8",
        )
        paths.append(p)
    r1 = diagnose_progress(paths)
    r2 = diagnose_progress(list(reversed(paths)))
    assert [d.code for d in r1.diagnoses] == [d.code for d in r2.diagnoses]


def test_flink_no_fp_on_checkpointed_job(tmp_path: Path) -> None:
    py = "env.enable_checkpointing(60000)\nds.key_by(f)\n"
    # no StreamExecutionEnvironment — a stray fragment should still flag
    # keyed-state evidence but never invent a managed app
    ctx = make_context(tmp_path, {"frag.py": py})
    m = flink_model(ctx)
    assert m.has_keyed_state and m.has_checkpoint
    assert not [j for j in m.jobs if j.source != "code"]
