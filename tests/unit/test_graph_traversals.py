"""Traversal-shape extraction per language (spec 175 fixtures)."""

from __future__ import annotations

from pathlib import Path

from forge_doctor_data.analyzers.graph_model import graph_model
from forge_doctor_data.core.context import ProjectContext


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


# --- Gremlin ---------------------------------------------------------------


def test_gremlin_traversal_shape(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "job.py": (
                'g.V().hasLabel("person").has("name", "alice")'
                '.out("knows").out("likes").valueMap("title")\n'
            )
        },
    )
    t = graph_model(ctx).traversals[0]
    assert t.language == "gremlin"
    assert t.steps == ("V", "hasLabel", "has", "out", "out", "valueMap")
    assert t.start_selective
    assert t.directions == ("out",)
    assert set(t.edge_labels) == {"knows", "likes"}
    assert t.first_filter_step == 1


def test_gremlin_writes_flagged(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": 'g.addV("person").property("name", "x")\n'})
    t = graph_model(ctx).traversals[0]
    assert t.writes
    assert "person" in t.vertex_labels


def test_gremlin_script_file(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"s.gremlin": 'g.V().hasLabel("a").out("b")\n'})
    ts = [t for t in graph_model(ctx).traversals if t.language == "gremlin"]
    assert ts and ts[0].steps[:3] == ("V", "hasLabel", "out")


# --- openCypher ------------------------------------------------------------


def test_cypher_shape(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "q.cypher": (
                "MATCH (p:Person {name: 'a'})-[:KNOWS*1..4]->(f:Person) "
                "WHERE p.age > 30 RETURN p, f\n"
            )
        },
    )
    t = graph_model(ctx).traversals[0]
    assert t.language == "opencypher"
    assert t.start_selective
    assert t.has_variable_length
    assert t.hop_bounds == "1..4"
    assert t.projection_size == 2
    assert set(t.vertex_labels) == {"Person"}
    assert t.edge_labels == ("KNOWS",)


def test_cypher_return_star(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"q.cypher": "MATCH (n)-[r]->(m) RETURN *\n"})
    t = graph_model(ctx).traversals[0]
    assert t.projection_star


def test_cypher_writes(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"q.cypher": "CREATE (p:Person {name: 'x'})\n"},
    )
    t = graph_model(ctx).traversals[0]
    assert t.writes
    assert "Person" in t.vertex_labels


def test_cypher_direction_in(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"q.cypher": "MATCH (a:X)<-[:FOLLOWS]-(b:Y) RETURN a\n"},
    )
    t = graph_model(ctx).traversals[0]
    assert "in" in t.directions


# --- SPARQL ----------------------------------------------------------------


def test_sparql_shape(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "q.sparql": (
                "PREFIX ex: <http://ex/>\n"
                "SELECT ?name WHERE {\n"
                "  ?s ex:name ?name .\n"
                "  ?s ex:knows/ex:knows ?o .\n"
                "  FILTER(?name != '')\n"
                "}\n"
            )
        },
    )
    t = graph_model(ctx).traversals[0]
    assert t.language == "sparql"
    assert t.parsed
    assert "ex:name" in t.predicates
    assert t.filters
    assert t.projection_size == 1


def test_sparql_insert_is_write(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"q.sparql": "INSERT DATA { <a> <b> <c> }\n"},
    )
    t = graph_model(ctx).traversals[0]
    assert t.writes


def test_sparql_star_projection(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"q.sparql": "SELECT * WHERE { ?s ?p ?o }\n"})
    assert graph_model(ctx).traversals[0].projection_star


# --- honesty ----------------------------------------------------------------


def test_dynamic_query_string_unresolved(tmp_path: Path) -> None:
    """A non-literal query (variable/f-string) is honestly absent."""
    ctx = make_context(
        tmp_path,
        {"job.py": "q = build_query()\nclient.execute(q)\n"},
    )
    assert graph_model(ctx).traversals == []
