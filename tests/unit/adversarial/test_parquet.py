"""Adversarial coverage for the Parquet model (spec 170)."""

from __future__ import annotations

from pathlib import Path

from forge_doctor.analyzers.parquet_model import parquet_model
from forge_doctor.core.context import ProjectContext


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


def test_string_literal_extension_no_fp(tmp_path: Path) -> None:
    """A ``.parquet`` path in a bare string is not a write call."""
    ctx = make_context(tmp_path, {"j.py": 'p = "data/events.parquet"\nprint(p)\n'})
    model = parquet_model(ctx)
    assert model.evidence == []


def test_format_parquet_save_detected(tmp_path: Path) -> None:
    """``df.write.format("parquet").save()`` is the same write as
    ``.parquet()`` - the model catches both."""
    ctx = make_context(tmp_path, {"j.py": 'df.write.format("parquet").save("/out")\n'})
    assert parquet_model(ctx).evidence


def test_empty_data_dir_stats_graceful(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    ctx = make_context(tmp_path, {"j.py": 'df.write.parquet("data/")\n'})
    model = parquet_model(ctx)
    assert model.file_count == 0
    assert model.median_bytes == 0


def test_zero_byte_files_counted(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "a.parquet").write_bytes(b"")
    (data / "b.parquet").write_bytes(b"")
    ctx = make_context(tmp_path, {"j.py": 'df.write.parquet("data/")\n'})
    model = parquet_model(ctx)
    assert model.file_count == 2
    assert model.median_bytes == 0
