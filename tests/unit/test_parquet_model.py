"""Unit tests for the ParquetProjectModel."""

from __future__ import annotations

from pathlib import Path

from forge_doctor.analyzers.parquet_model import parquet_model
from forge_doctor.core.context import ProjectContext


def make_context(tmp_path: Path, files: dict[str, str | bytes]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(source, bytes):
            path.write_bytes(source)
        else:
            path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


JOB = """\
df.write.parquet("s3://bucket/orders")
df2 = spark.read.parquet("s3://bucket/orders")
"""


def test_writer_reader_evidence(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": JOB})
    model = parquet_model(ctx)
    assert model.has_parquet
    assert len(model.writers) == 1
    assert len(model.readers) == 1


def test_config_evidence(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "job.py": 'spark.conf.set("spark.sql.parquet.compression.codec", "zstd")\n'
            'df.write.parquet("x")\n'
        },
    )
    model = parquet_model(ctx)
    assert "zstd" in model.compression_values


def test_option_compression(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"job.py": 'df.write.option("compression", "none").parquet("x")\n'},
    )
    model = parquet_model(ctx)
    assert "none" in model.compression_values


def test_write_pattern(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"job.py": 'df.repartition(1).write.parquet("x")\n'},
    )
    model = parquet_model(ctx)
    assert any(e.name == "repartition" for e in model.by_kind("write_pattern"))


def test_disk_files_stats(tmp_path: Path) -> None:
    files = {"a.parquet": b"x" * 10, "b.parquet": b"y" * 20, "c.parquet": b"z" * 30}
    ctx = make_context(tmp_path, files)
    model = parquet_model(ctx)
    assert model.file_count == 3
    assert model.total_bytes == 60
    assert model.median_bytes == 20


def test_conf_file_evidence(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"spark-defaults.conf": "spark.sql.parquet.compression.codec gzip\n"},
    )
    model = parquet_model(ctx)
    assert "gzip" in model.compression_values


def test_no_parquet(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"x.py": "x = 1\n"})
    assert not parquet_model(ctx).has_parquet


def test_deterministic(tmp_path: Path) -> None:
    files = {"a.py": JOB, "d.parquet": b"x" * 100}
    first = parquet_model(make_context(tmp_path / "p1", files))
    second = parquet_model(make_context(tmp_path / "p2", files))
    key = lambda e: (e.kind, e.name, e.value, e.line)  # noqa: E731
    assert sorted(map(key, first.evidence)) == sorted(map(key, second.evidence))
