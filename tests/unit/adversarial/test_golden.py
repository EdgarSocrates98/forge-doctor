"""Golden repo adversarial cases: corrupt snapshots, ordering, honesty."""

from __future__ import annotations

import json
from pathlib import Path

from forge_doctor.core.golden import (
    _diff,
    run_all_golden,
    run_golden,
    update_golden,
)


def _golden(root: Path, name: str, files: dict[str, str]) -> Path:
    repo = root / name / "repo"
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return repo


def test_corrupt_snapshot_is_error_not_crash(tmp_path: Path) -> None:
    repo = _golden(tmp_path, "corrupt", {"a.py": "x = 1\n"})
    exp = repo.parent / "expected"
    exp.mkdir(parents=True)
    (exp / "findings.json").write_text("{broken", encoding="utf-8")
    report = run_golden(repo)
    assert not report.passed
    assert any("findings" in e for e in report.errors)


def test_diff_lists_both_directions(tmp_path: Path) -> None:
    repo = _golden(tmp_path, "both", {"a.py": "x = 1\n"})
    update_golden(repo)
    findings = repo.parent / "expected/findings.json"
    rows = json.loads(findings.read_text())
    removed_row = rows.pop()  # snapshot row that no longer exists
    rows.append(
        {
            "check_id": "FAKE999",
            "severity": "error",
            "file": None,
            "line": None,
            "fingerprint": "zzz",
        }
    )
    findings.write_text(json.dumps(rows), encoding="utf-8")
    report = run_golden(repo)
    diff = next(d for d in report.diffs if d.artifact == "findings")
    assert any("FAKE999" in r for r in diff.removed)  # snapshot-only row
    assert any(json.loads(r)["check_id"] == removed_row["check_id"] for r in diff.added)


def test_snapshot_text_never_counts_as_evidence(tmp_path: Path) -> None:
    """Snapshot JSON containing 'iceberg' must not mint iceberg findings."""
    repo = _golden(tmp_path, "contam", {"a.py": "x = 1\n"})
    update_golden(repo)
    mig = repo.parent / "expected/migrations.json"
    mig.write_text(
        '[{"path_id": "iceberg-v1-to-v2", "source": "iceberg format-version 1",'
        ' "target": "iceberg format-version 2", "affected_entities": [],'
        ' "blockers": [], "warnings": []}]',
        encoding="utf-8",
    )
    report = run_golden(repo)
    diff = next(d for d in report.diffs if d.artifact == "migrations")
    # the fabricated row shows as `removed` (stale expected); crucially the
    # current run added NO iceberg plan -> snapshot text was never evidence
    assert not diff.added
    assert diff.removed and "iceberg-v1-to-v2" in diff.removed[0]


def test_empty_golden_root_reports_nothing(tmp_path: Path) -> None:
    assert run_all_golden(tmp_path) == []


def test_row_order_independence(tmp_path: Path) -> None:
    a = {"check_id": "X", "severity": "w", "file": "a.py", "line": 1, "fingerprint": "1"}
    b = {"check_id": "Y", "severity": "w", "file": "a.py", "line": 2, "fingerprint": "2"}
    assert _diff("findings", [a, b], [b, a]).ok


def test_name_filter_matches_scenario_dir(tmp_path: Path) -> None:
    _golden(tmp_path, "keep", {"a.py": "x = 1\n"})
    _golden(tmp_path, "skip", {"a.py": "x = 1\n"})
    reports = run_all_golden(tmp_path, name="keep")
    assert len(reports) == 1 and reports[0].name == "keep"
