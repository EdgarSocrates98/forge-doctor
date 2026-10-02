"""Capability Engine tests - status semantics, validation, honesty."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from forge_doctor.core.capabilities import (
    CapabilityContext,
    CapabilityRegistry,
    CapabilityStatus,
    capability_registry,
)


def make_registry(*packs: dict[str, Any]) -> CapabilityRegistry:
    """Registry from synthetic packs keyed by their ``platform`` field."""
    triples = [
        (pack.get("platform", f"pack{i}"), f"test/p{i}", pack) for i, pack in enumerate(packs)
    ]
    return CapabilityRegistry(packs=triples)


PACK = {
    "schema_version": 2,
    "pack_version": "2026.10.01",
    "verified_at": "2026-10-01",
    "platform": "demo",
    "capabilities": [
        {
            "id": "DEMO_FEATURE",
            "status": "supported",
            "reason": "baseline",
            "source": "https://example.com/docs",
        },
        {
            "id": "DEMO_GATED",
            "status": "supported",
            "when": {"variant": "A"},
            "source": "https://example.com/docs",
        },
        {
            "id": "DEMO_GATED",
            "status": "unsupported",
            "when": {"variant": "B"},
            "reason": "variant B lacks it",
            "source": "https://example.com/docs",
        },
        {
            "id": "DEMO_VERSIONED",
            "status": "supported",
            "versions": {"1.0": "unsupported", "2.0": "supported"},
            "source": "https://example.com/docs",
        },
        {
            "id": "DEMO_CONDITIONAL",
            "status": "supported",
            "conditions": [
                {
                    "attribute": "format_version",
                    "op": "gte",
                    "value": "2",
                    "status_on_fail": "unsupported",
                    "reason": "needs format_version>=2",
                }
            ],
            "source": "https://example.com/docs",
        },
    ],
}


def test_supported() -> None:
    reg = make_registry(PACK)
    res = reg.evaluate("DEMO_FEATURE", platform="demo")
    assert res.status is CapabilityStatus.SUPPORTED
    assert res.supported


def test_unknown_capability() -> None:
    reg = make_registry(PACK)
    res = reg.evaluate("NOPE", platform="demo")
    assert res.status is CapabilityStatus.UNKNOWN
    assert not res.supported


def test_unknown_platform() -> None:
    reg = make_registry(PACK)
    assert reg.evaluate("DEMO_FEATURE", platform="nope").status is CapabilityStatus.UNKNOWN


def test_when_gate_matches() -> None:
    reg = make_registry(PACK)
    assert (
        reg.evaluate("DEMO_GATED", platform="demo", variant="A").status
        is CapabilityStatus.SUPPORTED
    )
    res = reg.evaluate("DEMO_GATED", platform="demo", variant="B")
    assert res.status is CapabilityStatus.UNSUPPORTED
    assert res.reason == "variant B lacks it"


def test_when_gate_absent_variant() -> None:
    """Without the gating attribute, specialized entries don't apply."""
    reg = make_registry(PACK)
    assert reg.evaluate("DEMO_GATED", platform="demo").status is CapabilityStatus.UNKNOWN


def test_version_map() -> None:
    reg = make_registry(PACK)
    assert (
        reg.evaluate("DEMO_VERSIONED", platform="demo", version="1.0").status
        is CapabilityStatus.UNSUPPORTED
    )
    assert (
        reg.evaluate("DEMO_VERSIONED", platform="demo", version="2.0").status
        is CapabilityStatus.SUPPORTED
    )


def test_version_outside_map_is_unknown() -> None:
    """absence of proof != unsupported: uncovered versions are UNKNOWN."""
    reg = make_registry(PACK)
    res = reg.evaluate("DEMO_VERSIONED", platform="demo", version="999.0")
    assert res.status is CapabilityStatus.UNKNOWN


def test_version_map_without_version() -> None:
    reg = make_registry(PACK)
    assert reg.evaluate("DEMO_VERSIONED", platform="demo").status is CapabilityStatus.UNKNOWN


def test_conditions_satisfied() -> None:
    reg = make_registry(PACK)
    res = reg.evaluate("DEMO_CONDITIONAL", platform="demo", format_version="3")
    assert res.status is CapabilityStatus.SUPPORTED


def test_conditions_violated() -> None:
    reg = make_registry(PACK)
    res = reg.evaluate("DEMO_CONDITIONAL", platform="demo", format_version="1")
    assert res.status is CapabilityStatus.UNSUPPORTED
    assert "format_version" in res.reason
    assert res.conditions == ("format_version gte 2",)


def test_conditions_attr_absent_is_conditional() -> None:
    reg = make_registry(PACK)
    res = reg.evaluate("DEMO_CONDITIONAL", platform="demo")
    assert res.status is CapabilityStatus.CONDITIONAL


def test_supports_alias() -> None:
    reg = make_registry(PACK)
    res = reg.supports("demo", "DEMO_FEATURE")
    assert res.status is CapabilityStatus.SUPPORTED


def test_limitations_and_capabilities_for() -> None:
    reg = make_registry(PACK)
    caps = reg.capabilities_for("demo")
    assert "DEMO_FEATURE" in caps and "DEMO_VERSIONED" in caps
    assert reg.limitations("demo") == []


def test_context_object() -> None:
    reg = make_registry(PACK)
    ctx = CapabilityContext(platform="demo", version="2.0")
    assert reg.evaluate("DEMO_VERSIONED", ctx).status is CapabilityStatus.SUPPORTED


def test_deterministic() -> None:
    a = make_registry(PACK)
    b = make_registry(PACK)
    assert a.explain("demo", "DEMO_GATED", variant="B") == b.explain(
        "demo", "DEMO_GATED", variant="B"
    )


# -- validation ------------------------------------------------------------


def test_pack_missing_source_rejected() -> None:
    bad = dict(PACK)
    bad["capabilities"] = [{"id": "X", "status": "supported"}]
    reg = make_registry(bad)
    assert any("missing source" in i for i in reg.validation_issues)
    assert reg.evaluate("X", platform="demo").status is CapabilityStatus.UNKNOWN


def test_pack_invalid_schema_rejected() -> None:
    bad = dict(PACK)
    bad["schema_version"] = 1
    reg = make_registry(bad)
    assert reg.validation_issues
    assert reg.evaluate("DEMO_FEATURE", platform="demo").status is CapabilityStatus.UNKNOWN


def test_pack_invalid_status_rejected() -> None:
    bad = dict(PACK)
    bad["capabilities"] = [{"id": "X", "status": "maybe", "source": "https://example.com"}]
    reg = make_registry(bad)
    assert any("invalid status" in i for i in reg.validation_issues)


def test_pack_invalid_versions_rejected() -> None:
    bad = dict(PACK)
    bad["capabilities"] = [
        {
            "id": "X",
            "status": "supported",
            "versions": {"1.0": "maybe"},
            "source": "https://example.com",
        }
    ]
    reg = make_registry(bad)
    assert any("invalid status" in i for i in reg.validation_issues)


def test_duplicate_capability_flagged() -> None:
    bad = dict(PACK)
    bad["capabilities"] = [
        {"id": "X", "status": "supported", "source": "https://example.com"},
        {"id": "X", "status": "unsupported", "source": "https://example.com"},
    ]
    reg = make_registry(bad)
    assert any("duplicate capability X" in i for i in reg.validation_issues)
    # first entry survives deterministically
    assert reg.evaluate("X", platform="demo").status is CapabilityStatus.SUPPORTED


def test_missing_capability_list_issue() -> None:
    reg = make_registry({"schema_version": 2, "capabilities": "nope", "platform": "demo"})
    assert any("not a list" in i for i in reg.validation_issues)


# -- bundled packs ---------------------------------------------------------


def test_bundled_glue_iceberg_versions() -> None:
    reg = capability_registry()
    assert (
        reg.evaluate("ICEBERG_MERGE_WRITE", platform="glue", version="5.0").status
        is not CapabilityStatus.UNKNOWN
    )
    assert (
        reg.evaluate("ICEBERG_MERGE_WRITE", platform="glue", version="999.0").status
        is CapabilityStatus.UNKNOWN
    )
    assert (
        reg.evaluate("ICEBERG_MERGE_WRITE", platform="glue", version="3.0").status
        is CapabilityStatus.UNSUPPORTED
    )


def test_bundled_dynamodb_global_table() -> None:
    reg = capability_registry()
    mrsc = reg.evaluate("DYNAMODB_TRANSACTIONS", platform="dynamodb_global_table", variant="MRSC")
    assert mrsc.status is CapabilityStatus.UNSUPPORTED
    assert mrsc.source
    mrec = reg.evaluate("DYNAMODB_TRANSACTIONS", platform="dynamodb_global_table", variant="MREC")
    assert mrec.status is CapabilityStatus.CONDITIONAL


def test_bundled_neptune_language_paradigm() -> None:
    reg = capability_registry()
    assert (
        reg.evaluate("NEPTUNE_OPENCYPHER", platform="neptune").status is CapabilityStatus.SUPPORTED
    )
    assert (
        reg.evaluate("NEPTUNE_OPENCYPHER", platform="neptune", graph_model="rdf").status
        is CapabilityStatus.UNSUPPORTED
    )


def test_bundled_packs_validate() -> None:
    """Bundled packs pass `knowledge verify` structural checks."""
    from forge_doctor.core.knowledge import verify_pack

    for name in ("glue", "iceberg", "dynamodb", "neptune", "graph"):
        assert verify_pack("capabilities", name) == []


def test_bundled_registry_no_issues() -> None:
    assert capability_registry().validation_issues == ()


# -- migrated check (ICE001 keeps its behavior via the registry) -----------


def test_migrated_check_ice001_unchanged(tmp_path: Path) -> None:
    """FormatVersionCompat emits the same findings through the registry."""
    from forge_doctor.checks.iceberg import FormatVersionCompat
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.models import Severity

    sql = (
        "CREATE TABLE t (id int) USING iceberg TBLPROPERTIES ('format-version'='1');\n"
        "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET *;\n"
    )
    (tmp_path / "q.sql").write_text(sql)
    ctx = ProjectContext(root=tmp_path)
    results = FormatVersionCompat().run(ctx)
    assert results and all(r.severity == Severity.WARNING for r in results)
    assert "format-version=1" in results[0].message

    sql2 = sql.replace("'1'", "'2'")
    (tmp_path / "q.sql").write_text(sql2)
    ctx2 = ProjectContext(root=tmp_path)
    assert all(
        r.severity != Severity.WARNING or "format-version" not in r.message
        for r in FormatVersionCompat().run(ctx2)
    )
