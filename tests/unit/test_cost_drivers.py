"""Spec 237 — technical cost-driver intelligence (never billing)."""

from __future__ import annotations

from forge_doctor.core.cost_drivers import (
    CostDriverKind,
    CostPolicy,
    cost_findings,
    detect_transfers,
    driver_kinds_for_engine,
    extract_drivers,
    migration_cost_delta,
)
from forge_doctor.core.execution_model import (
    ExecutionScan,
    ExecutionStage,
    QueryExecution,
    StageKind,
)


def _ex(
    engine: str = "spark",
    execution_id: str = "e1",
    **kw: object,
) -> QueryExecution:
    base: dict[str, object] = {
        "execution_id": execution_id,
        "engine": engine,
        "evidence": ("adapter:test", "artifact:x.json"),
    }
    base.update(kw)
    return QueryExecution(**base)  # type: ignore[arg-type]


def test_per_engine_time_kinds() -> None:
    snow = extract_drivers([_ex("snowflake", duration_ms=1000.0)])
    bq = extract_drivers([_ex("bigquery", cpu_time_ms=500.0)])
    rs = extract_drivers([_ex("redshift", duration_ms=2000.0)])
    spark = extract_drivers([_ex("spark", cpu_time_ms=900.0)])
    assert snow[0].kind is CostDriverKind.WAREHOUSE_UPTIME
    assert bq[0].kind is CostDriverKind.SLOT_USAGE and bq[0].unit == "slot_ms"
    assert rs[0].kind is CostDriverKind.RPU_USAGE
    assert spark[0].kind is CostDriverKind.EXECUTOR_TIME


def test_absent_metrics_emit_no_driver() -> None:
    out = extract_drivers([_ex("spark")])
    assert out == []


def test_scan_shuffle_spill_drivers() -> None:
    ex = _ex(
        "spark",
        bytes_read=5e9,
        inputs=("events",),
        stages=(
            ExecutionStage(
                id="s1",
                kind=StageKind.EXCHANGE,
                shuffle_bytes=2e9,
                spill_bytes=4e8,
            ),
        ),
    )
    kinds = {d.kind for d in extract_drivers([ex])}
    assert CostDriverKind.SCAN_VOLUME in kinds
    assert CostDriverKind.SHUFFLE_VOLUME in kinds
    assert CostDriverKind.SPILL_IO in kinds


def test_team_attribution_requires_ownership_evidence() -> None:
    from forge_doctor.core.platform_graph import (
        DataPlatformGraph,
        Entity,
        EntityKind,
    )

    g = DataPlatformGraph()
    g.add_entity(Entity(kind=EntityKind.TABLE, domain="aws", identifier="events", name="events"))
    ex = _ex("spark", bytes_read=1e9, inputs=("events",))
    drivers = extract_drivers([ex], g)
    assert all(d.team == "" for d in drivers)

    g.add_entity(
        Entity(
            kind=EntityKind.TABLE,
            domain="aws",
            identifier="orders",
            name="orders",
            attrs=(("owner", "payments"), ("env", "prod")),
        )
    )
    ex2 = _ex("spark", bytes_read=1e9, inputs=("orders",))
    drivers2 = extract_drivers([ex2], g)
    assert any(d.team == "payments" and d.environment == "prod" for d in drivers2)


def test_cost001_idle_compute() -> None:
    ex = _ex("spark", duration_ms=5000.0)
    findings = cost_findings([], [], None, [ex])
    assert any(f.check_id == "COST001" for f in findings)


def test_cost002_repeated_scan_volume() -> None:
    exs = [_ex("spark", f"e{i}", bytes_read=2e10, inputs=("events",)) for i in range(3)]
    drivers = extract_drivers(exs)
    findings = cost_findings(drivers, [], CostPolicy.defaults(), exs)
    f = [x for x in findings if x.check_id == "COST002"]
    assert f and "events" in f[0].message
    # below bound -> no warning
    small = [_ex("spark", f"s{i}", bytes_read=100.0, inputs=("dim",)) for i in range(3)]
    drivers2 = extract_drivers(small)
    f2 = [
        x
        for x in cost_findings(drivers2, [], CostPolicy.defaults(), small)
        if x.check_id == "COST002" and x.severity.value == "warning"
    ]
    assert not f2


def test_cost003_cross_cloud_transfer() -> None:
    from forge_doctor.core.platform_graph import (
        DataPlatformGraph,
        Entity,
        EntityKind,
    )

    g = DataPlatformGraph()
    g.add_entity(Entity(kind=EntityKind.TABLE, domain="aws", identifier="events"))
    ex = _ex("bigquery", bytes_read=1e9, inputs=("events",))
    transfers = detect_transfers([ex], g)
    assert len(transfers) == 1
    assert transfers[0].source_cloud == "aws" and transfers[0].target_cloud == "gcp"
    findings = cost_findings([], transfers)
    assert any(f.check_id == "COST003" for f in findings)


def test_no_transfer_when_cloud_unknown() -> None:
    from forge_doctor.core.platform_graph import (
        DataPlatformGraph,
        Entity,
        EntityKind,
    )

    g = DataPlatformGraph()
    g.add_entity(Entity(kind=EntityKind.TABLE, domain="aws", identifier="events"))
    ex = _ex("snowflake", bytes_read=1e9, inputs=("events",))
    assert detect_transfers([ex], g) == []


def test_cost004_replication() -> None:
    from forge_doctor.core.platform_graph import (
        DataPlatformGraph,
        Entity,
        EntityKind,
    )

    g = DataPlatformGraph()
    g.add_entity(
        Entity(
            kind=EntityKind.TABLE,
            domain="gcp",
            identifier="idx",
            attrs=(("replicas", "5"),),
        )
    )
    drivers = extract_drivers([], g)
    findings = cost_findings(drivers, [], CostPolicy.defaults())
    f = [x for x in findings if x.check_id == "COST004"]
    assert f and f[0].severity.value == "warning"


def test_cost005_materialization_dup() -> None:
    exs = [_ex("spark", f"m{i}", outputs=("mart",)) for i in range(4)]
    findings = cost_findings([], [], CostPolicy.defaults(), exs)
    assert any(f.check_id == "COST005" and "mart" in f.message for f in findings)


def test_cost006_shuffle_driver() -> None:
    ex = _ex(
        "spark",
        stages=(ExecutionStage(id="s", kind=StageKind.EXCHANGE, shuffle_bytes=3e9),),
    )
    drivers = extract_drivers([ex])
    findings = cost_findings(drivers, [], CostPolicy.defaults())
    f = [x for x in findings if x.check_id == "COST006" and x.severity.value == "warning"]
    assert f


def test_cost007_tiny_files_needs_file_count() -> None:
    scan = ExecutionScan(source="events", bytes_scanned=1e6, files_scanned=200.0)
    ex = _ex(
        "spark",
        bytes_read=1e6,
        stages=(ExecutionStage(id="s", kind=StageKind.SCAN, scans=(scan,)),),
    )
    findings = cost_findings([], [], CostPolicy.defaults(), [ex])
    assert any(f.check_id == "COST007" for f in findings)
    # no file count -> no finding
    scan2 = ExecutionScan(source="events", bytes_scanned=1e6)
    ex2 = _ex(
        "spark",
        bytes_read=1e6,
        stages=(ExecutionStage(id="s", kind=StageKind.SCAN, scans=(scan2,)),),
    )
    findings2 = cost_findings([], [], CostPolicy.defaults(), [ex2])
    assert not any(f.check_id == "COST007" for f in findings2)


def test_migration_cost_delta_kinds_only() -> None:
    delta = migration_cost_delta("snowflake", "bigquery")
    assert "warehouse_uptime" in delta["lost"]
    assert "slot_usage" in delta["gained"]
    assert "cheaper" not in delta["note"]
    assert delta["shared"]


def test_determinism_and_serialization() -> None:
    ex = _ex("spark", bytes_read=1e9, duration_ms=100.0, inputs=("t",))
    a = [d.to_dict() for d in extract_drivers([ex])]
    b = [d.to_dict() for d in extract_drivers([ex])]
    assert a == b


def test_pack_drives_engine_kinds() -> None:
    kinds = driver_kinds_for_engine("opensearch")
    assert CostDriverKind.INDEX_STORAGE in kinds
    assert driver_kinds_for_engine("nonexistent") == set()
