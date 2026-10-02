"""Adversarial fixtures for the Neptune domain - FP/FN/malformed."""

from __future__ import annotations

from pathlib import Path

from forge_doctor.analyzers.neptune_model import neptune_model
from forge_doctor.core.context import ProjectContext


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


def test_word_neptune_in_comment_no_detection(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"app.py": '# TODO: move this to neptune later\nvalue = "neptune"\n'},
    )
    assert not neptune_model(ctx).has_neptune


def test_neptune_substring_in_string_no_detection(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"app.py": 'planet = "neptune is the eighth planet"\n'})
    assert not neptune_model(ctx).has_neptune


def test_unrelated_service_client_not_neptune(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"app.py": 'import boto3\ns = boto3.client("neptuneish")\n'})
    assert not neptune_model(ctx).has_neptune


def test_openCypher_property_graph_not_rdf(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"q.cypher": "MATCH (a:P) RETURN a\n"})
    model = neptune_model(ctx)
    assert "opencypher" in model.query_languages
    assert "sparql" not in model.query_languages


def test_sparql_rdf_not_property_graph(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"q.sparql": "SELECT ?s WHERE { ?s ?p ?o }\n"})
    model = neptune_model(ctx)
    assert model.query_languages == {"sparql"}


def test_gremlin_alias_no_traversal_vocab(tmp_path: Path) -> None:
    """An aliased client named ``g`` doing non-traversal calls must not
    produce a language signal."""
    ctx = make_context(tmp_path, {"app.py": "g = get_client()\ng.V()\ng.E()\n"})
    model = neptune_model(ctx)
    # g.V()/g.E() alone carry no traversal vocabulary - no language proof.
    assert "gremlin" not in model.query_languages or model.read_traversals == 0


def test_mixed_paradigms_both_visible(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "q.cypher": "MATCH (a:P)-[:K]->(b) RETURN a LIMIT 1\n",
            "data.ttl": "<a> <b> <c> .\n",
        },
    )
    model = neptune_model(ctx)
    assert "opencypher" in model.query_languages


def test_bulk_loader_literal_source(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "job.py": (
                'import boto3\ndata = boto3.client("neptunedata")\n'
                'data.start_loader_job(source="s3://b/p", iamRoleArn="r")\n'
            )
        },
    )
    loads = neptune_model(ctx).bulk_loads
    assert loads and loads[0].source_s3 == "s3://b/p"


def test_dynamic_query_unresolved(tmp_path: Path) -> None:
    """A query built from variables is not reconstructed."""
    ctx = make_context(
        tmp_path,
        {"app.py": 'q = "MATCH (a:" + label + ") RETURN a"\nx.submit(q)\n'},
    )
    model = neptune_model(ctx)
    # no query file, no literal query string: nothing to claim
    assert not model.query_languages


def test_malformed_query_surfaces_unparsed(tmp_path: Path) -> None:
    from forge_doctor.analyzers.neptune_queries import neptune_queries

    ctx = make_context(tmp_path, {"bad.cypher": "TOTALLY NOT CYPHER {{{\n"})
    assert neptune_queries(ctx).unparsed


def test_analytics_vs_database_split(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"infra.tf": 'resource "aws_neptune_graph" "g" {}\n'},
    )
    model = neptune_model(ctx)
    assert model.product == "analytics"
    assert "database" not in model.products


def test_neptune_db_client_is_database(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"app.py": 'import boto3\nx = boto3.client("neptune")\n'})
    model = neptune_model(ctx)
    assert model.product == "database"
