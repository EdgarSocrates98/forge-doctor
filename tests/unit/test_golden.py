"""Golden repository snapshot runner (roadmap-2 phase 3)."""

from __future__ import annotations

import json
from pathlib import Path

from forge_doctor_data.core.golden import (
    discover_golden,
    run_all_golden,
    run_golden,
    update_golden,
)

_REPO = {
    "main.tf": 'resource "aws_dynamodb_table" "t" {\n  name = "orders"\n}\n',
    "app.py": "x = 1\n",
}


def _golden(root: Path, name: str, files: dict[str, str]) -> Path:
    repo = root / name / "repo"
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return repo


def test_update_then_run_passes(tmp_path: Path) -> None:
    repo = _golden(tmp_path, "g1", _REPO)
    update_golden(repo)
    report = run_golden(repo)
    assert report.passed
    assert not report.diffs and not report.missing_snapshot


def test_missing_snapshot_fails(tmp_path: Path) -> None:
    repo = _golden(tmp_path, "g2", _REPO)
    report = run_golden(repo)
    assert not report.passed
    assert sorted(report.missing_snapshot) == [
        "findings",
        "graph",
        "migrations",
        "remediations",
        "root_causes",
    ]


def test_drift_detected_as_diff(tmp_path: Path) -> None:
    repo = _golden(tmp_path, "g3", _REPO)
    update_golden(repo)
    # mutate the repo -> snapshots must flag the drift
    (repo / "main.tf").write_text(
        'resource "aws_dynamodb_table" "t" {\n'
        '  name = "orders"\n'
        "  stream_enabled = true\n"
        '  stream_view_type = "NEW_IMAGE"\n'
        "}\n",
        encoding="utf-8",
    )
    report = run_golden(repo)
    assert not report.passed
    kinds = {d.artifact for d in report.diffs}
    assert "findings" in kinds  # DDBSTR checks now fire


def test_snapshot_is_deterministic(tmp_path: Path) -> None:
    repo = _golden(tmp_path, "g4", _REPO)
    update_golden(repo)
    first = (repo.parent / "expected/findings.json").read_text()
    update_golden(repo)
    second = (repo.parent / "expected/findings.json").read_text()
    assert first == second


def test_discover_and_run_all(tmp_path: Path) -> None:
    _golden(tmp_path, "a", _REPO)
    _golden(tmp_path, "b", _REPO)
    (tmp_path / "not-golden").mkdir()
    repos = discover_golden(tmp_path)
    assert [r.parent.name for r in repos] == ["a", "b"]
    reports = run_all_golden(tmp_path, name="a")
    assert len(reports) == 1 and reports[0].name == "a"


def test_repo_dir_scoped_not_parent(tmp_path: Path) -> None:
    """expected/ must never contaminate the scanned project."""
    repo = _golden(tmp_path, "scoped", _REPO)
    update_golden(repo)
    # expected/*.json mentions tokens like "dynamodb" — a parent-scope scan
    # would pick them up as evidence and drift
    before = (repo.parent / "expected/findings.json").read_text()
    run_golden(repo)
    after = (repo.parent / "expected/findings.json").read_text()
    assert before == after
    assert '"dynamodb"' not in json.dumps(json.loads(before)[:0])
