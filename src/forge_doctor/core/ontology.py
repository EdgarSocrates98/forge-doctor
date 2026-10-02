"""Canonical platform ontology — the documented vocabulary (spec 225).

The runtime enums (:class:`EntityKind`, :class:`RelKind`,
:class:`EvidenceKind`) are the executable vocabulary; this module is the
*documented registry* they conform to. Every term carries a one-line
definition, so docs and `forge-doctor ontology` output derive from one
source instead of drifting copies.

Conformance: ``validate_graph`` reports entities/relationships whose
free-text fields (producer ``domain``) fall outside the vocabulary —
the enums already constrain ``kind`` at construction time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from forge_doctor.core.incremental import (
    CI,
    CODE,
    CONFIG,
    CONTRACT,
    DOCKER,
    ENV,
    FILES,
    GIT,
    GRAPH,
    HOST,
    NOTEBOOK,
    PACKAGING,
    PYTHON,
    RUNTIME,
    SQL,
    TERRAFORM,
    UNBOUNDED,
)
from forge_doctor.core.models import EvidenceKind
from forge_doctor.core.platform_graph import EntityKind, RelKind

if TYPE_CHECKING:
    from forge_doctor.core.platform_graph import DataPlatformGraph


@dataclass(frozen=True)
class Term:
    """One ontology term: a stable public identifier + its definition."""

    name: str
    definition: str


# -- Entity kinds ------------------------------------------------------

_ENTITY_DEFS: dict[EntityKind, str] = {
    EntityKind.WORKFLOW: "An orchestrated pipeline of tasks (DAG, state machine, job flow).",
    EntityKind.TASK: "A single unit of work inside a workflow.",
    EntityKind.COMPUTE_JOB: "An execution target that runs code (Glue job, EMR cluster, Lambda).",
    EntityKind.QUERY: "A named or observed query (Athena named query, authored SQL).",
    EntityKind.DATASET: "A logical dataset independent of physical layout.",
    EntityKind.TABLE: "A physical or cataloged table (DynamoDB, Iceberg, Glue catalog).",
    EntityKind.STREAM: "A streaming channel (Kafka topic, Kinesis stream, SNS/SQS).",
    EntityKind.CATALOG: "A metadata catalog or namespace (Glue database, Databricks catalog).",
    EntityKind.STORAGE_LOCATION: "A storage endpoint (bucket/prefix, volume, external location).",
    EntityKind.PRINCIPAL: "An identity that holds grants (role, storage credential, LF principal).",
    EntityKind.INFRASTRUCTURE_RESOURCE: "An IaC-declared resource not covered by a narrower kind.",
    EntityKind.DATABASE: "A database instance or cluster (Neptune, operational stores).",
    EntityKind.GRAPH: "A property-graph container (Neptune cluster/graph).",
    EntityKind.GRAPH_NODE: "A node inside a property graph.",
    EntityKind.GRAPH_EDGE: "An edge inside a property graph.",
    EntityKind.REPO: "A repository participating in a workspace.",
    EntityKind.CAPABILITY: "A platform capability fact evaluated against context.",
    EntityKind.KNOWLEDGE_PACK: "A bundled knowledge pack (capabilities/errors/golden facts).",
}

# -- Relationship kinds ------------------------------------------------

_REL_DEFS: dict[RelKind, str] = {
    RelKind.INVOKES: "src triggers execution of dst (job call, function invoke).",
    RelKind.READS: "src reads data from dst.",
    RelKind.WRITES: "src writes data to dst.",
    RelKind.DEFINES: "src declares dst (IaC defines resource, workspace defines repo).",
    RelKind.GOVERNS: "src governs access to dst (LF grant, catalog policy).",
    RelKind.STORED_IN: "src's data physically resides in dst.",
    RelKind.DEPENDS_ON: "src requires dst to exist/run first (ordering, dependency).",
    RelKind.TRIGGERS: "src event fires dst (schedule, event rule, notification).",
    RelKind.PRODUCES: "src emits records consumed downstream (stream producer).",
    RelKind.CONSUMES: "src consumes records produced upstream (stream consumer).",
    RelKind.IMPLEMENTS: "src provides the implementation dst declares (repo implements entity).",
    RelKind.EVIDENCED_BY: "src's claim/status is established by dst (knowledge pack, evidence).",
}

# -- Evidence planes ----------------------------------------------------

_PLANE_DEFS: dict[EvidenceKind, str] = {
    EvidenceKind.STATIC: "Parsed source code / AST facts.",
    EvidenceKind.CONFIG: "Declarative config, manifests, IaC.",
    EvidenceKind.OBSERVED_METADATA: "Real metadata artifacts committed to the repo.",
    EvidenceKind.RUNTIME: "Runtime artifacts (progress logs, exported telemetry).",
    EvidenceKind.DERIVED: "Inferred by combining multiple facts.",
}

# -- Evidence domains (incremental channels) ----------------------------

_DOMAIN_DEFS: dict[str, str] = {
    PYTHON: ".py/.pyi — AST index, boto3, spark/glue/streaming models.",
    CODE: "Other source languages — .scala/.sh/.bat/.cmd/.ps1.",
    SQL: ".sql/.hql query files.",
    TERRAFORM: ".tf/.tfvars/.hcl infrastructure definitions.",
    CONFIG: ".yml/.yaml/.json/.toml/.ini/.cfg/.conf/.properties/.xml.",
    NOTEBOOK: ".ipynb notebooks.",
    GRAPH: "Cypher/gremlin/sparql/rdf graph files.",
    PACKAGING: "pyproject.toml, requirements, lock files.",
    DOCKER: "Dockerfile, compose, .dockerignore.",
    CI: "Workflow/pipeline definitions (GitHub Actions, GitLab CI…).",
    RUNTIME: "runtime/ evidence artifacts ingested by the project.",
    CONTRACT: "platform-contract.* files.",
    FILES: "File-tree enumeration — names and presence only.",
    ENV: "Host environment variables.",
    GIT: "Git index / tracked-file set.",
    HOST: "Host tools, home dir, PATH interpreters.",
    UNBOUNDED: "Derived multi-domain state — cannot be invalidated selectively.",
}

# -- Producer domains ---------------------------------------------------
#
# The ``domain`` segment of an entity id names the model/family that
# produced it. Free text at construction, so the vocabulary lives here.

_PRODUCER_DOMAIN_DEFS: dict[str, str] = {
    "airflow": "Apache Airflow orchestration model.",
    "athena": "Amazon Athena analytics model.",
    "aws": "Generic AWS provider resources not mapped to a narrower family.",
    "controlm": "Control-M scheduling model.",
    "databricks": "Databricks workspace model.",
    "delta": "Delta Lake table model.",
    "dynamodb": "Amazon DynamoDB model.",
    "emr": "Amazon EMR model.",
    "firehose": "Amazon Data Firehose model.",
    "flink": "Apache Flink model.",
    "glue": "AWS Glue jobs/catalog model.",
    "graph": "Generic property-graph model.",
    "graphdata": "Authored graph data files.",
    "iceberg": "Apache Iceberg table model.",
    "kafka": "Apache Kafka model.",
    "kinesis": "Amazon Kinesis model.",
    "knowledge": "Bundled knowledge packs (capabilities/errors provenance).",
    "lakeformation": "AWS Lake Formation governance model.",
    "lambda": "AWS Lambda model.",
    "neptune": "Amazon Neptune model.",
    "neptune_loader": "Neptune bulk-loader task entities (loader functions).",
    "parquet": "Apache Parquet physical-layout model.",
    "sns": "Amazon SNS model.",
    "spark_ss": "Spark Structured Streaming model.",
    "sql": "First-class SQL model (sqlglot index).",
    "sqs": "Amazon SQS model.",
    "stepfunctions": "AWS Step Functions model.",
    "terraform": "Terraform IaC model.",
    "workspace": "Workspace/repo aggregation model.",
}


# -- Public surface -----------------------------------------------------


def entity_kinds() -> tuple[Term, ...]:
    return tuple(Term(k.value, _ENTITY_DEFS[k]) for k in EntityKind)


def relationship_kinds() -> tuple[Term, ...]:
    return tuple(Term(k.value, _REL_DEFS[k]) for k in RelKind)


def evidence_planes() -> tuple[Term, ...]:
    return tuple(Term(k.value, _PLANE_DEFS[k]) for k in EvidenceKind)


def evidence_domains() -> tuple[Term, ...]:
    return tuple(Term(k, _DOMAIN_DEFS[k]) for k in sorted(_DOMAIN_DEFS))


def producer_domains() -> tuple[Term, ...]:
    return tuple(Term(k, _PRODUCER_DOMAIN_DEFS[k]) for k in sorted(_PRODUCER_DOMAIN_DEFS))


def capability_families() -> tuple[str, ...]:
    """Platform families with bundled capability packs (sorted, runtime-derived)."""
    from forge_doctor.core.capabilities import CapabilityRegistry

    return tuple(sorted(CapabilityRegistry().platforms()))


def vocabulary() -> dict[str, list[dict[str, str]]]:
    """Deterministic JSON-serializable vocabulary (stable key order)."""
    return {
        "capability_families": [{"name": n, "definition": ""} for n in capability_families()],
        "entity_kinds": [{"name": t.name, "definition": t.definition} for t in entity_kinds()],
        "evidence_domains": [
            {"name": t.name, "definition": t.definition} for t in evidence_domains()
        ],
        "evidence_planes": [
            {"name": t.name, "definition": t.definition} for t in evidence_planes()
        ],
        "producer_domains": [
            {"name": t.name, "definition": t.definition} for t in producer_domains()
        ],
        "relationship_kinds": [
            {"name": t.name, "definition": t.definition} for t in relationship_kinds()
        ],
    }


def validate_graph(graph: DataPlatformGraph) -> list[str]:
    """Conformance violations in a built graph (sorted, deterministic).

    Entity/rel *kinds* are enum-constrained at construction, so the
    runtime-checkable drift surface is the free-text producer ``domain``.
    """
    known = set(_PRODUCER_DOMAIN_DEFS)
    violations = {
        f"entity {e.id}: unknown producer domain {e.domain!r}"
        for e in graph.entities()
        if e.domain not in known
    }
    return sorted(violations)
