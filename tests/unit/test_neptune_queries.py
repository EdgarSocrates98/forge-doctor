"""Unit tests for Neptune query analyzers + explain artifacts (spec 179)."""

from __future__ import annotations

import json
from pathlib import Path

from forge_doctor.analyzers.neptune_explain import analyze_explain
from forge_doctor.analyzers.neptune_queries import (
    GremlinAnalyzer,
    OpenCypherAnalyzer,
    SPARQLAnalyzer,
    neptune_queries,
)
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.models import EvidenceKind


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


# --- per-language analyzers -------------------------------------------------


def test_gremlin_analyzer(tmp_path: Path) -> None:
    out = GremlinAnalyzer().analyze_text(
        'g.V().hasLabel("Person").out("knows").limit(5)', Path("a.gremlin")
    )
    assert len(out) == 1
    q = out[0]
    assert q.language == "gremlin"
    assert q.start_selective is True
    assert q.hop_count == 1
    assert q.result_bounded is True


def test_opencypher_analyzer(tmp_path: Path) -> None:
    out = OpenCypherAnalyzer().analyze_text(
        "MATCH (a:Person)-[:KNOWS]->(b) RETURN a,b LIMIT 10", Path("q.cypher")
    )
    assert len(out) == 1
    q = out[0]
    assert q.language == "opencypher"
    assert q.hop_count == 1
    assert q.result_bounded is True


def test_opencypher_analyzer_multistatement(tmp_path: Path) -> None:
    out = OpenCypherAnalyzer().analyze_text(
        "MATCH (a:Person) RETURN a; MATCH (b:City) RETURN b", Path("q.cypher")
    )
    assert len(out) == 2


def test_sparql_analyzer(tmp_path: Path) -> None:
    out = SPARQLAnalyzer().analyze_text(
        "SELECT ?s WHERE { ?s <knows> ?o } LIMIT 1", Path("q.sparql")
    )
    assert len(out) == 1
    q = out[0]
    assert q.language == "sparql"
    assert "knows" in q.predicates


def test_sparql_select_star_not_variable_length(tmp_path: Path) -> None:
    out = SPARQLAnalyzer().analyze_text("SELECT * WHERE { ?s ?p ?o }", Path("q.sparql"))
    assert out[0].has_variable_length is False


def test_sparql_property_path_variable_length(tmp_path: Path) -> None:
    out = SPARQLAnalyzer().analyze_text("SELECT ?x WHERE { ?s <knows>* ?x }", Path("q.sparql"))
    assert out[0].has_variable_length is True


def test_garbage_does_not_parse(tmp_path: Path) -> None:
    assert GremlinAnalyzer().analyze_text("not a traversal", Path("a.gremlin")) == []
    assert OpenCypherAnalyzer().analyze_text("hello world", Path("q.cypher")) == []
    assert SPARQLAnalyzer().analyze_text("hello", Path("q.rq")) == []


# --- project-level collection ------------------------------------------------


def test_report_collects_files_and_code(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "q.cypher": "MATCH (a:P) RETURN a\n",
            "r.sparql": "SELECT ?s WHERE { ?s ?p ?o } LIMIT 5\n",
            "app.py": 'g.V().out("knows")\n',
        },
    )
    report = neptune_queries(ctx)
    assert report.languages == {"opencypher", "sparql", "gremlin"}
    assert all(q.parsed for q in report.queries)


def test_report_memoized(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"q.cypher": "MATCH (a:P) RETURN a\n"})
    assert neptune_queries(ctx) is neptune_queries(ctx)


# --- explain/profile artifacts -----------------------------------------------


def test_explain_static_text(tmp_path: Path) -> None:
    f = tmp_path / "explain.txt"
    f.write_text(
        "Final Traversal\n  NeptuneGraphStep(vertex,[])\n  NeptuneTraverserConverterStep\n"
    )
    rep = analyze_explain(f)
    assert rep.parsed
    assert rep.evidence_kind == EvidenceKind.STATIC
    assert rep.language == "gremlin"


def test_explain_runtime_json(tmp_path: Path) -> None:
    f = tmp_path / "profile.json"
    f.write_text(
        json.dumps(
            {
                "metrics": [{"name": "NeptuneGraphStep", "dur": 120, "count": 5}],
                "pipeline": [{"name": "TraversalFilterStep"}],
            }
        )
    )
    rep = analyze_explain(f)
    assert rep.evidence_kind == EvidenceKind.RUNTIME
    assert rep.max_cardinality == 5


def test_explain_observed_metadata(tmp_path: Path) -> None:
    f = tmp_path / "plan.json"
    f.write_text(json.dumps({"steps": [{"name": "DFEScanStep"}]}))
    rep = analyze_explain(f)
    assert rep.evidence_kind == EvidenceKind.OBSERVED_METADATA


def test_explain_large_intermediate_flag(tmp_path: Path) -> None:
    f = tmp_path / "profile.json"
    f.write_text(json.dumps({"steps": [{"name": "Expand", "cardinality": 99999}]}))
    rep = analyze_explain(f)
    assert any(fl.kind == "large_intermediate" for fl in rep.flags)


def test_explain_broad_start_flag(tmp_path: Path) -> None:
    f = tmp_path / "explain.txt"
    f.write_text("Final Traversal\n  NeptuneGraphStep(vertex,[])  g.V()\n  out()\n")
    rep = analyze_explain(f)
    assert any(fl.kind == "broad_start" for fl in rep.flags)
