from pathlib import Path

from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.diagnose import diagnose_text, load_signatures, project_correlations


def test_signatures_load():
    sigs = load_signatures()
    assert sigs, "error knowledge packs should provide signatures"
    ids = [s.id for s in sigs]
    assert ids == sorted(ids)
    assert any(s.domain == "spark" for s in sigs)


def test_substring_and_regex_matching():
    text = (
        "ERROR: An error occurred while calling o42.showString.\n"
        "java.lang.OutOfMemoryError: Java heap space\n"
    )
    diagnoses = diagnose_text(text)
    assert diagnoses
    assert all(d.count >= 1 for d in diagnoses)
    assert all(d.signature.patterns for d in diagnoses)


def test_occurrence_count_per_line():
    text = "java.lang.OutOfMemoryError here\njava.lang.OutOfMemoryError again\n"
    diagnoses = diagnose_text(text)
    assert diagnoses
    assert max(d.count for d in diagnoses) == 2


def test_no_match_returns_empty():
    assert diagnose_text("all good, nothing known here") == []


def test_lakeformation_vending_denial_maps_to_pack_entry():
    text = (
        "ERROR software.amazon.awssdk.services.lakeformation.model.AccessDeniedException: "
        "GetTemporaryCredentialsForTableV2 is not authorized\n"
    )
    diagnoses = diagnose_text(text)
    vending = [d for d in diagnoses if d.signature.id == "LAKE-E004"]
    assert vending, "credential-vending denial should map to LAKE-E004"
    assert "credential-vending" in vending[0].signature.families
    assert vending[0].signature.fixes, "pack entries must carry fix hints"


def _ctx(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


_VENDING_LOG = "AccessDeniedException: GetTemporaryCredentialsForTableV2 denied\n"
_GLUE5_TF = 'resource "aws_glue_job" "j" {\n  glue_version = "5.0"\n}\n'
_LF_TF = 'resource "aws_lakeformation_permissions" "p" {}\n'
_WRITE_PY = 'df.write.saveAsTable("t")\n'


def test_correlation_all_three_legs(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {"main.tf": _GLUE5_TF + _LF_TF, "job.py": _WRITE_PY})
    diagnoses = diagnose_text(_VENDING_LOG)
    lines = project_correlations(ctx, diagnoses)
    assert len(lines) == 1
    assert "credential-vending/write-path" in lines[0]


def test_correlation_skips_without_any_leg(tmp_path: Path) -> None:
    diagnoses = diagnose_text(_VENDING_LOG)
    for i, files in enumerate(
        (
            {"main.tf": _LF_TF, "job.py": _WRITE_PY},  # no glue >=5
            {"main.tf": _GLUE5_TF, "job.py": _WRITE_PY},  # no lf/fgac config
            {"main.tf": _GLUE5_TF + _LF_TF, "job.py": "print(1)\n"},  # no write op
        )
    ):
        ctx = _ctx(tmp_path / f"case{i}", files)
        assert project_correlations(ctx, diagnoses) == []


def test_correlation_needs_vending_diagnosis(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {"main.tf": _GLUE5_TF + _LF_TF, "job.py": _WRITE_PY})
    assert project_correlations(ctx, diagnose_text("java.lang.OutOfMemoryError\n")) == []
