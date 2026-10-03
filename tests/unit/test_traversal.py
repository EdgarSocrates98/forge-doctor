from pathlib import Path

from forge_doctor_data.core.traversal import iter_files


def _tree(root: Path, *paths: str) -> None:
    for p in paths:
        f = root / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")


def test_iter_files_finds_all(tmp_path: Path):
    _tree(tmp_path, "a.py", "pkg/b.py", "pkg/sub/c.py")
    found = {p.as_posix() for p in iter_files(tmp_path)}
    assert found == {"a.py", "pkg/b.py", "pkg/sub/c.py"}


def test_iter_files_prunes_excluded_dirs(tmp_path: Path):
    _tree(
        tmp_path,
        "keep.py",
        ".git/objects/x",
        ".venv/lib/y.py",
        "node_modules/z/index.js",
        "dist/out.whl",
        "__pycache__/a.pyc",
    )
    found = {p.as_posix() for p in iter_files(tmp_path)}
    assert found == {"keep.py"}


def test_iter_files_exclude_globs(tmp_path: Path):
    _tree(tmp_path, "tests/fixtures/a.py", "src/b.py")
    found = {p.as_posix() for p in iter_files(tmp_path, exclude_globs=("tests/fixtures/**",))}
    assert found == {"src/b.py"}
