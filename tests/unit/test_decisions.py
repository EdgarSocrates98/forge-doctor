"""Decision intelligence: ranked advise + CLI (spec 228)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from forge_doctor.cli.app import app
from forge_doctor.core.decisions import advise
from forge_doctor.core.diagnosis import FindingCluster, PromotionLevel
from forge_doctor.core.fixes import SAFE, FixAction
from forge_doctor.core.models import CheckResult, Confidence, Severity
from forge_doctor.core.platform_graph import (
    DataPlatformGraph,
    Entity,
    EntityKind,
    Relationship,
    RelKind,
)
from forge_doctor.core.remediation import RemediationPlan

runner = CliRunner()


def _finding(
    check_id: str,
    severity: Severity = Severity.WARNING,
    confidence: Confidence | None = Confidence.HIGH,
    fingerprint: str | None = None,
) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        title=f"{check_id} title",
        severity=severity,
        category="cat",
        message="m",
        fingerprint=fingerprint,
    )


def _graph() -> DataPlatformGraph:
    g = DataPlatformGraph()
    a = Entity(kind=EntityKind.COMPUTE_JOB, domain="glue", identifier="job")
    b = Entity(kind=EntityKind.STREAM, domain="kinesis", identifier="stream")
    c = Entity(kind=EntityKind.TABLE, domain="dynamodb", identifier="orders")
    for e in (a, b, c):
        g.add_entity(e)
    g.add_relationship(Relationship(src=b.id, dst=a.id, kind=RelKind.TRIGGERS))
    g.add_relationship(Relationship(src=a.id, dst=c.id, kind=RelKind.WRITES))
    return g


def test_empty_project_no_advice() -> None:
    assert advise([], [], [], [], _graph()) == []


def test_breakdown_sums_to_score() -> None:
    rows = advise([_finding("X001", Severity.ERROR)], [], [], [], _graph())
    assert len(rows) == 1
    assert rows[0].score == sum(rows[0].breakdown.values())
    assert rows[0].breakdown["severity"] == 100


def test_error_outranks_warning() -> None:
    rows = advise([_finding("WARN1"), _finding("ERR1", Severity.ERROR)], [], [], [], _graph())
    assert rows[0].check_id == "ERR1"


def test_policy_violation_boosted() -> None:
    rows = advise([_finding("POLICY001"), _finding("X001")], [], [], [], _graph())
    assert rows[0].check_id == "POLICY001"
    assert rows[0].breakdown["policy"] == 40


def test_cluster_and_blast_radius() -> None:
    g = _graph()
    f = _finding("STREAM001", fingerprint="fp1")
    cluster = FindingCluster(
        id="chain-abc",
        title="c",
        root_causes=("evidence",),
        symptoms=(),
        related_findings=("STREAM001:fp1",),
        affected_entities=("stream:kinesis:stream",),
        evidence=("e",),
        confidence=PromotionLevel.CONFIRMED,
        causal_edges=(),
    )
    rows = advise([f], [cluster], [], [], g)
    row = rows[0]
    # member 10 + size(1)*5 + confirmed 30 = 45; blast: job -> table = 2*3
    assert row.breakdown["cluster"] == 45
    assert row.breakdown["blast_radius"] == 6
    assert "compute_job:glue:job" in row.entities
    assert row.cluster_id == "chain-abc"


def test_fix_and_plan_attached() -> None:
    f = _finding("PY002")
    fix = FixAction(
        check_id="PY002",
        check_ids=("PY002",),
        fingerprints=(f.fingerprint or "",),
        file="pyproject.toml",
        title="Declare requires-python",
        fix_class=SAFE,
        transform="t",
        before="",
        after="",
    )
    plan = RemediationPlan(
        id="PLAN-PY002",
        problem="Pin the interpreter floor",
        check_id="PY002",
        targets=("pyproject.toml",),
        actions=(),
    )
    rows = advise([f], [], [plan], [fix], _graph())
    row = rows[0]
    assert row.fix_class == "safe"
    assert row.plan_id == "PLAN-PY002"
    assert row.action == "Pin the interpreter floor"
    assert row.breakdown["fix"] == 20 and row.breakdown["plan"] == 15


def test_unlinked_findings_state_unknowns() -> None:
    rows = advise([_finding("X001")], [], [], [], _graph())
    assert rows[0].unknowns == ("no entity attribution — findings unlinked to the graph",)


def test_deterministic_ordering() -> None:
    fs = [_finding("B001", fingerprint="fpB"), _finding("A001", fingerprint="fpA")]
    a = advise(fs, [], [], [], _graph())
    b = advise(list(reversed(fs)), [], [], [], _graph())
    assert [r.check_id for r in a] == [r.check_id for r in b]


def test_ties_break_on_fingerprint() -> None:
    fs = [_finding("Z001", fingerprint="zz"), _finding("Y001", fingerprint="aa")]
    rows = advise(fs, [], [], [], _graph())
    assert rows[0].check_id == "Y001"


# --- CLI ---------------------------------------------------------------------


def test_cli_advise(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'resource "aws_dynamodb_table" "t" { name = "t" }\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["advise", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Advised actions" in result.output


def test_cli_advise_json_and_top(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'resource "aws_dynamodb_table" "t" { name = "t" }\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["advise", str(tmp_path), "-f", "json", "--top", "1"])
    assert result.exit_code == 0
    rows = json.loads(result.output)
    assert len(rows) == 1
    row = rows[0]
    assert row["score"] == sum(row["breakdown"].values())
    assert row["fingerprints"]
