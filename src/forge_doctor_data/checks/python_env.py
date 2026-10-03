"""Python environment checks (PY*): interpreter, packaging metadata, tooling."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version

from forge_doctor_data.analyzers.pyproject import requires_python
from forge_doctor_data.core.models import EvidenceKind, Severity
from forge_doctor_data.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor_data.core.context import ProjectContext
    from forge_doctor_data.core.models import CheckResult

CATEGORY = "python"


def _tool_table(pyproject: dict[str, Any] | None, name: str) -> dict[str, Any] | None:
    """Return ``[tool.<name>]`` as a dict, or ``None``."""
    if not pyproject:
        return None
    tool = pyproject.get("tool")
    if not isinstance(tool, dict):
        return None
    table = tool.get(name)
    return table if isinstance(table, dict) else None


def _format_version(version: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


class PythonVersion(CheckBase):
    """PY001 - a Python interpreter is available on PATH."""

    id = "PY001"
    title = "Python version"
    category = CATEGORY
    evidence_kind = EvidenceKind.CONFIG
    why = "Every other diagnostic assumes a working interpreter."
    when_ok = "Anchor check - always reports."
    fix = "Install Python and put it on PATH."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        version = ctx.python_version
        if version is None:
            return [
                self.result(
                    Severity.INFO,
                    "No Python interpreter found on PATH.",
                    recommendation="Install Python and ensure it is on PATH.",
                )
            ]
        return [self.result(Severity.PASS, f"Python {_format_version(version)} found on PATH.")]


class RequiresPython(CheckBase):
    """PY002 - ``requires-python`` is declared in packaging metadata."""

    id = "PY002"
    title = "requires-python"
    category = CATEGORY
    evidence_kind = EvidenceKind.CONFIG
    why = "'Any Python' rots - the interpreter and the code diverge silently."
    when_ok = "Truly single-interpreter, pinned environments."
    fix = "Declare requires-python (e.g. '>=3.11') in pyproject.toml."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        requirement = requires_python(ctx.pyproject) if ctx.pyproject else None
        if requirement:
            return [
                self.result(
                    Severity.PASS,
                    f"requires-python declared: {requirement}.",
                    file=Path("pyproject.toml"),
                )
            ]
        return [
            self.result(
                Severity.WARNING,
                "requires-python is not declared.",
                recommendation="Declare requires-python (e.g. '>=3.11') in pyproject.toml.",
            )
        ]


class VersionCompatibility(CheckBase):
    """PY003 - the interpreter on PATH satisfies ``requires-python``."""

    id = "PY003"
    title = "Version compatibility"
    category = CATEGORY
    evidence_kind = EvidenceKind.CONFIG
    why = "Running the wrong interpreter breaks at install or at runtime."
    when_ok = "Never - a real mismatch is an error."
    fix = "Switch interpreter or adjust the requires-python constraint."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        requirement = requires_python(ctx.pyproject) if ctx.pyproject else None
        if requirement is None:
            return []  # PY002 reports the missing declaration.
        try:
            specifier = SpecifierSet(requirement)
        except InvalidSpecifier:
            return [
                self.result(
                    Severity.INFO,
                    f"requires-python '{requirement}' is not a PEP 440 specifier.",
                    recommendation="Use a PEP 440 specifier such as '>=3.11'.",
                )
            ]
        version = ctx.python_version
        if version is None:
            return [
                self.result(
                    Severity.INFO,
                    "Cannot compare: no Python interpreter found on PATH.",
                )
            ]
        interpreter = Version(_format_version(version))
        if specifier.contains(interpreter, prereleases=True):
            return [
                self.result(
                    Severity.PASS,
                    f"Python {interpreter} satisfies requires-python '{requirement}'.",
                )
            ]
        return [
            self.result(
                Severity.ERROR,
                f"Python {interpreter} does not satisfy requires-python '{requirement}'.",
                recommendation="Switch to a compatible interpreter or adjust requires-python.",
            )
        ]


class VirtualEnvironment(CheckBase):
    """PY004 - the project uses an isolated virtual environment."""

    id = "PY004"
    title = "Virtual environment"
    category = CATEGORY
    evidence_kind = EvidenceKind.CONFIG
    why = "Shared-site installs pollute and leak between projects."
    when_ok = "Containerized or global installs by design."
    fix = "Create one with `python -m venv .venv`."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        if ctx.has_dir(".venv") or ctx.has_dir("venv") or ctx.env.get("VIRTUAL_ENV"):
            return [self.result(Severity.PASS, "Virtual environment detected.")]
        return [
            self.result(
                Severity.INFO,
                "No .venv/ directory and VIRTUAL_ENV is not set.",
                recommendation="Create a virtual environment (python -m venv .venv).",
            )
        ]


class PytestConfigured(CheckBase):
    """PY005 - pytest (or tox) has a configuration entry point."""

    id = "PY005"
    title = "Test configuration"
    category = CATEGORY
    evidence_kind = EvidenceKind.CONFIG
    why = "Config-less pytest runs on silent defaults and wrong testpaths."
    when_ok = "Projects with no test suite (see REP005)."
    fix = "Add [tool.pytest.ini_options] to pyproject.toml."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        pytest_table = _tool_table(ctx.pyproject, "pytest")
        configured = (pytest_table is not None and "ini_options" in pytest_table) or any(
            ctx.has_file(name) for name in ("pytest.ini", ".pytest.ini", "tox.ini")
        )
        if configured:
            return [self.result(Severity.PASS, "Test runner configuration found.")]
        return [
            self.result(
                Severity.INFO,
                "No pytest or tox configuration found.",
                recommendation="Add [tool.pytest.ini_options] to pyproject.toml.",
            )
        ]


class LinterConfigured(CheckBase):
    """PY006 - a linter (ruff/flake8/pre-commit) is configured."""

    id = "PY006"
    title = "Linter configuration"
    category = CATEGORY
    evidence_kind = EvidenceKind.CONFIG
    why = "A configured linter turns style and bug-patterns into an automatic gate."
    when_ok = "Prototypes."
    fix = "Add a [tool.ruff] section or ruff.toml."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        configured = _tool_table(ctx.pyproject, "ruff") is not None or any(
            ctx.has_file(name)
            for name in ("ruff.toml", ".ruff.toml", ".flake8", ".pre-commit-config.yaml")
        )
        if configured:
            return [self.result(Severity.PASS, "Linter configuration found.")]
        return [
            self.result(
                Severity.INFO,
                "No linter configuration found (ruff, flake8, pre-commit).",
                recommendation="Add a [tool.ruff] section or a ruff.toml file.",
            )
        ]


class TypeCheckerConfigured(CheckBase):
    """PY007 - a static type checker (mypy/pyright) is configured."""

    id = "PY007"
    title = "Type checker"
    category = CATEGORY
    evidence_kind = EvidenceKind.CONFIG
    why = "Type checking catches whole classes of bugs lint cannot see."
    when_ok = "Tiny scripts and prototypes."
    fix = "Add [tool.mypy] or pyrightconfig.json."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        configured = (
            _tool_table(ctx.pyproject, "mypy") is not None
            or _tool_table(ctx.pyproject, "pyright") is not None
            or any(ctx.has_file(name) for name in ("mypy.ini", ".mypy.ini", "pyrightconfig.json"))
        )
        if configured:
            return [self.result(Severity.PASS, "Type checker configuration found.")]
        return [
            self.result(
                Severity.INFO,
                "No type checker configuration found (mypy, pyright).",
                recommendation="Add a [tool.mypy] section or a pyrightconfig.json file.",
            )
        ]


CHECKS: list[Check] = [
    PythonVersion(),
    RequiresPython(),
    VersionCompatibility(),
    VirtualEnvironment(),
    PytestConfigured(),
    LinterConfigured(),
    TypeCheckerConfigured(),
]
