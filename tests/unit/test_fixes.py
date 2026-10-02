"""Unit tests for safe-fix intelligence (core/fixes.py + `forge-doctor fix`)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from forge_doctor.checks.git_checks import CHECKS as GIT_CHECKS
from forge_doctor.checks.python_env import CHECKS as PY_CHECKS
from forge_doctor.checks.repository import CHECKS as REP_CHECKS
from forge_doctor.checks.terraform import CHECKS as TF_CHECKS
from forge_doctor.cli import app
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.fixes import (
    MANUAL,
    REVIEW,
    SAFE,
    FixAction,
    FixRefused,
    apply_fix,
    plan_fixes,
    register_transform,
)
from forge_doctor.core.models import CheckResult, Severity

runner = CliRunner()

_ALL_CHECKS = [*GIT_CHECKS, *PY_CHECKS, *REP_CHECKS, *TF_CHECKS]

_PYPROJECT = '[project]\nname = "x"\nversion = "0.1.0"\n'


def _result(
    check_id: str,
    file: str | None = None,
    message: str = "m",
    severity: Severity = Severity.WARNING,
) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        title="t",
        severity=severity,
        category="x",
        message=message,
        file=Path(file) if file else None,
    )


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --- planning ---------------------------------------------------------------


def test_py002_proposes_requires_python(project: Path) -> None:
    _write(project, "pyproject.toml", _PYPROJECT)
    ctx = ProjectContext(root=project)
    actions = plan_fixes([_result("PY002")], _ALL_CHECKS, ctx)
    assert len(actions) == 1
    action = actions[0]
    assert action.fix_class == SAFE
    assert action.file == "pyproject.toml"
    assert 'requires-python = ">=3.10"' in action.after
    assert "+requires-python" in action.diff()


def test_py002_not_proposed_when_declared(project: Path) -> None:
    _write(project, "pyproject.toml", _PYPROJECT + 'requires-python = ">=3.11"\n')
    ctx = ProjectContext(root=project)
    assert plan_fixes([_result("PY002")], _ALL_CHECKS, ctx) == []


def test_py002_not_proposed_without_project_table(project: Path) -> None:
    _write(project, "pyproject.toml", '[tool.poetry]\nname = "x"\n')
    ctx = ProjectContext(root=project)
    assert plan_fixes([_result("PY002")], _ALL_CHECKS, ctx) == []


def test_git002_creates_gitignore_with_pattern(project: Path) -> None:
    ctx = ProjectContext(root=project)
    actions = plan_fixes(
        [_result("GIT002", file=".env", severity=Severity.ERROR)], _ALL_CHECKS, ctx
    )
    assert len(actions) == 1
    action = actions[0]
    assert action.fix_class == SAFE and action.create
    assert action.after == ".env\n"


def test_git002_appends_missing_pattern_only(project: Path) -> None:
    _write(project, ".gitignore", "build/\n.env\n")
    ctx = ProjectContext(root=project)
    actions = plan_fixes(
        [
            _result("GIT002", file=".env", severity=Severity.ERROR),
            _result("GIT002", file="secrets.txt", severity=Severity.ERROR),
        ],
        _ALL_CHECKS,
        ctx,
    )
    assert actions[0].after == "build/\n.env\nsecrets.txt\n"


def test_git003_artifact_patterns(project: Path) -> None:
    _write(project, ".gitignore", ".env\n")
    ctx = ProjectContext(root=project)
    actions = plan_fixes([_result("GIT003")], _ALL_CHECKS, ctx)
    assert "__pycache__/" in actions[0].after


def test_rep004_creates_gitignore(project: Path) -> None:
    ctx = ProjectContext(root=project)
    actions = plan_fixes([_result("REP004")], _ALL_CHECKS, ctx)
    assert actions[0].create and "__pycache__/" in actions[0].after


def test_conflicting_gitignore_actions_superseded(project: Path) -> None:
    """GIT002 and REP004 both target .gitignore when it's missing: the
    first (registry order) wins, the other is marked superseded."""
    ctx = ProjectContext(root=project)
    actions = plan_fixes(
        [_result("GIT002", file=".env", severity=Severity.ERROR), _result("REP004")],
        _ALL_CHECKS,
        ctx,
    )
    assert len(actions) == 2
    winners = [a for a in actions if not a.superseded]
    losers = [a for a in actions if a.superseded]
    assert len(winners) == 1 and len(losers) == 1


def test_rep007_review_tier_delete(project: Path) -> None:
    _write(project, "requirements.txt", "boto3\n")
    ctx = ProjectContext(root=project)
    actions = plan_fixes(
        [
            _result(
                "REP007",
                message="Conflicting dependency manifests: requirements.txt + poetry.lock.",
            )
        ],
        _ALL_CHECKS,
        ctx,
    )
    assert actions[0].fix_class == REVIEW and actions[0].delete


def test_manual_findings_emit_guidance(project: Path) -> None:
    ctx = ProjectContext(root=project)
    actions = plan_fixes([_result("TF001")], _ALL_CHECKS, ctx)
    assert len(actions) == 1
    assert actions[0].fix_class == MANUAL
    assert actions[0].guidance  # carries the check's fix text


def test_plan_is_deterministic(project: Path) -> None:
    _write(project, "pyproject.toml", _PYPROJECT)
    ctx = ProjectContext(root=project)
    results = [_result("PY002"), _result("GIT002", file=".env", severity=Severity.ERROR)]
    a = [x.diff() + x.title for x in plan_fixes(list(results), _ALL_CHECKS, ctx)]
    b = [x.diff() + x.title for x in plan_fixes(list(results), _ALL_CHECKS, ctx)]
    assert a == b


# --- apply ------------------------------------------------------------------


def test_apply_writes_and_marks_audit(project: Path) -> None:
    _write(project, "pyproject.toml", _PYPROJECT)
    ctx = ProjectContext(root=project)
    action = plan_fixes([_result("PY002")], _ALL_CHECKS, ctx)[0]
    record = apply_fix(action, project)
    assert record["status"] == "updated"
    assert record["check_id"] == "PY002"
    assert 'requires-python = ">=3.10"' in (project / "pyproject.toml").read_text()


def test_apply_is_idempotent_via_replan(project: Path) -> None:
    _write(project, "pyproject.toml", _PYPROJECT)
    ctx = ProjectContext(root=project)
    apply_fix(plan_fixes([_result("PY002")], _ALL_CHECKS, ctx)[0], project)
    ctx2 = ProjectContext(root=project)
    assert plan_fixes([_result("PY002")], _ALL_CHECKS, ctx2) == []


def test_stale_source_aborts(project: Path) -> None:
    _write(project, "pyproject.toml", _PYPROJECT)
    ctx = ProjectContext(root=project)
    action = plan_fixes([_result("PY002")], _ALL_CHECKS, ctx)[0]
    _write(project, "pyproject.toml", _PYPROJECT + "# drift\n")
    assert apply_fix(action, project)["status"] == "stale-source"
    assert "requires-python" not in (project / "pyproject.toml").read_text()


def test_review_delete_applies(project: Path) -> None:
    _write(project, "requirements.txt", "boto3\n")
    ctx = ProjectContext(root=project)
    action = plan_fixes(
        [_result("REP007", message="x requirements.txt + poetry.lock y")], _ALL_CHECKS, ctx
    )[0]
    assert apply_fix(action, project)["status"] == "deleted"
    assert not (project / "requirements.txt").exists()


def test_superseded_never_applies(project: Path) -> None:
    ctx = ProjectContext(root=project)
    actions = plan_fixes(
        [_result("GIT002", file=".env", severity=Severity.ERROR), _result("REP004")],
        _ALL_CHECKS,
        ctx,
    )
    loser = next(a for a in actions if a.superseded)
    assert apply_fix(loser, project)["status"] == "superseded"


def test_apply_refuses_path_escape(project: Path) -> None:
    action = FixAction(
        check_id="X",
        check_ids=("X",),
        fingerprints=(),
        file="../evil.txt",
        title="t",
        fix_class=SAFE,
        transform="t",
        before="",
        after="x",
    )
    with pytest.raises(FixRefused):
        apply_fix(action, project)


def test_manual_class_never_applies(project: Path) -> None:
    action = FixAction(
        check_id="TF001",
        check_ids=("TF001",),
        fingerprints=(),
        file="main.tf",
        title="t",
        fix_class=MANUAL,
        transform="t",
        before="",
        after="x",
    )
    with pytest.raises(FixRefused):
        apply_fix(action, project)


def test_manual_category_transform_registration_refused() -> None:
    def bogus(ctx, results):  # pragma: no cover - never reached
        return []

    categories = {c.id: c.category for c in _ALL_CHECKS}
    with pytest.raises(FixRefused):
        register_transform(bogus, ("TF001",), category_lookup=lambda cid: categories.get(cid))


# --- CLI --------------------------------------------------------------------


def _repo_with_issues(root: Path) -> None:
    _write(root, "pyproject.toml", _PYPROJECT)
    _write(root, "requirements.txt", "boto3\n")
    _write(root, "poetry.lock", "# lock\n")


def test_cli_dry_run_writes_nothing(project: Path) -> None:
    _repo_with_issues(project)
    result = runner.invoke(app, ["fix", str(project)])
    assert result.exit_code == 0, result.output
    assert "dry run" in result.output
    assert "requires-python" not in (project / "pyproject.toml").read_text()
    assert (project / "requirements.txt").exists()


def test_cli_apply_writes_safe_only(project: Path) -> None:
    _repo_with_issues(project)
    result = runner.invoke(app, ["fix", str(project), "--apply"])
    assert result.exit_code == 0, result.output
    assert 'requires-python = ">=3.10"' in (project / "pyproject.toml").read_text()
    # review-tier delete did NOT apply
    assert (project / "requirements.txt").exists()


def test_cli_apply_class_review_also_deletes(project: Path) -> None:
    _repo_with_issues(project)
    result = runner.invoke(app, ["fix", str(project), "--apply", "--class", "review"])
    assert result.exit_code == 0, result.output
    assert not (project / "requirements.txt").exists()


def test_cli_json_audit(project: Path) -> None:
    _repo_with_issues(project)
    result = runner.invoke(app, ["fix", str(project), "--apply", "--json"])
    assert result.exit_code == 0, result.output
    import json

    audit = json.loads(result.output)
    applied_files = {row["file"] for row in audit["applied"]}
    assert "pyproject.toml" in applied_files


def test_fixable_marked_on_results(project: Path) -> None:
    _write(project, "pyproject.toml", _PYPROJECT)
    ctx = ProjectContext(root=project)
    result = _result("PY002")
    plan_fixes([result], _ALL_CHECKS, ctx)
    assert result.fixable is True
