"""Unit tests for the GRAPH### checks."""

from __future__ import annotations

from pathlib import Path

from forge_doctor_data.checks.graph import (
    CHECKS,
    DirectionInconsistency,
    DisconnectedComponents,
    FullGraphStarts,
    GenericRelationship,
    GraphUsage,
    HighFanout,
    LateFiltering,
    MixedParadigms,
    OrphanVertex,
    PropertyFanout,
    RedundantRelationship,
    RelationalShape,
    RepeatedTraversal,
    SupernodePattern,
    UnboundedResult,
    UndefinedVertexType,
    UnselectiveStart,
    VariableLengthUnbounded,
)
from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.models import Severity


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


def test_anchor_reports_usage(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"q.cypher": "MATCH (p:Person)-[:KNOWS]->(f) RETURN p\n"})
    results = GraphUsage().run(ctx)
    assert results[0].severity == Severity.INFO
    assert "opencypher" in results[0].message


def test_anchor_passes_clean(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"a.py": "x = 1\n"})
    assert GraphUsage().run(ctx)[0].severity == Severity.PASS


def test_disconnected_components(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "a.cypher": "MATCH (a:Alpha)-[:X]->(b:Beta) RETURN a\n",
            "b.cypher": "MATCH (c:Gamma)-[:Y]->(d:Delta) RETURN c\n",
        },
    )
    results = DisconnectedComponents().run(ctx)
    assert results and "disconnected" in results[0].message


def test_connected_components_clean(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "a.cypher": (
                "MATCH (a:Alpha)-[:X]->(b:Beta) RETURN a\nMATCH (b:Beta)-[:Y]->(c:Gamma) RETURN b\n"
            ),
        },
    )
    assert DisconnectedComponents().run(ctx) == []


def test_mixed_paradigms_project_level(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "a.cypher": "MATCH (p:P) RETURN p\n",
            "b.sparql": "SELECT ?s WHERE { ?s a ?o }\n",
        },
    )
    results = MixedParadigms().run(ctx)
    assert results and results[0].severity == Severity.INFO


def test_mixed_paradigms_same_file(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"q.py": ('c.run("MATCH (p:P) RETURN p")\nc.query("SELECT ?s WHERE { ?s a ?o }")\n')},
    )
    results = MixedParadigms().run(ctx)
    assert results and results[0].severity == Severity.WARNING


def test_unselective_start_warns(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": "g.V().out('knows').values('n')\n"})
    results = UnselectiveStart().run(ctx)
    assert results and results[0].severity == Severity.WARNING
    assert results[0].confidence.value == "medium"


def test_unbounded_result_warns(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": 'g.V().hasLabel("p").out("k")\n'})
    results = UnboundedResult().run(ctx)
    assert results and results[0].severity == Severity.INFO


def test_bounded_result_clean(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": 'g.V().hasLabel("p").out("k").limit(10)\n'})
    assert UnboundedResult().run(ctx) == []


def test_cypher_limit_bounded(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"q.cypher": "MATCH (a:P)-[:K]->(b:P) RETURN a LIMIT 5\n"},
    )
    assert UnboundedResult().run(ctx) == []


def test_unbounded_variable_length_cypher(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"q.cypher": "MATCH (a:P)-[:KNOWS*]->(b:P) RETURN a\n"},
    )
    results = VariableLengthUnbounded().run(ctx)
    assert results and results[0].severity == Severity.WARNING


def test_bounded_variable_length_no_flag(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"q.cypher": "MATCH (a:P)-[:KNOWS*1..3]->(b:P) RETURN a\n"},
    )
    assert VariableLengthUnbounded().run(ctx) == []


def test_gremlin_repeat_unbounded(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": 'g.V("1").repeat(__.out("k")).path()\n'})
    results = VariableLengthUnbounded().run(ctx)
    assert results and results[0].severity == Severity.WARNING


def test_gremlin_repeat_bounded_clean(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": 'g.V("1").repeat(__.out("k")).times(3)\n'})
    assert VariableLengthUnbounded().run(ctx) == []


def test_late_filtering(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"job.py": 'g.V().out("a").out("b").out("c").has("x", 1)\n'},
    )
    results = LateFiltering().run(ctx)
    assert results and results[0].severity == Severity.INFO


def test_high_fanout(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"q.cypher": "MATCH (a:P)-[:X]->(b:Q)-[:Y]->(c:R)-[:Z]->(d:S) RETURN a\n"},
    )
    results = HighFanout().run(ctx)
    assert results and results[0].severity == Severity.INFO


def test_repeated_traversal(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"job.py": ('g.V().hasLabel("p").out("k")\nx = 1\ng.V().hasLabel("p").out("k")\n')},
    )
    results = RepeatedTraversal().run(ctx)
    assert results and results[0].severity == Severity.INFO


def test_repeated_traversal_single_clean(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": 'g.V().hasLabel("p").out("k")\n'})
    assert RepeatedTraversal().run(ctx) == []


def test_full_graph_starts_threshold(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"job.py": ("g.V().out('a')\ng.V().out('b')\ng.V().out('c')\n")},
    )
    assert FullGraphStarts().run(ctx)
    assert len(FullGraphStarts().run(ctx)) == 1


def test_generic_relationship(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": 'g.V().hasLabel("p").out("RELATED")\n'})
    assert GenericRelationship().run(ctx)


def test_direction_inconsistency(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"job.py": ('g.V().hasLabel("a").out("KNOWS")\ng.V().hasLabel("b").in_("KNOWS")\n')},
    )
    results = DirectionInconsistency().run(ctx)
    assert results and "KNOWS" in results[0].message


def test_orphan_vertex(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"job.py": 'g.V().hasLabel("person").out("knows")\ng.addV("lonely")\n'},
    )
    results = OrphanVertex().run(ctx)
    assert results and "lonely" in results[0].message


def test_redundant_relationship(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"job.py": 'g.V().hasLabel("member").out("member")\n'},
    )
    assert RedundantRelationship().run(ctx)


def test_property_fanout(tmp_path: Path) -> None:
    props = ".".join(f'has("p{i}", 1)' for i in range(9))
    ctx = make_context(tmp_path, {"job.py": f'g.V().hasLabel("p").{props}\n'})
    results = PropertyFanout().run(ctx)
    assert results and results[0].severity == Severity.INFO


def test_property_fanout_clean(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": 'g.V().hasLabel("p").has("a", 1)\n'})
    assert PropertyFanout().run(ctx) == []


def test_supernode_pattern(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"job.py": 'g.V().hasLabel("user").out("a").out("b").out("c").out("d").out("e")\n'},
    )
    assert SupernodePattern().run(ctx)


def test_relational_shape(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"nodes.csv": "~id,~label,name\n1,p,a\n"})
    results = RelationalShape().run(ctx)
    assert results and results[0].severity == Severity.INFO


def test_undefined_vertex_type(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"q.cypher": "MATCH (a:Person)-[:KNOWS]->(b) RETURN a\n"},
    )
    results = UndefinedVertexType().run(ctx)
    assert results and "KNOWS" in results[0].message


def test_defined_vertex_types_clean(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"q.cypher": "MATCH (a:Person)-[:KNOWS]->(b:Person) RETURN a\n"},
    )
    assert UndefinedVertexType().run(ctx) == []


def test_registry_collects_all() -> None:
    ids = {c.id for c in CHECKS}
    assert {
        "GRAPH001",
        "GRAPH002",
        "GRAPH003",
        "GRAPH004",
        "GRAPH005",
        "GRAPH006",
        "GRAPH007",
        "GRAPH008",
        "GRAPH009",
        "GRAPH010",
        "GRAPH020",
        "GRAPH021",
        "GRAPH022",
        "GRAPH023",
        "GRAPH024",
        "GRAPH025",
    } <= ids


def test_no_error_severity(tmp_path: Path) -> None:
    """Heuristic graph checks never claim certainty (no ERROR)."""
    ctx = make_context(
        tmp_path,
        {
            "job.py": "g.V().repeat(__.out('k')).path()\n",
            "q.cypher": "MATCH (a)-[:R*]->(b) RETURN a\n",
        },
    )
    for check in CHECKS:
        assert all(r.severity != Severity.ERROR for r in check.run(ctx))
