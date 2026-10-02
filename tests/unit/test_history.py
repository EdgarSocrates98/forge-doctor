"""Roadmap-3 phase 4: historical intelligence (spec 205)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from forge_doctor.cli.app import app
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.history import (
    HistoryError,
    diff_snapshots,
    list_snapshots,
    load_snapshot,
    prune,
    record_snapshot,
    resolve_snapshot,
    trend,
)
from forge_doctor.core.models import CheckResult, ScanReport, Severity


def _finding(check_id: str, fp: str, file: str = "a.py") -> CheckResult:
    return CheckResult(
        check_id=check_id,
        title="t",
        severity=Severity.WARNING,
        category="python",
        message="m",
        file=Path(file),
        line=1,
        fingerprint=fp,
    )


def _report(*results: CheckResult) -> ScanReport:
    return ScanReport(version="0.1.0", project=Path("/tmp/x"), results=list(results))


def test_record_and_list(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print('x')\n")
    ctx = ProjectContext(root=tmp_path)
    report = _report(_finding("X001", "fp1"), _finding("X002", "fp2"))
    p1 = record_snapshot(report, tmp_path, ctx)
    assert p1.is_file() and p1.parent.name == "history"
    record_snapshot(report, tmp_path, ctx)
    snaps = list_snapshots(tmp_path)
    assert len(snaps) == 2  # same-second names disambiguated
    snap = load_snapshot(p1)
    assert len(snap.findings) == 2
    assert snap.summary["total"] == 2


def test_record_never_runs_without_flag(tmp_path: Path) -> None:
    """Normal scans never touch .forge-doctor/history."""
    (tmp_path / "a.py").write_text("print('x')\n")
    result = CliRunner().invoke(app, ["scan", str(tmp_path), "--quiet"])
    assert result.exit_code == 0
    assert not (tmp_path / ".forge-doctor" / "history").exists()


def test_diff_new_resolved_entities(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print('x')\n")
    ctx = ProjectContext(root=tmp_path)
    record_snapshot(
        _report(_finding("X001", "fp1"), _finding("ARCH001", "fp-drift")),
        tmp_path,
        ctx,
    )
    (tmp_path / "b.py").write_text("print('y')\n")
    record_snapshot(
        _report(_finding("X002", "fp9")),
        tmp_path,
        ProjectContext(root=tmp_path),
    )
    snaps = [load_snapshot(p) for p in list_snapshots(tmp_path)]
    diff = diff_snapshots(snaps[0], snaps[1])
    assert [f["check_id"] for f in diff.new_findings] == ["X002"]
    assert sorted(f["check_id"] for f in diff.resolved_findings) == ["ARCH001", "X001"]
    assert diff.drift_resolved  # ARCH finding left the series


def test_resolve_snapshot_by_index_and_name(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print('x')\n")
    p1 = record_snapshot(_report(_finding("X001", "fp1")), tmp_path, ProjectContext(root=tmp_path))
    assert resolve_snapshot(tmp_path, "0") == p1
    assert resolve_snapshot(tmp_path, p1.stem) == p1
    with pytest.raises(HistoryError):
        resolve_snapshot(tmp_path, "nope")


def test_trend_and_prune(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print('x')\n")
    ctx = ProjectContext(root=tmp_path)
    for i in range(4):
        record_snapshot(_report(_finding(f"X00{i}", f"fp{i}")), tmp_path, ctx)
    snaps = [load_snapshot(p) for p in list_snapshots(tmp_path)]
    rows = trend(snaps)
    assert len(rows) == 4
    assert all(r["total"] == 1 for r in rows)
    assert rows[0]["by_category"] == {"python": 1}
    removed = prune(tmp_path, 2)
    assert len(removed) == 2
    assert len(list_snapshots(tmp_path)) == 2


def test_history_cli_list_diff_trend(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print('x')\n")
    runner = CliRunner()
    for _ in range(2):
        runner.invoke(app, ["scan", str(tmp_path), "--record", "--quiet"])
    listed = runner.invoke(app, ["history", "--path", str(tmp_path)])
    assert listed.exit_code == 0
    assert "Snapshot" in listed.stdout
    diff = runner.invoke(app, ["history", "diff", "--path", str(tmp_path), "--last"])
    assert diff.exit_code == 0
    assert "findings:" in diff.stdout
    trend_out = runner.invoke(app, ["history", "trend", "--path", str(tmp_path), "-f", "json"])
    assert trend_out.exit_code == 0
    data = json.loads(trend_out.stdout)
    assert len(data["series"]) == 2


def test_scan_record_with_keep_prunes(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print('x')\n")
    runner = CliRunner()
    for _ in range(3):
        result = runner.invoke(app, ["scan", str(tmp_path), "--record", "--keep", "2", "--quiet"])
        assert result.exit_code == 0, result.stdout
    assert len(list_snapshots(tmp_path)) == 2
