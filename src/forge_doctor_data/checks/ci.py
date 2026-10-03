"""CI checks (CI*): text-based pipeline hygiene.

GitHub Actions workflows are parsed line-by-line; GitLab/Azure/CircleCI
files only prove that CI exists - they are never deep-parsed. No YAML
dependency is used.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from forge_doctor_data.core.models import EvidenceKind, Severity
from forge_doctor_data.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor_data.core.context import ProjectContext
    from forge_doctor_data.core.models import CheckResult

CATEGORY = "ci"

_USES_RE = re.compile(r"^\s*-?\s*uses:\s*['\"]?([^\s'\"#]+)", re.IGNORECASE)
_PYTHON_RE = re.compile(r"python-version|setup-python", re.IGNORECASE)
_TEST_STEP_RE = re.compile(r"\b(pytest|ruff|mypy|tox|nox|lint|test)\b", re.IGNORECASE)
_FLOATING_REFS = frozenset({"main", "master", "develop", "latest"})
_SHA_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
_TAG_RE = re.compile(r"^v?\d+(\.\d+){0,3}([-+][0-9A-Za-z.+-]+)?$")


def _gha_workflows(ctx: ProjectContext) -> list[Path]:
    """Relative paths to .github/workflows/*.yml|*.yaml (direct children only)."""
    return sorted(
        path
        for path in ctx.files
        if len(path.parts) == 3
        and path.parts[0] == ".github"
        and path.parts[1] == "workflows"
        and path.suffix in {".yml", ".yaml"}
    )


def _ci_providers(ctx: ProjectContext) -> list[str]:
    """Detected CI provider names, for the anchor check's message."""
    providers: list[str] = []
    if _gha_workflows(ctx):
        providers.append("GitHub Actions")
    if ctx.has_file(".gitlab-ci.yml"):
        providers.append("GitLab CI")
    if ctx.has_file("azure-pipelines.yml"):
        providers.append("Azure Pipelines")
    if ctx.has_file(".circleci/config.yml"):
        providers.append("CircleCI")
    return providers


def _workflow_lines(ctx: ProjectContext) -> list[tuple[Path, list[str]]]:
    """(path, lines) for every readable GitHub Actions workflow."""
    out: list[tuple[Path, list[str]]] = []
    for path in _gha_workflows(ctx):
        text = ctx.read_text(path)
        if text is not None:
            out.append((path, text.splitlines()))
    return out


class CiConfigured(CheckBase):
    """CI001 - anchor check: is any CI provider configured?"""

    id = "CI001"
    title = "CI configured"
    category = CATEGORY
    evidence_kind = EvidenceKind.CONFIG
    why = "Pipelines catch breakage before code reaches the main branch."
    when_ok = "Throwaway experiments or projects exercised purely by hand."
    fix = "Add a CI pipeline, e.g. .github/workflows/ci.yml running tests and lint."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        providers = _ci_providers(ctx)
        if not providers:
            return [
                self.result(
                    Severity.INFO,
                    "No CI configuration found.",
                    recommendation=(
                        "Add a CI pipeline (e.g. .github/workflows) to run tests and lint."
                    ),
                )
            ]
        return [self.result(Severity.PASS, f"CI configured: {', '.join(providers)}.")]


class UnpinnedActions(CheckBase):
    """CI002 - `uses:` refs: full SHA is strong, tags mutable, branches float."""

    id = "CI002"
    title = "Unpinned actions"
    category = CATEGORY
    evidence_kind = EvidenceKind.CONFIG
    why = "Floating refs can move or disappear, silently changing what CI executes."
    when_ok = "Local composite actions (./...) and docker:// images are not action refs."
    fix = "Pin each action to a full-length commit SHA (tags are mutable)."
    tags: tuple[str, ...] = ("security", "supply-chain")

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        results: list[CheckResult] = []
        for path, lines in _workflow_lines(ctx):
            for lineno, line in enumerate(lines, start=1):
                match = _USES_RE.match(line)
                if match is None:
                    continue
                action = match.group(1)
                if action.startswith("./") or "://" in action:
                    continue  # local path or docker:// image - not a marketplace ref.
                evidence = line.strip()[:200]
                if "@" not in action:
                    results.append(
                        self.result(
                            Severity.WARNING,
                            f"Action '{action}' has no pinned ref.",
                            file=path,
                            line=lineno,
                            evidence=evidence,
                            recommendation="Pin to a full commit SHA.",
                        )
                    )
                    continue
                name, _, ref = action.rpartition("@")
                if _SHA_RE.match(ref):
                    continue  # strongest pin
                if _TAG_RE.match(ref):
                    results.append(
                        self.result(
                            Severity.INFO,
                            (f"Action '{name}' pinned to tag '@{ref}' - tags are mutable."),
                            file=path,
                            line=lineno,
                            evidence=evidence,
                            recommendation="Prefer a full-length commit SHA.",
                        )
                    )
                    continue
                results.append(
                    self.result(
                        Severity.WARNING,
                        f"Action '{name}' floats on '{ref}'.",
                        file=path,
                        line=lineno,
                        evidence=evidence,
                        recommendation="Pin the action to a full commit SHA.",
                    )
                )
        return results


class PythonVersion(CheckBase):
    """CI003 - workflows should pin the Python they test against."""

    id = "CI003"
    title = "Python version"
    category = CATEGORY
    evidence_kind = EvidenceKind.CONFIG
    why = "Testing against an unpinned interpreter hides version-specific breakage."
    when_ok = "Non-Python projects or container-based CI where the image pins Python."
    fix = "Add actions/setup-python with an explicit python-version."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        workflows = _workflow_lines(ctx)
        if not workflows:
            return []
        if any(_PYTHON_RE.search(line) for _, lines in workflows for line in lines):
            return [self.result(Severity.PASS, "Workflows configure a Python version.")]
        return [
            self.result(
                Severity.INFO,
                "GitHub Actions workflows never set python-version or use setup-python.",
                recommendation="Pin Python via actions/setup-python.",
            )
        ]


class TestLintSteps(CheckBase):
    """CI004 - workflows should actually run tests or linters."""

    id = "CI004"
    title = "Test/lint steps"
    category = CATEGORY
    evidence_kind = EvidenceKind.CONFIG
    why = "A pipeline that only checks out code gives false confidence."
    when_ok = "Release-only or publish workflows covered by a separate CI pipeline."
    fix = "Add a step running pytest/ruff/mypy (or tox/nox)."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        workflows = _workflow_lines(ctx)
        if not workflows:
            return []
        if any(_TEST_STEP_RE.search(line) for _, lines in workflows for line in lines):
            return [self.result(Severity.PASS, "Workflows run test or lint steps.")]
        return [
            self.result(
                Severity.INFO,
                "No workflow step mentions pytest, ruff, mypy, tox/nox, lint or test.",
                recommendation="Add a step running the project's tests or linters.",
            )
        ]


CHECKS: list[Check] = [
    CiConfigured(),
    UnpinnedActions(),
    PythonVersion(),
    TestLintSteps(),
]
