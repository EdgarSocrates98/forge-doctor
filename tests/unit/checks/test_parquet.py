"""Unit tests for the PARQ### checks (ParquetProjectModel consumers)."""

from __future__ import annotations

from pathlib import Path

from forge_doctor_data.checks.parquet import (
    CHECKS,
    ExcessiveFileCount,
    MixedCodecs,
    ParquetUsage,
    SinglePartitionWrite,
    SizeSkew,
    SmallFileDataset,
    UncompressedWrite,
)
from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.models import Severity


def make_context(tmp_path: Path, files: dict[str, str | bytes]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(source, bytes):
            path.write_bytes(source)
        else:
            path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


JOB = 'df.write.parquet("s3://b/orders")\n'


def test_usage_anchor(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": JOB})
    results = ParquetUsage().run(ctx)
    assert results[0].severity == Severity.INFO
    assert "writers" in results[0].message


def test_usage_anchor_empty(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"x.py": "x = 1\n"})
    assert ParquetUsage().run(ctx)[0].severity == Severity.PASS


def test_single_partition_write(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": 'df.repartition(1).write.parquet("x")\n'})
    results = SinglePartitionWrite().run(ctx)
    assert len(results) == 1
    assert results[0].severity == Severity.INFO


def test_uncompressed_write(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"job.py": 'df.write.option("compression", "none").parquet("x")\n'},
    )
    results = UncompressedWrite().run(ctx)
    assert len(results) == 1
    assert results[0].severity == Severity.WARNING


def test_uncompressed_ok(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": JOB})
    assert UncompressedWrite().run(ctx) == []


def test_mixed_codecs(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "a.py": 'spark.conf.set("spark.sql.parquet.compression.codec", "zstd")\n'
            'df.write.parquet("x")\n',
            "b.properties": "spark.sql.parquet.compression.codec=gzip\n",
        },
    )
    results = MixedCodecs().run(ctx)
    assert len(results) == 1
    assert "gzip" in results[0].message and "zstd" in results[0].message


def test_mixed_codecs_consistent_ok(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": JOB})
    assert MixedCodecs().run(ctx) == []


def test_small_file_dataset(tmp_path: Path) -> None:
    files = {f"f{i}.parquet": b"x" * 1_000_000 for i in range(5)}
    files["job.py"] = JOB
    ctx = make_context(tmp_path, files)
    results = SmallFileDataset().run(ctx)
    assert len(results) == 1
    assert "1MB" in results[0].message


def test_small_file_dataset_ok(tmp_path: Path) -> None:
    files = {f"f{i}.parquet": b"x" * 20_000_000 for i in range(3)}
    ctx = make_context(tmp_path, files)
    assert SmallFileDataset().run(ctx) == []


def test_excessive_file_count(tmp_path: Path, monkeypatch) -> None:
    # 10k real files is too slow for a unit test - override the built model.
    import forge_doctor_data.checks.parquet as check_mod

    ctx = make_context(tmp_path, {"one.parquet": b"x"})
    model = check_mod._model(ctx)
    model.file_count = 10_001
    monkeypatch.setattr(check_mod, "_model", lambda _ctx: model)
    results = ExcessiveFileCount().run(ctx)
    assert len(results) == 1


def test_size_skew(tmp_path: Path) -> None:
    files = {f"f{i}.parquet": b"x" * 1_000_000 for i in range(19)}
    files["big1.parquet"] = b"y" * 200_000_000
    files["big2.parquet"] = b"y" * 200_000_000
    ctx = make_context(tmp_path, files)
    results = SizeSkew().run(ctx)
    assert len(results) == 1
    assert "skew" in results[0].message


def test_size_skew_even_ok(tmp_path: Path) -> None:
    files = {f"f{i}.parquet": b"x" * 20_000_000 for i in range(5)}
    ctx = make_context(tmp_path, files)
    assert SizeSkew().run(ctx) == []


def test_all_checks_run(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": JOB, "d.parquet": b"x" * 50})
    for check in CHECKS:
        check.run(ctx)


def test_cli_inspect(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from forge_doctor_data.cli import app

    make_context(tmp_path, {"job.py": JOB, "d.parquet": b"x" * 100})
    result = CliRunner().invoke(app, ["parquet", "inspect", str(tmp_path)])
    assert result.exit_code == 0
    assert "Parquet" in result.output


def test_cli_inspect_empty(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from forge_doctor_data.cli import app

    result = CliRunner().invoke(app, ["parquet", "inspect", str(tmp_path)])
    assert result.exit_code == 0
    assert "no Parquet" in result.output
