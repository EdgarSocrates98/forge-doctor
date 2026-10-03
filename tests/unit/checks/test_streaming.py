"""Unit tests for the streaming checks (STREAM###)."""

from __future__ import annotations

from pathlib import Path

from forge_doctor_data.checks.streaming import (
    CHECKS,
    DynamicCheckpoint,
    ForeachBatchDetected,
    MissingCheckpoint,
    SharedCheckpoint,
    StatefulNoWatermark,
    TempCheckpoint,
)
from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.models import Severity


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


NO_CKPT = """
df = spark.readStream.format("kafka").option("subscribe", "t").load()
q = df.writeStream.format("delta").start()
"""

GOOD = """
df = spark.readStream.format("rate").load()
df = df.withWatermark("ts", "5 minutes")
q = (df.writeStream.format("delta")
     .option("checkpointLocation", "s3://ckpt/q1")
     .start())
"""


def test_stream001_anchor(tmp_path: Path) -> None:
    empty = make_context(tmp_path / "e", {"x.txt": "hi"})
    res = CHECKS[0].run(empty)
    assert res[0].severity == Severity.PASS
    ctx = make_context(tmp_path / "p", {"job.py": GOOD})
    res = CHECKS[0].run(ctx)
    assert res[0].severity == Severity.INFO and "1 streaming queries" in res[0].message


def test_stream002_missing_checkpoint(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": NO_CKPT})
    res = MissingCheckpoint().run(ctx)
    assert len(res) == 1 and res[0].severity == Severity.WARNING


def test_stream002_ok(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": GOOD})
    assert MissingCheckpoint().run(ctx) == []


def test_stream003_temp_checkpoint(tmp_path: Path) -> None:
    job = NO_CKPT.replace(".start()", '.option("checkpointLocation", "/tmp/ck").start()')
    ctx = make_context(tmp_path, {"job.py": job})
    res = TempCheckpoint().run(ctx)
    assert len(res) == 1


def test_stream013_shared_checkpoint(tmp_path: Path) -> None:
    job = """
a = spark.readStream.format("rate").load()
b = spark.readStream.format("kafka").load()
qa = a.writeStream.option("checkpointLocation", "s3://same").start()
qb = b.writeStream.option("checkpointLocation", "s3://same").start()
"""
    ctx = make_context(tmp_path, {"job.py": job})
    res = SharedCheckpoint().run(ctx)
    assert len(res) == 1 and "s3://same" in res[0].message


def test_stream014_dynamic_checkpoint(tmp_path: Path) -> None:
    job = """
df = spark.readStream.format("rate").load()
q = df.writeStream.option("checkpointLocation", f"/ck/{now}").start()
"""
    ctx = make_context(tmp_path, {"job.py": job})
    res = DynamicCheckpoint().run(ctx)
    assert len(res) == 1


def test_stream020_stateful_no_watermark(tmp_path: Path) -> None:
    job = NO_CKPT.replace(".load()", '.load().groupBy("k").count()')
    ctx = make_context(tmp_path, {"job.py": job})
    res = StatefulNoWatermark().run(ctx)
    assert len(res) == 1 and res[0].severity == Severity.INFO


def test_stream020_ok_with_watermark(tmp_path: Path) -> None:
    job = GOOD.replace("df.writeStream", 'df.groupBy("k").count().writeStream')
    ctx = make_context(tmp_path, {"job.py": job})
    assert StatefulNoWatermark().run(ctx) == []


def test_stream070_foreach_batch(tmp_path: Path) -> None:
    job = """
df = spark.readStream.format("rate").load()
q = df.writeStream.foreachBatch(lambda b, i: None).start()
"""
    ctx = make_context(tmp_path, {"job.py": job})
    res = ForeachBatchDetected().run(ctx)
    assert len(res) == 1


def test_all_checks_run(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": GOOD})
    for check in CHECKS:
        check.run(ctx)


def test_cli_inspect(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from forge_doctor_data.cli import app

    make_context(tmp_path, {"job.py": NO_CKPT})
    result = CliRunner().invoke(app, ["streaming", "inspect", str(tmp_path)])
    assert result.exit_code == 0
    assert "Streaming" in result.output
    assert "STREAM002" in result.output


def test_cli_inspect_empty(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from forge_doctor_data.cli import app

    result = CliRunner().invoke(app, ["streaming", "inspect", str(tmp_path)])
    assert result.exit_code == 0
    assert "no streaming" in result.output
