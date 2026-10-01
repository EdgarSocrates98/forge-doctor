from pathlib import Path

from forge_doctor.core.context import ProjectContext
from forge_doctor.core.project_info import collect_info


def test_collect_info_counts_files_and_lines(tmp_path: Path):
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("z = 3\n", encoding="utf-8")
    (tmp_path / "data.txt").write_text("hello\n", encoding="utf-8")

    info = collect_info(ProjectContext(root=tmp_path))
    assert info.file_count == 3
    assert info.line_count == 4
    extensions = dict(info.by_extension)
    assert extensions[".py"] == 2
    assert extensions[".txt"] == 1


def test_collect_info_reads_project_metadata(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "1.2.3"\nrequires-python = ">=3.11"\n',
        encoding="utf-8",
    )
    (tmp_path / "poetry.lock").write_text("", encoding="utf-8")

    info = collect_info(ProjectContext(root=tmp_path))
    assert info.name == "demo"
    assert info.project_version == "1.2.3"
    assert info.requires_python == ">=3.11"
    assert "Poetry" in info.tools


def test_collect_info_empty_project(tmp_path: Path):
    info = collect_info(ProjectContext(root=tmp_path))
    assert info.file_count == 0
    assert info.line_count == 0
    assert info.name == tmp_path.name
    assert info.tools == []


def test_collect_info_git_fields(tmp_path: Path):
    info = collect_info(ProjectContext(root=tmp_path))
    # pytest basetemp lives inside the repo; only the contract shape is
    # asserted - ambient git state varies outside this workspace.
    assert isinstance(info.git_repo, bool)
    if info.git_repo:
        assert info.tracked_files is not None
    else:
        assert info.tracked_files is None
