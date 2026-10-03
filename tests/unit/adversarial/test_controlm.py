"""Adversarial coverage for the Control-M model (spec 170)."""

from __future__ import annotations

from pathlib import Path

from forge_doctor_data.analyzers.controlm_model import controlm_model
from forge_doctor_data.core.context import ProjectContext


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


def test_truncated_json_graceful(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"defs.json": '{"jobs": [{"name": "a"}, {"name"'})
    model = controlm_model(ctx)
    assert model.jobs == []


def test_garbage_file_graceful(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"defs.json": "not json {{{"})
    assert controlm_model(ctx).jobs == []


def test_job_missing_fields_graceful(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"defs.json": '{"jobs": [{}]}'})
    controlm_model(ctx)  # must not raise


def test_random_json_not_controlm(tmp_path: Path) -> None:
    """A JSON file without Control-M structure must not produce jobs."""
    ctx = make_context(tmp_path, {"defs.json": '{"a": {"b": 1}}'})
    assert controlm_model(ctx).jobs == []
