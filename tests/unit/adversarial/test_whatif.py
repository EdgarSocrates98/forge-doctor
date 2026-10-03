"""Adversarial tests for what-if + migration (Phase 10).

Guarantees: never fabricate capability, never execute, never claim a
migration applies where evidence is absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.migration import plan_migrations
from forge_doctor_data.core.whatif import evaluate_change, parse_change


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


def test_no_glue_means_no_glue_plan(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"x.py": "print(1)\n"})
    ids = {p.path_id for p in plan_migrations(ctx)}
    assert "glue-4-to-5" not in ids


def test_glue_5_does_not_offer_upgrade(tmp_path: Path) -> None:
    tf = 'resource "aws_glue_job" "j" {\n  name = "j"\n  glue_version = "5.0"\n}\n'
    ctx = make_context(tmp_path, {"main.tf": tf})
    assert not any(p.path_id == "glue-4-to-5" for p in plan_migrations(ctx))


def test_change_to_unknown_version_unknown_not_supported(tmp_path: Path) -> None:
    tf = 'resource "aws_glue_job" "j" {\n  name = "j"\n  glue_version = "4.0"\n}\n'
    ctx = make_context(tmp_path, {"main.tf": tf})
    report = evaluate_change(ctx, parse_change("glue-version=99.0"))
    # no pack facts cover 99.0 → never claim support
    assert not report.supported_now
    assert report.unknown


def test_spoofed_iceberg_comment_not_a_table(tmp_path: Path) -> None:
    sql = "-- TODO: migrate events to USING iceberg format-version 2\nSELECT 1\n"
    ctx = make_context(tmp_path, {"x.sql": sql})
    report = evaluate_change(ctx, parse_change("iceberg-format-version=2"))
    # a comment alone must not claim an iceberg table is affected
    assert not any(e.startswith("table:iceberg:") for e in report.affected_entities)


def test_migrate_plan_never_emits_apply(tmp_path: Path) -> None:
    tf = 'resource "aws_glue_job" "j" {\n  name = "j"\n  glue_version = "4.0"\n}\n'
    ctx = make_context(tmp_path, {"main.tf": tf})
    for p in plan_migrations(ctx):
        blob = " ".join(
            list(p.required_changes) + list(p.validation_steps) + list(p.rollback)
        ).lower()
        for verb in ("apply", "execute", "run terraform", "deploy"):
            assert verb not in blob or "revert" in blob or "rerun" in blob


def test_whatif_changes_never_mutate(tmp_path: Path) -> None:
    tf = 'resource "aws_glue_job" "j" {\n  name = "j"\n  glue_version = "4.0"\n}\n'
    ctx = make_context(tmp_path, {"main.tf": tf})
    before = (tmp_path / "main.tf").read_text()
    evaluate_change(ctx, parse_change("glue-version=5.0"))
    plan_migrations(ctx)
    assert (tmp_path / "main.tf").read_text() == before


def test_malformed_change_rejected() -> None:
    for bad in ("", "=1", "nonsense", "glue-version="):
        with pytest.raises(ValueError):
            parse_change(bad)


def test_deterministic_whatif(tmp_path: Path) -> None:
    tf = 'resource "aws_glue_job" "j" {\n  name = "j"\n  glue_version = "4.0"\n}\n'
    ctx = make_context(tmp_path, {"main.tf": tf})
    r1 = evaluate_change(ctx, parse_change("glue-version=5.0"))
    r2 = evaluate_change(ctx, parse_change("glue-version=5.0"))
    assert r1.impacts == r2.impacts
    assert r1.affected_entities == r2.affected_entities
