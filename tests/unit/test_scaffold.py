from pathlib import Path

from forge_doctor.core.scaffold import _package_name, scaffold


def test_scaffold_creates_skeleton(tmp_path: Path):
    report = scaffold(tmp_path, name="my-project")
    assert not report.skipped
    expected = {
        "pyproject.toml",
        ".gitignore",
        "README.md",
        "src/my_project/__init__.py",
        "tests/test_placeholder.py",
    }
    created = {p.relative_to(tmp_path).as_posix() for p in report.created}
    assert expected == created
    pyproject = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "my-project"' in pyproject
    assert 'packages = ["my_project"]' in pyproject


def test_scaffold_never_overwrites_without_force(tmp_path: Path):
    existing = tmp_path / "README.md"
    existing.write_text("keep me", encoding="utf-8")
    report = scaffold(tmp_path, name="x")
    assert existing.read_text(encoding="utf-8") == "keep me"
    assert existing in report.skipped


def test_scaffold_force_overwrites(tmp_path: Path):
    existing = tmp_path / "README.md"
    existing.write_text("old", encoding="utf-8")
    report = scaffold(tmp_path, name="x", force=True)
    assert existing.read_text(encoding="utf-8") != "old"
    assert not report.skipped


def test_scaffold_defaults_name_to_directory(tmp_path: Path):
    target = tmp_path / "cool-app"
    target.mkdir()
    scaffold(target)
    assert 'name = "cool-app"' in (target / "pyproject.toml").read_text(encoding="utf-8")
    assert (target / "src" / "cool_app" / "__init__.py").exists()


def test_package_name_sanitization():
    assert _package_name("My Project!") == "my_project"
    assert _package_name("123-abc") == "app"
    assert _package_name("___") == "app"
