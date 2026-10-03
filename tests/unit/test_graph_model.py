"""GraphProjectModel tests - detection sources and paradigm split."""

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


def test_empty_project(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"a.py": "x = 1\n"})
    model = graph_model(ctx)
    assert not model.has_graph
    assert model.traversals == []


def test_cypher_file_detected(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"q.cypher": "MATCH (p:Person)-[:WORKS_AT]->(c:Company) RETURN p\n"},
    )
    model = graph_model(ctx)
    assert model.has_graph
    assert "opencypher" in model.languages
    assert model.paradigms == {"property_graph"}
    assert model.traversals[0].vertex_labels == ("Person", "Company")
    assert model.traversals[0].edge_labels == ("WORKS_AT",)


def test_rdf_data_file_detected(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"data.ttl": "<a> <b> <c> .\n"})
    model = graph_model(ctx)
    assert any(w.paradigm == "rdf" for w in model.workloads)


def test_sparql_file_parsed(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"q.sparql": "PREFIX foaf: <http://x/>\nSELECT ?n WHERE { ?s foaf:name ?n }\n"},
    )
    model = graph_model(ctx)
    assert "sparql" in model.languages
    assert model.paradigms == {"rdf"}
    assert model.traversals[0].predicates


def test_gremlin_chain_from_callsites(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"job.py": 'g.V().hasLabel("person").out("knows").limit(5)\n'},
    )
    model = graph_model(ctx)
    assert len(model.traversals) == 1
    t = model.traversals[0]
    assert t.language == "gremlin"
    assert t.steps[:3] == ("V", "hasLabel", "out")
    assert t.start_selective
    assert t.vertex_labels == ("person",)
    assert "knows" in t.edge_labels


def test_gremlin_unselective_start(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": "g.V().out('e')\n"})
    t = graph_model(ctx).traversals[0]
    assert not t.start_selective


def test_gremlin_repeat_unbounded(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": 'g.V("1").repeat(__.out("knows")).path()\n'})
    t = graph_model(ctx).traversals[0]
    assert t.has_variable_length
    assert t.hop_bounds == "unbounded"


def test_gremlin_repeat_bounded(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": 'g.V("1").repeat(__.out("knows")).times(3)\n'})
    t = graph_model(ctx).traversals[0]
    assert t.hop_bounds == "3"


def test_query_string_arg_detected(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"job.py": ('client.execute("MATCH (n:Person)-[:KNOWS]->(m) RETURN n")\n')},
    )
    model = graph_model(ctx)
    assert any(t.language == "opencypher" for t in model.traversals)


def test_sparql_string_arg(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"job.py": ('c.query("SELECT ?s WHERE { ?s <pred> ?o }")\n')},
    )
    assert any(t.language == "sparql" for t in graph_model(ctx).traversals)


def test_bulk_csv_vertex(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"nodes.csv": "~id,~label,name\n1,person,a\n"})
    model = graph_model(ctx)
    assert any(w.kind == "bulk_load" for w in model.workloads)
    assert model.workloads[0].detail == "neptune_csv_vertex"


def test_bulk_csv_edge(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"edges.csv": "~id,~from,~to,~label\ne1,1,2,knows\n"})
    model = graph_model(ctx)
    assert model.workloads[0].detail == "neptune_csv_edge"


def test_mixed_paradigms(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "q.cypher": "MATCH (p:Person) RETURN p\n",
            "q.sparql": "SELECT ?s WHERE { ?s a ?o }\n",
        },
    )
    assert graph_model(ctx).paradigms == {"property_graph", "rdf"}


def test_deterministic(tmp_path: Path) -> None:
    files = {"q.cypher": "MATCH (a:X)-[:R]->(b:Y) RETURN a\n"}
    m1 = graph_model(make_context(tmp_path / "a", files))
    m2 = graph_model(make_context(tmp_path / "b", files))
    assert [(t.language, t.steps) for t in m1.traversals] == [
        (t.language, t.steps) for t in m2.traversals
    ]
