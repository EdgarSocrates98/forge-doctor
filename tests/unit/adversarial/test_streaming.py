"""Adversarial coverage for the streaming model (spec 170)."""

from __future__ import annotations

from pathlib import Path

from forge_doctor_data.analyzers.streaming_model import streaming_model
from forge_doctor_data.core.context import ProjectContext


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


def test_aliased_session_readstream_detected(tmp_path: Path) -> None:
    """`ss = SparkSession...; ss.readStream` resolves the stream chain."""
    ctx = make_context(
        tmp_path,
        {
            "job.py": (
                "from pyspark.sql import SparkSession\n"
                "ss = SparkSession.builder.getOrCreate()\n"
                'df = ss.readStream.format("kafka").load()\n'
                'q = df.writeStream.format("delta").start()\n'
            )
        },
    )
    model = streaming_model(ctx)
    assert len(model.queries) == 1
    assert model.queries[0].source == "kafka"
    assert model.queries[0].sink == "delta"


def test_non_spark_readstream_attr_no_fp(tmp_path: Path) -> None:
    """A custom object's ``readStream`` attribute is not a stream source."""
    ctx = make_context(
        tmp_path,
        {"job.py": ("class F:\n    def readStream(self): ...\nf = F()\nx = f.readStream\n")},
    )
    assert streaming_model(ctx).queries == []


def test_write_only_file_yields_sink_record(tmp_path: Path) -> None:
    """A writeStream with no observable readStream still records a query."""
    ctx = make_context(tmp_path, {"job.py": 'q = df.writeStream.format("delta").start()\n'})
    (q,) = streaming_model(ctx).queries
    assert q.sink == "delta"
    assert q.source == ""


def test_cross_file_halves_documented(tmp_path: Path) -> None:
    """Documented limitation: var binding is per-file, so a read in a.py
    and a write in b.py surface as two half-empty records, never merged
    and never an exception."""
    ctx = make_context(
        tmp_path,
        {
            "a.py": 'df = spark.readStream.format("kafka").load()\n',
            "b.py": 'q = df.writeStream.format("delta").start()\n',
        },
    )
    model = streaming_model(ctx)
    assert len(model.queries) == 2
    by_file = {q.file.name: q for q in model.queries}
    assert by_file["a.py"].source == "kafka"
    assert by_file["b.py"].sink == "delta"


def test_checkpoint_option_without_literal_is_dynamic(tmp_path: Path) -> None:
    """The index keeps only literal args, so ``option("checkpointLocation")``
    with a dropped or absent value reports ``checkpoint=""`` and
    ``checkpoint_dynamic=True`` - "no resolvable literal" and "non-literal
    expression" are indistinguishable at the CallSite level."""
    ctx = make_context(
        tmp_path,
        {
            "job.py": (
                'df = spark.readStream.format("kafka").load()\n'
                'q = df.writeStream.option("checkpointLocation").start()\n'
            )
        },
    )
    (q,) = streaming_model(ctx).queries
    assert q.checkpoint == ""
    assert q.checkpoint_dynamic is True


def test_lambda_foreachbatch_is_present(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "job.py": (
                'df = spark.readStream.format("rate").load()\n'
                "q = df.writeStream.foreachBatch(lambda b, i: None).start()\n"
            )
        },
    )
    (q,) = streaming_model(ctx).queries
    assert q.foreach_batch == "present"


def test_query_name_kwarg_wins(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "job.py": (
                'df = spark.readStream.format("rate").load()\n'
                'q = df.writeStream.queryName("nightly").start()\n'
            )
        },
    )
    (q,) = streaming_model(ctx).queries
    assert q.name == "nightly"
