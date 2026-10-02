"""Adversarial Definition of Done for the graph model (spec 174/175).

False positives, false negatives, malformed input, comments/strings,
aliased clients, multi-language files, dynamic queries.
"""

from __future__ import annotations

from pathlib import Path

from forge_doctor.analyzers.graph_model import graph_model
from forge_doctor.checks.graph import CHECKS
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.models import Severity


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


def run_all(ctx: ProjectContext) -> list:
    out = []
    for check in CHECKS:
        out.extend(check.run(ctx))
    return out


def test_comment_word_not_detected(tmp_path: Path) -> None:
    """'graph'/'gremlin' in a comment must not create graph evidence."""
    ctx = make_context(
        tmp_path,
        {"a.py": "# this job is not about graph or gremlin or cypher\nx = 1\n"},
    )
    assert not graph_model(ctx).has_graph
    assert all(r.severity == Severity.PASS or r.severity == Severity.INFO for r in run_all(ctx))


def test_plain_string_arg_not_detected(tmp_path: Path) -> None:
    """Arbitrary strings passed to calls are not queries."""
    ctx = make_context(tmp_path, {"a.py": 'log.info("matching rows in table")\n'})
    assert graph_model(ctx).traversals == []


def test_sql_string_not_sparql(tmp_path: Path) -> None:
    """SQL SELECT must not be mistaken for SPARQL."""
    ctx = make_context(tmp_path, {"a.py": 'cursor.execute("SELECT * FROM t WHERE id = 1")\n'})
    assert all(t.language != "sparql" for t in graph_model(ctx).traversals)


def test_non_traversal_V_call_ignored(tmp_path: Path) -> None:
    """``something.V()`` with no traversal vocabulary is not a traversal."""
    ctx = make_context(tmp_path, {"a.py": "result = version.V()\n"})
    assert graph_model(ctx).traversals == []


def test_aliased_client_detected(tmp_path: Path) -> None:
    """An aliased traversal root with real steps is still detected."""
    ctx = make_context(
        tmp_path,
        {"a.py": 'trav.V().hasLabel("p").out("knows").has("w", 1)\n'},
    )
    ts = graph_model(ctx).traversals
    assert ts and ts[0].language == "gremlin"


def test_malformed_cypher_unparsed(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"bad.cypher": "MATCH ((( incomplete\n"})
    ts = graph_model(ctx).traversals
    # either nothing detected or an honest unparsed marker - never a crash
    assert all((not t.parsed) or t.language for t in ts)


def test_dynamic_query_unresolved(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"a.py": 'q = f"MATCH (n:{label}) RETURN n"\nrun(q)\n'},
    )
    assert graph_model(ctx).traversals == []


def test_multiple_languages_one_file(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "a.py": (
                'cypher.run("MATCH (p:P)-[:R]->(q:Q) RETURN p")\n'
                'sparql.query("SELECT ?s WHERE { ?s <p> ?o }")\n'
            )
        },
    )
    langs = {t.language for t in graph_model(ctx).traversals}
    assert langs == {"opencypher", "sparql"}


def test_false_negative_known_chain(tmp_path: Path) -> None:
    """A canonical g.V()...out() chain must be detected (no silent miss)."""
    ctx = make_context(tmp_path, {"a.py": 'g.V().hasLabel("a").out("b").limit(1)\n'})
    assert any(t.language == "gremlin" for t in graph_model(ctx).traversals)


def test_no_error_severity_anywhere(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "a.py": "g.V().repeat(__.out('k')).path()\n",
            "b.cypher": "MATCH (a)-[*]->(b) RETURN *\n",
        },
    )
    assert all(r.severity != Severity.ERROR for r in run_all(ctx))


def test_gremlin_string_submission(tmp_path: Path) -> None:
    """`conn.submit("g.V()...")` script strings are parsed as Gremlin."""
    ctx = make_context(
        tmp_path,
        {"a.py": 'conn.submit("g.V().hasLabel(\\"p\\").out(\\"e\\")")\n'},
    )
    ts = [t for t in graph_model(ctx).traversals if t.language == "gremlin"]
    assert ts and "out" in ts[0].steps
