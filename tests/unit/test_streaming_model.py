"""Unit tests for the StreamingProjectModel."""

from __future__ import annotations

from pathlib import Path

from forge_doctor.analyzers.streaming_model import streaming_model
from forge_doctor.core.context import ProjectContext


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


KAFKA_JOB = """
from pyspark.sql import SparkSession
spark = SparkSession.builder.getOrCreate()
df = (spark.readStream
      .format("kafka")
      .option("kafka.bootstrap.servers", "b:9092")
      .option("subscribe", "orders")
      .load())
df = df.withWatermark("event_time", "10 minutes")
df = df.groupBy("customer_id").count()
q = (df.writeStream
     .format("delta")
     .outputMode("update")
     .option("checkpointLocation", "s3://ckpt/orders")
     .trigger(processingTime="30 seconds")
     .queryName("orders_stream")
     .start())
"""

FOREACH_JOB = """
def handle(batch_df, batch_id):
    batch_df.write.saveAsTable("t")

stream = spark.readStream.format("rate").load()
q = (stream.writeStream
     .foreachBatch(handle)
     .start())
"""


def test_no_streaming(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": "print('hi')"})
    assert not streaming_model(ctx).has_streaming


def test_kafka_query(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": KAFKA_JOB})
    model = streaming_model(ctx)
    assert len(model.queries) == 1
    q = model.queries[0]
    assert q.engine == "spark_ss"
    assert q.source == "kafka"
    assert q.sink == "delta"
    assert q.output_mode == "update"
    assert q.trigger_kind.lower() == "processingtime"
    assert q.checkpoint == "s3://ckpt/orders"
    assert not q.checkpoint_dynamic
    assert q.watermark == "event_time:10 minutes"
    assert "groupby" in q.stateful_ops
    assert q.name == "orders_stream"


def test_foreach_batch(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": FOREACH_JOB})
    model = streaming_model(ctx)
    assert len(model.queries) == 1
    q = model.queries[0]
    assert q.sink == "foreachBatch"
    assert q.foreach_batch in {"handle", "present"}
    assert not q.checkpoint


def test_dynamic_checkpoint(tmp_path: Path) -> None:
    job = """
df = spark.readStream.format("rate").load()
q = df.writeStream.option("checkpointLocation", f"/tmp/{run_id}").start()
"""
    ctx = make_context(tmp_path, {"job.py": job})
    q = streaming_model(ctx).queries[0]
    assert q.checkpoint_dynamic or "/tmp" in q.checkpoint


def test_multiple_queries(tmp_path: Path) -> None:
    job = """
a = spark.readStream.format("kafka").load()
b = spark.readStream.format("rate").load()
qa = a.writeStream.format("delta").option("checkpointLocation", "s3://a").start()
qb = b.writeStream.format("console").option("checkpointLocation", "s3://b").start()
"""
    ctx = make_context(tmp_path, {"job.py": job})
    model = streaming_model(ctx)
    assert len(model.queries) == 2
    by_src = {q.source: q.sink for q in model.queries}
    assert by_src.get("kafka") == "delta"
    assert by_src.get("rate") == "console"
