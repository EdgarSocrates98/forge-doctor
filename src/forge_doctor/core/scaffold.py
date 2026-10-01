"""``forge-doctor init`` scaffolding - creates a sane Python project skeleton."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_PYPROJECT_TEMPLATE = """\
[project]
name = "{name}"
version = "0.1.0"
description = ""
requires-python = ">=3.11"
dependencies = []

[dependency-groups]
dev = [
    "pytest>=8",
    "ruff>=0.9",
    "mypy>=1.13",
]

[tool.ruff]
target-version = "py311"
src = ["src", "tests"]
line-length = 100

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.mypy]
python_version = "3.11"
mypy_path = "src"
packages = ["{package}"]
strict = true

[build-system]
requires = ["poetry-core>=2.0"]
build-backend = "poetry.core.masonry.api"
"""

_GITIGNORE_TEMPLATE = """\
__pycache__/
*.py[cod]
.venv/
venv/
dist/
build/
*.egg-info/
.pytest_cache/
.forge-doctor/
.mypy_cache/
.ruff_cache/
.env
.env.*
!.env.example
"""

_README_TEMPLATE = """\
# {name}

TODO: describe the project.

## Development

```bash
poetry install
poetry run pytest
```
"""

_INIT_PY_TEMPLATE = '''"""{name}."""

__version__ = "0.1.0"
'''

_TEST_TEMPLATE = """\
def test_placeholder() -> None:
    assert True
"""


@dataclass
class ScaffoldReport:
    created: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)


def _package_name(name: str) -> str:
    """``my-project`` -> ``my_project``; falls back to ``app``."""
    slug = re.sub(r"[^0-9a-zA-Z_]", "_", name).lower()
    slug = re.sub(r"_+", "_", slug).strip("_")
    if not slug or slug[0].isdigit():
        return "app"
    return slug


def _write(path: Path, content: str, force: bool, report: ScaffoldReport) -> None:
    if path.exists() and not force:
        report.skipped.append(path)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    report.created.append(path)


def scaffold(root: Path, name: str | None = None, force: bool = False) -> ScaffoldReport:
    """Create project skeleton files; never overwrites without ``force``."""
    name = name or root.resolve().name
    package = _package_name(name)
    report = ScaffoldReport()

    _write(
        root / "pyproject.toml",
        _PYPROJECT_TEMPLATE.format(name=name, package=package),
        force,
        report,
    )
    _write(root / ".gitignore", _GITIGNORE_TEMPLATE, force, report)
    _write(root / "README.md", _README_TEMPLATE.format(name=name), force, report)
    _write(
        root / "src" / package / "__init__.py",
        _INIT_PY_TEMPLATE.format(name=name),
        force,
        report,
    )
    _write(root / "tests" / "test_placeholder.py", _TEST_TEMPLATE, force, report)
    return report
