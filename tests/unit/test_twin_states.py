"""Five-state digital twin: facts, reconciliation, drift, temporal diff (spec 232)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from forge_doctor.cli.app import app
from forge_doctor.core.models import EvidenceKind
from forge_doctor.core.platform_graph import DataPlatformGraph, Entity, EntityKind
from forge_doctor.core.twin_states import (
    DriftType,
    TwinFact,
    TwinState,
    collect_twin_facts,
    diff_twin_snapshots,
    hypothetical_facts,
    load_twin_snapshot,
    reconcile,
    record_twin_snapshot,
    twin_state_snapshot,
)
from forge_doctor.core.whatif import WhatIfChange, WhatIfImpact, WhatIfReport

runner = CliRunner()


def _fact(entity, prop, value, state, source="test"):
    return TwinFact(entity, prop, value, state, EvidenceKind.STATIC, source)


def _graph() -> DataPlatformGraph:
    g = DataPlatformGraph()
    g.add_entity(
        Entity(
            kind=EntityKind.COMPUTE_JOB,
            domain="terraform",
            identifier="etl",
            attrs=(("version", "4.0"), ("engine", "glue")),
        )
    )
    g.add_entity(
        Entity(
            kind=EntityKind.COMPUTE_JOB,
            domain="glue",
            identifier="etl",
            attrs=(("version", "5.0"),),
        )
    )
    return g


# --- states -------------------------------------------------------------------


def test_five_states_exist() -> None:
    assert {s.value for s in TwinState} == {
        "desired",
        "declared",
        "implemented",
        "observed",
        "hypothetical",
    }


def test_eight_drift_types_exist() -> None:
    assert len(DriftType) == 8


def test_graph_facts_tagged_by_domain() -> None:
    facts = collect_twin_facts(_graph())
    by_state = {(f.entity, f.state) for f in facts}
    assert ("etl", TwinState.DECLARED) in by_state  # terraform
    assert ("etl", TwinState.IMPLEMENTED) in by_state  # glue analyzer


def test_state_conflict_detected() -> None:
    facts = [
        _fact("etl", "version", "5.0", TwinState.DECLARED),
        _fact("etl", "version", "4.0", TwinState.IMPLEMENTED),
    ]
    recs = reconcile(facts)
    assert len(recs) == 1
    r = recs[0]
    assert r.states_compared == (TwinState.DECLARED, TwinState.IMPLEMENTED)
    assert r.drift_type is DriftType.IMPLEMENTATION_DRIFT
    assert r.expected == "5.0" and r.actual == "4.0"


def test_state_agreement_no_drift() -> None:
    facts = [
        _fact("etl", "version", "5.0", TwinState.DECLARED),
        _fact("etl", "version", "5.0", TwinState.IMPLEMENTED),
    ]
    assert reconcile(facts) == ()


def test_missing_observed_state_no_fabrication() -> None:
    facts = [_fact("etl", "version", "5.0", TwinState.IMPLEMENTED)]
    recs = reconcile(facts)
    assert recs == ()  # single state, nothing compared, nothing invented


def test_hypothetical_state_isolated() -> None:
    report = WhatIfReport(
        change=WhatIfChange("glue", "glue_version", "4.0", "5.0"),
        impacts=(WhatIfImpact("enabler", "info", "unlocks connectors"),),
    )
    hypo = hypothetical_facts(report)
    assert all(f.state is TwinState.HYPOTHETICAL for f in hypo)
    base = [_fact("glue", "glue_version", "4.0", TwinState.IMPLEMENTED)]
    # hypothetical merges for comparison but nothing mutates the base facts
    recs = reconcile([*base, *hypo])
    assert any(TwinState.HYPOTHETICAL in r.states_compared for r in recs)
    assert all(f.state is not TwinState.HYPOTHETICAL for f in base)


def test_ownership_drift_classification() -> None:
    facts = [
        _fact("t", "owner", "team-a", TwinState.DECLARED),
        _fact("t", "owner", "team-b", TwinState.IMPLEMENTED),
    ]
    recs = reconcile(facts)
    assert recs[0].drift_type is DriftType.OWNERSHIP_DRIFT


def test_schema_drift_classification() -> None:
    facts = [
        _fact("orders", "column.customer_id", "string", TwinState.DESIRED),
        _fact("orders", "column.customer_id", "bigint", TwinState.IMPLEMENTED),
    ]
    recs = reconcile(facts)
    assert recs[0].drift_type is DriftType.SCHEMA_DRIFT


def test_reconciliation_fields() -> None:
    facts = [
        _fact("x", "encryption", "true", TwinState.DECLARED),
        _fact("x", "encryption", "false", TwinState.IMPLEMENTED),
    ]
    (r,) = reconcile(facts)
    assert r.drift_type is DriftType.SECURITY_DRIFT
    assert r.difference
    assert r.confidence in {"declared", "inferred", "observed"}
    assert r.affected_entities == ("x",)


# --- snapshots + temporal diff ----------------------------------------------------


def test_twin_snapshot_and_diff() -> None:
    g = _graph()
    facts = collect_twin_facts(g)
    snap_a = twin_state_snapshot(g, facts, "2026-01-01T00:00:00Z")
    # newer snapshot: same graph + a drift fact
    facts_b = (
        *tuple(facts),
        TwinFact("etl", "sla_seconds", "300", TwinState.DESIRED, source="contract"),
        TwinFact("etl", "sla_seconds", "600", TwinState.OBSERVED, source="runtime"),
    )
    snap_b = twin_state_snapshot(g, facts_b, "2026-01-02T00:00:00Z")
    diff = diff_twin_snapshots(snap_a, snap_b)
    assert diff.entities_added == () and diff.entities_removed == ()
    assert diff.drift_introduced, "sla divergence should appear as new drift"
    assert diff.drift_resolved == ()
    assert json.dumps(diff.to_dict())


def test_diff_entity_changes() -> None:
    g = _graph()
    snap_a = twin_state_snapshot(g, (), "t1")
    g2 = _graph()
    g2.add_entity(Entity(kind=EntityKind.TABLE, domain="dynamodb", identifier="t"))
    snap_b = twin_state_snapshot(g2, (), "t2")
    diff = diff_twin_snapshots(snap_a, snap_b)
    assert "table:dynamodb:t" in diff.entities_added


def test_record_and_load_roundtrip(tmp_path: Path) -> None:
    snap = twin_state_snapshot(_graph(), (), "snap1")
    path = record_twin_snapshot(tmp_path, snap, "snap1")
    assert path.is_file()
    loaded = load_twin_snapshot(tmp_path, "snap1")
    assert loaded["format"] == "forge-doctor/twin-state@1"
    assert loaded["entities"] == snap["entities"]


# --- CLI -------------------------------------------------------------------------


def test_cli_twin_facts(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'resource "aws_dynamodb_table" "t" { name = "t" }\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["twin", "facts", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert any(r["state"] == "declared" for r in rows)


def test_cli_twin_reconcile(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'resource "aws_dynamodb_table" "t" { name = "t" }\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["twin", "reconcile", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "reconciliations" in data and "drift_summary" in data


def test_cli_twin_explain(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'resource "aws_dynamodb_table" "t" { name = "orders" }\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["twin", "facts", str(tmp_path), "--json"])
    rows = json.loads(result.output)
    assert rows, "expected terraform-declared facts"
    entity = rows[0]["entity"]
    result = runner.invoke(app, ["twin", "explain", entity, str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "declared" in result.output


def test_cli_twin_explain_unknown(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'resource "aws_dynamodb_table" "t" { name = "t" }\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["twin", "explain", "nope", str(tmp_path)])
    assert result.exit_code == 1


def test_cli_twin_record_and_diff(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'resource "aws_dynamodb_table" "t" { name = "t" }\n', encoding="utf-8"
    )
    r1 = runner.invoke(app, ["twin", "record", str(tmp_path), "--name", "s1"])
    assert r1.exit_code == 0, r1.output
    r2 = runner.invoke(app, ["twin", "record", str(tmp_path), "--name", "s2"])
    assert r2.exit_code == 0
    result = runner.invoke(app, ["twin", "diff", "s1", "s2", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "entities_added" in data


def test_deterministic_reconcile() -> None:
    facts = [
        _fact("a", "p", "1", TwinState.DESIRED),
        _fact("a", "p", "2", TwinState.IMPLEMENTED),
        _fact("b", "q", "x", TwinState.DECLARED),
        _fact("b", "q", "y", TwinState.OBSERVED),
    ]
    assert reconcile(facts) == reconcile(list(reversed(facts)))
