"""Dependency checks (DEP###): lock files, constraints, dev-tool leaks."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from forge_doctor.analyzers.pyproject import (
    declared_dependencies,
    dev_dependencies,
    is_poetry_managed,
)
from forge_doctor.core.models import EvidenceKind, Severity
from forge_doctor.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.models import CheckResult

_DEV_TOOL_NAMES = frozenset(
    {
        "pytest",
        "pytest-cov",
        "ruff",
        "mypy",
        "pyright",
        "black",
        "isort",
        "flake8",
        "pylint",
        "coverage",
        "tox",
        "nox",
        "pre-commit",
        "hypothesis",
        "sphinx",
        "mkdocs",
    }
)
_DEV_TOOL_PREFIXES = ("pytest-", "flake8-")


def _is_dev_tool(name: str) -> bool:
    lowered = name.lower()
    if lowered in _DEV_TOOL_NAMES:
        return True
    return any(lowered.startswith(prefix) for prefix in _DEV_TOOL_PREFIXES)


class LockFile(CheckBase):
    """DEP001: a dependency lock/pin file should exist and match the manager."""

    id = "DEP001"
    title = "Lock file"
    category = "dependencies"
    evidence_kind = EvidenceKind.CONFIG
    why = "Unlocked installs are unrepeatable across machines and time."
    when_ok = "Libraries pinned by consumers - still commit a lock for development."
    fix = "Run `poetry lock` (or pin requirements.txt) and commit it."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        pyproject = ctx.pyproject
        if pyproject is None:
            return []
        if is_poetry_managed(pyproject):
            if ctx.has_file("poetry.lock"):
                return [self.result(Severity.PASS, "poetry.lock present")]
            return [
                self.result(
                    Severity.WARNING,
                    "poetry-managed project without poetry.lock",
                    recommendation="Run `poetry lock` and commit the lock file.",
                )
            ]
        if ctx.has_file("requirements.txt"):
            return [self.result(Severity.PASS, "requirements.txt present")]
        return [
            self.result(
                Severity.INFO,
                "no lock file detected",
                recommendation=(
                    "Pin dependencies (poetry.lock, requirements.txt, uv.lock) "
                    "for reproducible installs."
                ),
            )
        ]


class UnrestrictedDependencies(CheckBase):
    """DEP002: runtime dependencies should carry a version constraint."""

    id = "DEP002"
    title = "Unrestricted dependencies"
    category = "dependencies"
    evidence_kind = EvidenceKind.CONFIG
    why = "A `*` or bare name lets any future release break the install."
    when_ok = "Private metapackages where you own every release."
    fix = "Declare a minimum or compatible version range."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        pyproject = ctx.pyproject
        if pyproject is None:
            return []
        dependencies = declared_dependencies(pyproject)
        results = [
            self.result(
                Severity.WARNING,
                f"{name} has no version constraint",
                recommendation="Declare a minimum or compatible version range.",
            )
            for name in sorted(dependencies)
            if dependencies[name] in ("", "*")
        ]
        if not results:
            results.append(
                self.result(
                    Severity.PASS,
                    f"all {len(dependencies)} dependencies constrained",
                )
            )
        return results


class DevToolsAsRuntimeDeps(CheckBase):
    """DEP003: test/lint/format tools should not ship as runtime dependencies."""

    id = "DEP003"
    title = "Dev tools as runtime deps"
    category = "dependencies"
    evidence_kind = EvidenceKind.CONFIG
    why = "Dev tools shipped at runtime bloat installs and confuse consumers."
    when_ok = "Nearly never."
    fix = "Move them to a dev dependency group."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        pyproject = ctx.pyproject
        if pyproject is None:
            return []
        results = [
            self.result(
                Severity.WARNING,
                f"{name} is a development tool declared as a runtime dependency",
                recommendation=(
                    "Move it to a dev group ([dependency-groups] or "
                    "tool.poetry.group.dev.dependencies)."
                ),
            )
            for name in sorted(declared_dependencies(pyproject))
            if _is_dev_tool(name)
        ]
        if not results:
            results.append(self.result(Severity.PASS, "no dev tools in runtime dependencies"))
        return results


class RuntimeDevDuplication(CheckBase):
    """DEP004: names declared in both runtime and dev dependency groups."""

    id = "DEP004"
    title = "Runtime and dev duplication"
    category = "dependencies"
    evidence_kind = EvidenceKind.CONFIG
    why = "Duplicate declarations drift apart and resolve differently."
    when_ok = "Transitional states while migrating groups."
    fix = "Keep a single declaration."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        pyproject = ctx.pyproject
        if pyproject is None:
            return []
        runtime = declared_dependencies(pyproject)
        duplicated = sorted(set(runtime) & set(dev_dependencies(pyproject)))
        return [
            self.result(
                Severity.INFO,
                f"{name} declared in both runtime and dev dependencies",
                recommendation="Remove the duplicate dev-group declaration.",
            )
            for name in duplicated
        ]


def _run_poetry(
    binary: str, args: list[str], cwd: Path, timeout: float = 30.0
) -> subprocess.CompletedProcess[str] | None:
    """Run a read-only poetry subcommand; ``None`` on any failure."""
    try:
        return subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=cwd,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


class LockFreshness(CheckBase):
    """DEP005: poetry.lock should agree with pyproject.toml.

    When Poetry is available, ``poetry check --lock`` gives a definitive
    answer. Without it, a stale-mtime heuristic fires INFO (weak signal).
    """

    id = "DEP005"
    title = "Lock freshness"
    category = "dependencies"
    evidence_kind = EvidenceKind.CONFIG
    why = "A lock that disagrees with pyproject installs a different dependency set."
    when_ok = "Right after hand-editing pyproject, before re-locking."
    fix = "Run `poetry check --lock`; regenerate with `poetry lock`."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        pyproject_path = ctx.pyproject_path
        lock_path = ctx.root / "poetry.lock"
        if pyproject_path is None or not lock_path.is_file():
            return []

        binary = shutil.which("poetry")
        if binary is not None and is_poetry_managed(ctx.pyproject or {}):
            completed = _run_poetry(binary, ["check", "--lock"], ctx.root)
            if completed is not None:
                if completed.returncode == 0:
                    return [
                        self.result(Severity.PASS, "poetry.lock consistent with pyproject.toml")
                    ]
                return [
                    self.result(
                        Severity.WARNING,
                        "poetry check --lock: lock file inconsistent with pyproject.toml",
                        recommendation="Run `poetry lock` to regenerate the lock file.",
                    )
                ]

        try:
            stale = lock_path.stat().st_mtime < pyproject_path.stat().st_mtime
        except OSError:
            return []
        if not stale:
            return []
        return [
            self.result(
                Severity.INFO,
                "poetry.lock older than pyproject.toml",
                recommendation=(
                    "Dependencies may have changed after locking; run "
                    "`poetry check --lock` or regenerate the lock file."
                ),
            )
        ]


class PoetryAvailable(CheckBase):
    """DEP006: Poetry on PATH - only meaningful for poetry-managed projects."""

    id = "DEP006"
    title = "Poetry available"
    category = "dependencies"
    evidence_kind = EvidenceKind.CONFIG
    why = "Poetry-managed projects need the tool to install, lock and publish."
    when_ok = "Machines that only consume a built artifact."
    fix = "Install with `pipx install poetry`."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        pyproject = ctx.pyproject
        if pyproject is None or not is_poetry_managed(pyproject):
            return []
        binary = shutil.which("poetry")
        if binary is None:
            return [
                self.result(
                    Severity.INFO,
                    "poetry not on PATH",
                    recommendation="Install Poetry (e.g. `pipx install poetry`).",
                )
            ]
        # Generous timeout: the pipx Poetry shim cold-starts slowly on Windows.
        completed = _run_poetry(binary, ["--version"], ctx.root)
        if completed is None or completed.returncode != 0:
            return [
                self.result(
                    Severity.INFO,
                    "poetry found on PATH but `poetry --version` failed",
                )
            ]
        lines = completed.stdout.strip().splitlines()
        version = lines[0].strip() if lines else ""
        if not version:
            return [
                self.result(
                    Severity.INFO,
                    "poetry found on PATH but `poetry --version` failed",
                )
            ]
        return [self.result(Severity.PASS, version)]


CHECKS: list[Check] = [
    LockFile(),
    UnrestrictedDependencies(),
    DevToolsAsRuntimeDeps(),
    RuntimeDevDuplication(),
    LockFreshness(),
    PoetryAvailable(),
]
