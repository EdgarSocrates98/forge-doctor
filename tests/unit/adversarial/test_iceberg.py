"""Adversarial coverage for the Iceberg model (spec 170)."""

from __future__ import annotations

from pathlib import Path

from forge_doctor_data.analyzers.iceberg_model import iceberg_model
from forge_doctor_data.core.context import ProjectContext


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


def test_using_iceberg_in_comment_no_fp(tmp_path: Path) -> None:
    """sqlglot normalizes comments out of the statement the model scans -
    `USING iceberg` inside a block comment is not format evidence."""
    ctx = make_context(tmp_path, {"q.sql": "SELECT a /* USING iceberg */ FROM t;\n"})
    assert not iceberg_model(ctx).has_iceberg


def test_using_iceberg_detected(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"q.sql": "CREATE TABLE d.t (a int) USING iceberg;\n"})
    assert iceberg_model(ctx).has_iceberg


def test_format_iceberg_call_detected(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"j.py": 'df.write.format("iceberg").save("d.t")\n'})
    assert iceberg_model(ctx).has_iceberg


def test_iceberg_word_in_string_no_fp(tmp_path: Path) -> None:
    """The bare word ``iceberg`` in a non-call string is not evidence."""
    ctx = make_context(tmp_path, {"j.py": 'README = "we do not use iceberg here"\n'})
    assert not iceberg_model(ctx).has_iceberg


def test_merge_without_partitioning_flagged(tmp_path: Path) -> None:
    """FN coverage: a bare MERGE with no PARTITIONED BY anywhere produces
    the ICE002 derived finding (op evidence x absent property)."""
    from forge_doctor_data.checks.iceberg import MergeNoPartition

    ctx = make_context(
        tmp_path,
        {
            "q.sql": (
                "MERGE INTO cat.t t USING src.s s ON t.id = s.id "
                "WHEN MATCHED THEN UPDATE SET a = s.a;\n"
            ),
        },
    )
    results = MergeNoPartition().run(ctx)
    assert results
