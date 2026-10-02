"""Populate a DataPlatformGraph from the domain models (spec 172).

Each domain adapter maps model facts to canonical entities + typed
relationships. Adapters are unidirectional: models never import each
other - this module is the single composition point. Edges carry the
EvidenceKind of the fact they came from.

Canonical ids use the *platform* domain (``table:dynamodb:x``,
``compute_job:lambda:f``), not the producing model - a Terraform
``aws_dynamodb_table`` and later DynamoDB code evidence merge on the
same entity id. Joins are therefore deterministic by construction: two
producers emit the same id only when the identifier is identical.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from forge_doctor.core.models import EvidenceKind
from forge_doctor.core.platform_graph import (
    DataPlatformGraph,
    Entity,
    Relationship,
)
from forge_doctor.core.platform_graph import (
    EntityKind as K,
)
from forge_doctor.core.platform_graph import (
    RelKind as R,
)

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext

_CACHE_ATTR = "_forge_doctor_platform_graph"

_CFG = EvidenceKind.CONFIG
_STA = EvidenceKind.STATIC
_DER = EvidenceKind.DERIVED
_OBS = EvidenceKind.OBSERVED_METADATA

# Terraform resource type -> (entity kind, platform domain) so the
# definition lands on the same canonical id code evidence would use.
_TF_TYPED: dict[str, tuple[K, str]] = {
    "aws_sfn_state_machine": (K.WORKFLOW, "stepfunctions"),
    "aws_lambda_function": (K.COMPUTE_JOB, "lambda"),
    "aws_glue_job": (K.COMPUTE_JOB, "glue"),
    "aws_dynamodb_table": (K.TABLE, "dynamodb"),
    "aws_s3_bucket": (K.STORAGE_LOCATION, "s3"),
    "aws_kinesis_stream": (K.STREAM, "kinesis"),
    "aws_neptune_cluster": (K.GRAPH, "neptune"),
    "aws_glue_catalog_database": (K.CATALOG, "glue"),
    "aws_emr_cluster": (K.COMPUTE_JOB, "emr"),
    "aws_emrserverless_application": (K.COMPUTE_JOB, "emr"),
    "aws_emrcontainers_virtual_cluster": (K.COMPUTE_JOB, "emr"),
    "databricks_job": (K.COMPUTE_JOB, "databricks"),
    "databricks_cluster": (K.COMPUTE_JOB, "databricks"),
    "databricks_pipeline": (K.COMPUTE_JOB, "databricks"),
    "databricks_sql_warehouse": (K.COMPUTE_JOB, "databricks"),
    "databricks_sql_endpoint": (K.COMPUTE_JOB, "databricks"),
    "databricks_catalog": (K.CATALOG, "databricks"),
    "databricks_schema": (K.CATALOG, "databricks"),
    "databricks_volume": (K.STORAGE_LOCATION, "databricks"),
    "databricks_external_location": (K.STORAGE_LOCATION, "databricks"),
    "databricks_storage_credential": (K.PRINCIPAL, "databricks"),
    "aws_athena_named_query": (K.QUERY, "athena"),
    "aws_athena_prepared_statement": (K.QUERY, "athena"),
    "aws_athena_database": (K.CATALOG, "glue"),
    "aws_athena_data_catalog": (K.CATALOG, "athena"),
    "aws_athena_workgroup": (K.COMPUTE_JOB, "athena"),
}

# Streaming source/sink class -> entity kind (bus/topic vs table vs blob).
_ENDPOINT_KINDS: dict[str, K] = {
    "kafka": K.STREAM,
    "kinesis": K.STREAM,
    "delta": K.TABLE,
    "iceberg": K.TABLE,
}

# ASL integration family -> (entity kind, platform domain) for Task
# resources the state machine invokes.
_SFN_RESOURCES: dict[str, tuple[K, str]] = {
    "lambda": (K.COMPUTE_JOB, "lambda"),
    "glue": (K.COMPUTE_JOB, "glue"),
    "athena": (K.QUERY, "athena"),
    "dynamodb": (K.TABLE, "dynamodb"),
    "sns": (K.STREAM, "sns"),
    "sqs": (K.STREAM, "sqs"),
}


def _e(
    entity_kind: K,
    domain: str,
    ident: str,
    file: Path | None = None,
    line: int | None = None,
    **attrs: str,
) -> Entity:
    return Entity(
        kind=entity_kind,
        domain=domain,
        identifier=ident,
        file=file,
        line=line,
        attrs=tuple(sorted(attrs.items())),
    )


# Airflow operator -> (entity kind, platform domain) for the external
# resource its ``target`` kwarg names. Only operators whose target maps
# to a canonical platform entity id produce an edge.
_AIRFLOW_OPERATOR_TARGETS: dict[str, tuple[K, str]] = {
    "GlueJobOperator": (K.COMPUTE_JOB, "glue"),
    "GlueJobRunTrigger": (K.COMPUTE_JOB, "glue"),
    "LambdaInvokeFunctionOperator": (K.COMPUTE_JOB, "lambda"),
    "LambdaInvokeAsyncOperator": (K.COMPUTE_JOB, "lambda"),
    "StepFunctionStartExecutionOperator": (K.WORKFLOW, "stepfunctions"),
    "EmrAddStepsOperator": (K.COMPUTE_JOB, "emr"),
    "EmrServerlessStartJobOperator": (K.COMPUTE_JOB, "emr"),
    "DatabricksRunNowOperator": (K.COMPUTE_JOB, "databricks"),
    "DatabricksSubmitRunOperator": (K.COMPUTE_JOB, "databricks"),
}


def _airflow(ctx: ProjectContext, g: DataPlatformGraph) -> None:
    from forge_doctor.analyzers.airflow_model import airflow_model

    model = airflow_model(ctx)
    for dag in model.dags:
        g.add_entity(
            _e(
                K.WORKFLOW,
                "airflow",
                dag.dag_id or dag.var,
                dag.file,
                dag.line,
                schedule=dag.schedule,
            )
        )
    task_ids: dict[str, str] = {}  # var or task_id -> canonical task id
    for task in model.tasks:
        t = _e(K.TASK, "airflow", task.task_id, task.file, task.line, operator=task.operator)
        g.add_entity(t)
        for alias in {task.var, task.task_id}:
            if alias:
                task_ids.setdefault(alias, t.id)
        if task.dag:
            for dag in model.dags:
                if task.dag in {dag.var, dag.dag_id}:
                    g.add_entity(_e(K.WORKFLOW, "airflow", dag.dag_id or dag.var))
                    g.add_relationship(
                        Relationship(
                            src=f"workflow:airflow:{dag.dag_id or dag.var}",
                            dst=t.id,
                            kind=R.INVOKES,
                            evidence_kind=_STA,
                        )
                    )
        kind_domain = _AIRFLOW_OPERATOR_TARGETS.get(task.operator)
        if task.target and kind_domain is not None:
            kind, domain = kind_domain
            ident = task.target.rsplit(":", 1)[-1].rsplit("/", 1)[-1]
            target = _e(kind, domain, ident or task.target)
            g.add_entity(target)
            g.add_relationship(
                Relationship(src=t.id, dst=target.id, kind=R.INVOKES, evidence_kind=_STA)
            )
    for edge in model.edges:
        # `a >> b` means b depends on a; edges reference vars or task_ids.
        src_id = task_ids.get(edge.dst)
        dst_id = task_ids.get(edge.src)
        if src_id and dst_id:
            g.add_relationship(
                Relationship(src=src_id, dst=dst_id, kind=R.DEPENDS_ON, evidence_kind=_STA)
            )


def _controlm(ctx: ProjectContext, g: DataPlatformGraph) -> None:
    from forge_doctor.analyzers.controlm_model import controlm_model

    model = controlm_model(ctx)
    for folder in model.folders:
        g.add_entity(_e(K.WORKFLOW, "controlm", folder.name, folder.file, folder.line))
    producers: dict[str, str] = {}  # event name -> producing job id
    for job in model.jobs:
        jid = f"{job.folder}.{job.name}" if job.folder else job.name
        j = _e(K.TASK, "controlm", jid, job.file, job.line, job_type=job.job_type)
        g.add_entity(j)
        if job.folder:
            g.add_relationship(
                Relationship(
                    src=f"workflow:controlm:{job.folder}",
                    dst=j.id,
                    kind=R.INVOKES,
                    evidence_kind=_CFG,
                )
            )
        for event in job.add_events:
            producers.setdefault(event, j.id)
    for job in model.jobs:
        jid = f"{job.folder}.{job.name}" if job.folder else job.name
        for event in job.wait_events:
            src = producers.get(event)
            if src and src != f"task:controlm:{jid}":
                g.add_relationship(
                    Relationship(
                        src=f"task:controlm:{jid}",
                        dst=src,
                        kind=R.DEPENDS_ON,
                        evidence_kind=_DER,
                        attrs=(("via_event", event),),
                    )
                )


def _stepfunctions(ctx: ProjectContext, g: DataPlatformGraph) -> None:
    from forge_doctor.analyzers.stepfunctions_model import stepfunctions_model

    for machine in stepfunctions_model(ctx).machines:
        wf = _e(
            K.WORKFLOW, "stepfunctions", machine.name, machine.file, machine.line, type=machine.type
        )
        g.add_entity(wf)
        for state in machine.states:
            sid = f"{machine.name}.{state.name}"
            task = _e(K.TASK, "stepfunctions", sid, machine.file, type=state.type)
            g.add_entity(task)
            g.add_relationship(
                Relationship(src=wf.id, dst=task.id, kind=R.INVOKES, evidence_kind=_CFG)
            )
            target = _sfn_target(state)
            if target is not None:
                g.add_entity(target)
                g.add_relationship(
                    Relationship(src=task.id, dst=target.id, kind=R.INVOKES, evidence_kind=_CFG)
                )


def _sfn_target(state: object) -> Entity | None:
    """Entity for a Task's integration resource, when identifiable."""
    integration = getattr(state, "integration", "")
    resource = getattr(state, "resource", "")
    # ``arn:aws:states:::lambda:invoke``-style resources encode the
    # integration pattern, not a named entity - nothing to point at.
    if not resource or resource.startswith("arn:aws:states:::"):
        return None
    if integration.startswith("sdk:"):
        svc = integration.split(":", 1)[1]
        return _resource_entity(resource, K.INFRASTRUCTURE_RESOURCE, svc or "aws")
    kind_domain = _SFN_RESOURCES.get(integration)
    if kind_domain is None:
        return None
    return _resource_entity(resource, *kind_domain)


def _resource_entity(arn: str, kind: K, domain: str) -> Entity:
    """Canonical entity for a service-integration ARN-ish reference."""
    ident = arn.rsplit(":", 1)[-1].rsplit("/", 1)[-1] if ":" in arn else arn
    return _e(kind, domain, ident or arn)


# Catalog name -> impl classes seen, from model facts only
# (``spark.sql.catalog.<name> = <impl>``, IaC catalog resources).
def _catalog_impls(ctx: ProjectContext) -> dict[str, set[str]]:
    from forge_doctor.analyzers.iceberg_model import iceberg_model

    impls: dict[str, set[str]] = {}
    for e in iceberg_model(ctx).by_kind("catalog"):
        impls.setdefault(e.name, set()).add(e.value)
    return impls


def _non_iceberg(impls: dict[str, set[str]], name: str) -> bool:
    """True iff the catalog has impl evidence and none is iceberg."""
    vals = {v for v in impls.get(name, set()) if v}
    return bool(vals) and not any("iceberg" in v.lower() for v in vals)


# Catalog name -> table domain: ``spark.sql.catalog.<name> = *iceberg*``
# means refs under ``<name>.`` are iceberg tables - the producer doesn't
# choose the namespace, the configured catalog does. Never fuzzy.
def _catalog_domains(ctx: ProjectContext) -> dict[str, str]:
    impls = _catalog_impls(ctx)
    return {
        name: "iceberg" for name, vals in impls.items() if any("iceberg" in v.lower() for v in vals)
    }


def _table_id(name: str, declared: str, catalog_domains: dict[str, str]) -> str:
    """Canonical id for a table reference. A known catalog qualifier wins
    over the producing model's domain; otherwise the declared domain
    (sql/delta/iceberg) is kept."""
    head, _, _rest = name.partition(".")
    domain = catalog_domains.get(head) or declared
    return f"table:{domain}:{name}"


def _endpoint_entity(fmt: str, identifier: str, catalog_domains: dict[str, str]) -> Entity:
    """Endpoint entity for a stream source/sink.

    An *identified* endpoint lands on its canonical id (kafka topic,
    delta/iceberg table resolved through known catalogs). An
    unidentified one becomes ``dataset:<fmt>:<fmt>`` - an honest marker
    that never masquerades as a specific table.
    """
    kind = _ENDPOINT_KINDS.get(fmt, K.DATASET)
    if kind is K.TABLE:
        if identifier:
            return _table(_table_id(identifier, fmt, catalog_domains))
        return _e(K.DATASET, fmt, fmt, identified="no")
    if identifier:
        return _e(kind, fmt, identifier)
    return _e(kind, fmt, fmt, identified="no")


def _streaming(ctx: ProjectContext, g: DataPlatformGraph) -> None:
    from forge_doctor.analyzers.streaming_model import streaming_model

    catalog_domains = _catalog_domains(ctx)
    for q in streaming_model(ctx).queries:
        sid = f"{q.file.as_posix()}:{q.line}:{q.name}"
        stream = _e(
            K.STREAM, "spark_ss", sid, q.file, q.line, engine=q.engine, trigger=q.trigger_kind
        )
        g.add_entity(stream)
        if q.source:
            src = _endpoint_entity(q.source, q.source_identifier, catalog_domains)
            g.add_entity(src)
            g.add_relationship(
                Relationship(src=stream.id, dst=src.id, kind=R.CONSUMES, evidence_kind=_STA)
            )
        if q.sink and q.sink != "foreachBatch":
            sink = _endpoint_entity(q.sink, q.sink_identifier, catalog_domains)
            g.add_entity(sink)
            g.add_relationship(
                Relationship(src=stream.id, dst=sink.id, kind=R.PRODUCES, evidence_kind=_STA)
            )


def _table(table_id: str) -> Entity:
    """Entity for a canonical ``table:<domain>:<name>`` id."""
    _, domain, ident = table_id.split(":", 2)
    return _e(K.TABLE, domain, ident)


def _graph_intel(ctx: ProjectContext, g: DataPlatformGraph) -> None:
    """GraphProjectModel adapter: schema/model entities, not real nodes.

    ``graph:graphdata:<file>`` marks each detected artifact; vertex/edge
    labels land as GRAPH_NODE/GRAPH_EDGE *types* (never per-record) and
    traversals become QUERY entities reading/writing the workload graph.
    """
    from forge_doctor.analyzers.graph_model import graph_model

    model = graph_model(ctx)
    by_file: dict[Path, str] = {}
    for w in model.workloads:
        e = _e(K.GRAPH, "graphdata", w.file.as_posix(), w.file, w.line, paradigm=w.paradigm)
        g.add_entity(e)
        by_file.setdefault(w.file, e.id)
    for label, sites in model.vertex_labels.items():
        e = _e(K.GRAPH_NODE, "graph", label, sites[0][0], sites[0][1])
        g.add_entity(e)
    for label, sites in model.edge_labels.items():
        e = _e(K.GRAPH_EDGE, "graph", label, sites[0][0], sites[0][1])
        g.add_entity(e)
    for t in model.traversals:
        q = _e(
            K.QUERY,
            "graph",
            f"{t.file.as_posix()}:{t.line}",
            t.file,
            t.line,
            language=t.language,
        )
        g.add_entity(q)
        target = by_file.get(t.file)
        if target:
            g.add_relationship(
                Relationship(
                    src=q.id,
                    dst=target,
                    kind=R.WRITES if t.writes else R.READS,
                    evidence_kind=_STA,
                )
            )
        for label in t.vertex_labels:
            g.add_relationship(
                Relationship(
                    src=q.id,
                    dst=f"graph_node:graph:{label}",
                    kind=R.READS,
                    evidence_kind=_STA,
                )
            )
        for label in t.edge_labels:
            g.add_relationship(
                Relationship(
                    src=q.id,
                    dst=f"graph_edge:graph:{label}",
                    kind=R.READS,
                    evidence_kind=_STA,
                )
            )


def _lambda_index(ctx: ProjectContext) -> tuple[dict[str, str], dict[str, str]]:
    """Lambda canonical names + handler-module stems for joins.

    Returns (label -> display name, module stem -> lambda name)."""
    from forge_doctor.analyzers.terraform_model import terraform_model

    by_label: dict[str, str] = {}
    by_module: dict[str, str] = {}
    for res in terraform_model(ctx).resources:
        if not res.labels or res.labels[0] != "aws_lambda_function":
            continue
        name = str(res.attrs.get("function_name") or res.labels[-1])
        by_label[res.labels[-1]] = name
        handler = str(res.attrs.get("handler") or "")
        stem = handler.split(".", 1)[0]
        if stem:
            by_module.setdefault(stem, name)
    return by_label, by_module


def _dynamodb(ctx: ProjectContext, g: DataPlatformGraph) -> None:
    """DynamoDB adapter: tables, streams->lambdas, code access edges."""
    from forge_doctor.analyzers.dynamodb_model import dynamodb_model
    from forge_doctor.analyzers.terraform_model import terraform_model

    model = dynamodb_model(ctx)
    for t in model.tables:
        g.add_entity(_e(K.TABLE, "dynamodb", t.name, t.file, t.line, source=t.source))
    for s in model.streams:
        if not s.table:
            continue
        stream = _e(K.STREAM, "dynamodb", s.table, s.file, s.line, view=s.view_type)
        g.add_entity(stream)
        g.add_relationship(
            Relationship(
                src=f"table:dynamodb:{s.table}",
                dst=stream.id,
                kind=R.PRODUCES,
                evidence_kind=_CFG,
            )
        )
    tf_label_to_table: dict[str, str] = {}
    for res in terraform_model(ctx).resources:
        if res.labels and res.labels[0] == "aws_dynamodb_table":
            tf_label_to_table[res.labels[-1]] = str(res.attrs.get("name") or res.labels[-1])
    lambda_names, _ = _lambda_index(ctx)
    for res in terraform_model(ctx).resources:
        if not res.labels or res.labels[0] != "aws_lambda_event_source_mapping":
            continue
        arn = str(res.attrs.get("event_source_arn") or "")
        table = next(
            (name for label, name in tf_label_to_table.items() if label in arn),
            None,
        )
        if table is None:
            continue
        fn = str(res.attrs.get("function_name") or res.attrs.get("function_arn") or "")
        fn_name = next((name for label, name in lambda_names.items() if label in fn), fn or "")
        if not fn_name:
            continue
        lam = _e(K.COMPUTE_JOB, "lambda", fn_name, res.file, res.line)
        g.add_entity(lam)
        stream = _e(K.STREAM, "dynamodb", table)
        g.add_entity(stream)
        g.add_relationship(
            Relationship(src=stream.id, dst=lam.id, kind=R.TRIGGERS, evidence_kind=_CFG)
        )
    for a in model.accesses:
        if not a.table:
            continue
        t_ent = _e(K.TABLE, "dynamodb", a.table)
        g.add_entity(t_ent)
        q = _e(
            K.QUERY,
            "dynamodb",
            f"{a.file.as_posix()}:{a.line}",
            a.file,
            a.line,
            op=a.op,
        )
        g.add_entity(q)
        kind = (
            R.WRITES
            if a.op.startswith(("put", "update", "delete", "batch_write", "transact_write"))
            else R.READS
        )
        g.add_relationship(Relationship(src=q.id, dst=t_ent.id, kind=kind, evidence_kind=_STA))


def _neptune(ctx: ProjectContext, g: DataPlatformGraph) -> None:
    """Neptune adapter: cluster graphs, loader jobs, code writes."""
    from forge_doctor.analyzers.neptune_model import neptune_model
    from forge_doctor.analyzers.neptune_queries import neptune_queries

    model = neptune_model(ctx)
    cluster_ids: list[str] = []
    for c in model.clusters:
        ent = _e(K.GRAPH, "neptune", c.name, c.file, c.line, product="database")
        g.add_entity(ent)
        cluster_ids.append(ent.id)
    for e in model.endpoints:
        g.add_entity(
            _e(
                K.INFRASTRUCTURE_RESOURCE,
                "neptune",
                e.value,
                e.file,
                e.line,
                type="endpoint",
            )
        )

    # File -> cluster resolution: a query joins the cluster whose name
    # appears in an endpoint the same file references; a single cluster
    # absorbs everything. Otherwise the join stays unresolved (no edge).
    file_cluster: dict[Path, str] = {}
    for e in model.endpoints:
        for c in model.clusters:
            if c.name and c.name in e.value:
                file_cluster.setdefault(e.file, f"graph:neptune:{c.name}")

    _labels, lambda_by_module = _lambda_index(ctx)
    for q in neptune_queries(ctx).queries:
        target = file_cluster.get(q.file)
        if target is None:
            if len(cluster_ids) != 1:
                continue  # multi-cluster ambiguity stays unresolved
            target = cluster_ids[0]
        qent = _e(
            K.QUERY,
            "neptune",
            f"{q.file.as_posix()}:{q.line}",
            q.file,
            q.line,
            language=q.language,
        )
        g.add_entity(qent)
        g.add_relationship(
            Relationship(
                src=qent.id,
                dst=target,
                kind=R.WRITES if q.writes else R.READS,
                evidence_kind=_STA,
            )
        )
        if q.writes:
            lam = lambda_by_module.get(q.file.stem)
            if lam:
                le = _e(K.COMPUTE_JOB, "lambda", lam)
                g.add_entity(le)
                g.add_relationship(
                    Relationship(src=le.id, dst=target, kind=R.WRITES, evidence_kind=_DER)
                )
    for load in model.bulk_loads:
        job = _e(
            K.TASK,
            "neptune_loader",
            f"{load.file.as_posix()}:{load.line}",
            load.file,
            load.line,
            origin=load.origin,
        )
        g.add_entity(job)
        if load.source_s3:
            bucket = load.source_s3.removeprefix("s3://").split("/", 1)[0]
            s3 = _e(K.STORAGE_LOCATION, "s3", bucket)
            g.add_entity(s3)
            g.add_relationship(
                Relationship(src=job.id, dst=s3.id, kind=R.READS, evidence_kind=_STA)
            )
        target = cluster_ids[0] if cluster_ids else None
        if target:
            g.add_relationship(
                Relationship(src=job.id, dst=target, kind=R.WRITES, evidence_kind=_STA)
            )


def _sql(ctx: ProjectContext, g: DataPlatformGraph) -> None:
    from forge_doctor.analyzers.sql_ast import analyze_sql

    catalog_domains = _catalog_domains(ctx)
    for stmt in analyze_sql(ctx).statements:
        q = _e(
            K.QUERY,
            "sql",
            f"{stmt.file.as_posix()}:{stmt.line}",
            stmt.file,
            stmt.line,
            kind=stmt.kind,
        )
        g.add_entity(q)
        for table in stmt.tables_read:
            t = _table(_table_id(table, "sql", catalog_domains))
            g.add_entity(t)
            g.add_relationship(Relationship(src=q.id, dst=t.id, kind=R.READS, evidence_kind=_STA))
        for table in stmt.tables_written:
            t = _table(_table_id(table, "sql", catalog_domains))
            g.add_entity(t)
            g.add_relationship(Relationship(src=q.id, dst=t.id, kind=R.WRITES, evidence_kind=_STA))


def _iceberg(ctx: ProjectContext, g: DataPlatformGraph) -> None:
    from forge_doctor.analyzers.iceberg_model import iceberg_model

    model = iceberg_model(ctx)
    impls = _catalog_impls(ctx)
    # The model claims tables under ANY registered catalog name; for the
    # graph we keep them unless the catalog's impl is positively
    # non-iceberg (demonstrable identity only, same rule as SQL refs).
    for name, ev in model.tables.items():
        head = name.partition(".")[0]
        if "." in name and head in impls and _non_iceberg(impls, head):
            continue
        g.add_entity(_e(K.TABLE, "iceberg", name, ev.file, ev.line))
    for name in model.catalog_names:
        if not _non_iceberg(impls, name):
            g.add_entity(_e(K.CATALOG, "iceberg", name))
    # catalog.<table> prefix match: catalog GOVERNS table (inferred join).
    for name in model.tables:
        for cat in model.catalog_names:
            if _non_iceberg(impls, cat):
                continue
            if name.startswith(f"{cat}.") and g.entity(f"table:iceberg:{name}"):
                g.add_relationship(
                    Relationship(
                        src=f"catalog:iceberg:{cat}",
                        dst=f"table:iceberg:{name}",
                        kind=R.GOVERNS,
                        evidence_kind=_DER,
                    )
                )


def _parquet(ctx: ProjectContext, g: DataPlatformGraph) -> None:
    from forge_doctor.analyzers.parquet_model import parquet_model

    # Only on-disk ``file`` evidence carries an identity (its repo-relative
    # path); reader/writer evidence is a call-site, not a location.
    for e in parquet_model(ctx).by_kind("file"):
        dataset = _e(K.DATASET, "parquet", e.name, e.file, e.line, evidence="on_disk")
        g.add_entity(dataset)
        parent = Path(e.name).parent.as_posix()
        if parent and parent != ".":
            loc = _e(K.STORAGE_LOCATION, "parquet", parent)
            g.add_entity(loc)
            g.add_relationship(
                Relationship(src=dataset.id, dst=loc.id, kind=R.STORED_IN, evidence_kind=_OBS)
            )


def _terraform(ctx: ProjectContext, g: DataPlatformGraph) -> None:
    from forge_doctor.analyzers.terraform_model import terraform_model

    for res in terraform_model(ctx).resources:
        infra = _e(
            K.INFRASTRUCTURE_RESOURCE,
            "aws",
            res.address,
            res.file,
            res.line,
            type=res.labels[0] if res.labels else "",
        )
        g.add_entity(infra)
        mapping = _TF_TYPED.get(res.labels[0] if res.labels else "")
        if not mapping:
            continue
        kind, domain = mapping
        # SFN machines are named by the TF label in stepfunctions_model -
        # use the same key so both adapters converge on one entity.
        name = (
            res.labels[-1]
            if res.labels[0] == "aws_sfn_state_machine"
            else str(
                res.attrs.get("name")
                or res.attrs.get("function_name")
                or res.attrs.get("bucket")
                or res.attrs.get("cluster_identifier")
                or res.attrs.get("cluster_name")
                or res.labels[-1]
            )
        )
        typed = _e(kind, domain, name, res.file, res.line, producer="terraform")
        g.add_entity(typed)
        g.add_relationship(
            Relationship(src=infra.id, dst=typed.id, kind=R.DEFINES, evidence_kind=_CFG)
        )


def _lakeformation(ctx: ProjectContext, g: DataPlatformGraph) -> None:
    from forge_doctor.analyzers.lakeformation_model import lakeformation_model

    model = lakeformation_model(ctx)
    for grant in model.grants:
        if not grant.principal:
            continue
        principal = _e(K.PRINCIPAL, "lakeformation", grant.principal, grant.file, grant.line)
        g.add_entity(principal)
        # Grant target -> canonical entity: catalog on database/table kinds,
        # storage_location on registered s3 arns.
        dst: Entity | None = None
        if grant.resource_kind in ("database", "table", "columns"):
            name = grant.resource_name.split(".")[0] or grant.resource_name
            dst = _e(K.CATALOG, "glue", name, grant.file, grant.line)
        elif grant.resource_kind == "data_location":
            dst = _e(K.STORAGE_LOCATION, "s3", grant.resource_name)
        elif grant.resource_kind == "catalog":
            dst = _e(K.CATALOG, "glue", grant.resource_name or "account")
        if dst is not None:
            g.add_entity(dst)
            g.add_relationship(
                Relationship(
                    src=principal.id,
                    dst=dst.id,
                    kind=R.GOVERNS,
                    evidence_kind=_CFG,
                    attrs=(("permissions", "+".join(grant.permissions)),),
                )
            )
    for loc in model.data_locations:
        g.add_entity(
            _e(
                K.STORAGE_LOCATION,
                "lakeformation",
                loc.arn,
                loc.file,
                loc.line,
                registered="true",
            )
        )
    for link in model.resource_links:
        link_e = _e(K.CATALOG, "lakeformation", link.name, link.file, link.line)
        g.add_entity(link_e)
        if link.target_catalog:
            remote = _e(K.CATALOG, "glue", f"{link.target_catalog}:{link.target_database}")
            g.add_entity(remote)
            g.add_relationship(
                Relationship(
                    src=link_e.id,
                    dst=remote.id,
                    kind=R.DEPENDS_ON,
                    evidence_kind=_CFG,
                    attrs=(("resource_link", "true"),),
                )
            )


def _platforms(ctx: ProjectContext, g: DataPlatformGraph) -> None:
    """EMR / Databricks compute + Delta tables/ops as canonical entities."""
    from forge_doctor.analyzers.databricks_model import databricks_model
    from forge_doctor.analyzers.delta_model import delta_model
    from forge_doctor.analyzers.emr_model import emr_model

    emr = emr_model(ctx)
    for c in emr.clusters:
        g.add_entity(
            _e(K.COMPUTE_JOB, "emr", c.name, c.file, c.line, release=c.release, kind="ec2")
        )
    for app in emr.serverless_apps:
        g.add_entity(
            _e(
                K.COMPUTE_JOB,
                "emr",
                app.name,
                app.file,
                app.line,
                release=app.release,
                kind="serverless",
            )
        )
    for vc in emr.eks_clusters:
        g.add_entity(_e(K.COMPUTE_JOB, "emr", vc.name, vc.file, vc.line, kind="eks"))

    dbx = databricks_model(ctx)
    for j in dbx.jobs:
        g.add_entity(_e(K.COMPUTE_JOB, "databricks", j.name, j.file, j.line, kind="job"))
    for dc in dbx.clusters:
        g.add_entity(
            _e(
                K.COMPUTE_JOB,
                "databricks",
                dc.name,
                dc.file,
                dc.line,
                dbr=dc.dbr_version,
                kind="job_cluster" if dc.is_job_cluster else "cluster",
            )
        )
    for w in dbx.warehouses:
        g.add_entity(_e(K.COMPUTE_JOB, "databricks", w.name, w.file, w.line, kind="sql_warehouse"))
    for p in dbx.pipelines:
        g.add_entity(_e(K.COMPUTE_JOB, "databricks", p.name, p.file, p.line, kind="pipeline"))
    for uc in dbx.uc_objects:
        kind = (
            K.PRINCIPAL
            if uc.kind == "storage_credential"
            else K.STORAGE_LOCATION
            if uc.kind in ("external_location", "volume")
            else K.CATALOG
        )
        g.add_entity(_e(kind, "databricks", uc.name, uc.file, uc.line, uc_kind=uc.kind))

    delta = delta_model(ctx)
    for tname in sorted(delta.tables):
        g.add_entity(_e(K.TABLE, "delta", tname))
    for op in delta.ops:
        q = _e(K.QUERY, "delta", f"{op.file.as_posix()}:{op.line}", op.file, op.line, op=op.op)
        g.add_entity(q)
        if op.target:
            t = _e(K.TABLE, "delta", op.target)
            g.add_entity(t)
            rel = R.WRITES if op.op not in ("optimize", "vacuum") else R.DEPENDS_ON
            g.add_relationship(Relationship(src=q.id, dst=t.id, kind=rel, evidence_kind=_STA))


def _serverless(ctx: ProjectContext, g: DataPlatformGraph) -> None:
    """Lambda/Athena model entities + trigger/destination edges."""
    from forge_doctor.analyzers.athena_model import athena_model
    from forge_doctor.analyzers.lambda_model import lambda_model

    lam = lambda_model(ctx)
    for fn in lam.functions:
        g.add_entity(
            _e(
                K.COMPUTE_JOB,
                "lambda",
                fn.name,
                fn.file,
                fn.line,
                runtime=fn.runtime,
                producer=fn.source,
            )
        )
    _DEST_KINDS = {
        "sns": K.STREAM,
        "sqs": K.STREAM,
        "states": K.WORKFLOW,
        "lambda": K.COMPUTE_JOB,
        "events": K.STREAM,
    }

    # resolve a function reference (name, TF label, or CFN logical id)
    def _fn_id(ref: str) -> str:
        for fn in lam.functions:
            if fn.matches(ref):
                return f"compute_job:lambda:{fn.name}"
        return f"compute_job:lambda:{ref}" if ref else ""

    for src in lam.event_sources:
        fn_id = _fn_id(src.function)
        src_e = _e(
            K.STREAM,
            src.kind,
            src.detail or f"{src.kind}:{src.file.as_posix()}:{src.line}",
            src.file,
            src.line,
        )
        g.add_entity(src_e)
        if fn_id and any(e.id == fn_id for e in g.entities()):
            g.add_relationship(
                Relationship(src=src_e.id, dst=fn_id, kind=R.TRIGGERS, evidence_kind=_CFG)
            )
    for dest in lam.destinations:
        fn_id = _fn_id(dest.function)
        if not any(e.id == fn_id for e in g.entities()):
            continue
        for label, arn in (("on_success", dest.on_success), ("on_failure", dest.on_failure)):
            if not arn:
                continue
            svc = arn.split(":")[2] if arn.count(":") >= 3 else ""
            kind = _DEST_KINDS.get(svc)
            if kind is None:
                continue
            ident = arn.rsplit(":", 1)[-1].rsplit("/", 1)[-1] or arn
            domain = "stepfunctions" if svc == "states" else svc
            tgt = _e(kind, domain, ident, dest.file, dest.line)
            g.add_entity(tgt)
            g.add_relationship(
                Relationship(
                    src=fn_id,
                    dst=tgt.id,
                    kind=R.INVOKES,
                    evidence_kind=_CFG,
                    attrs=(("destination", label),),
                )
            )

    athena = athena_model(ctx)
    for w in athena.workgroups:
        g.add_entity(_e(K.COMPUTE_JOB, "athena", w.name, w.file, w.line, engine=w.engine_version))
    for q in athena.named_queries:
        qe = _e(K.QUERY, "athena", q.name, q.file, q.line)
        g.add_entity(qe)
        if q.workgroup:
            tail = q.workgroup.rsplit(".", 1)[-1]
            wg_id = f"compute_job:athena:{tail}"
            if any(e.id == wg_id for e in g.entities()):
                g.add_relationship(
                    Relationship(src=qe.id, dst=wg_id, kind=R.DEPENDS_ON, evidence_kind=_CFG)
                )
    for c in athena.catalogs:
        g.add_entity(_e(K.CATALOG, "athena", c.name, c.file, c.line, type=c.type))
    for d in athena.databases:
        g.add_entity(_e(K.CATALOG, "glue", d))


def build_platform_graph(ctx: ProjectContext) -> DataPlatformGraph:
    """Fuse every domain model into one canonical graph (memoized)."""
    cached = getattr(ctx, _CACHE_ATTR, None)
    if cached is not None:
        return cast(DataPlatformGraph, cached)
    graph = DataPlatformGraph()
    for adapter in (
        _airflow,
        _controlm,
        _stepfunctions,
        _streaming,
        _graph_intel,
        _dynamodb,
        _neptune,
        _lakeformation,
        _platforms,
        _serverless,
        _sql,
        _iceberg,
        _parquet,
        _terraform,
    ):
        adapter(ctx, graph)
    setattr(ctx, _CACHE_ATTR, graph)
    return graph


# Impact direction per relationship kind (blast-radius policy - lives in
# the consumer layer, not the graph core).
#
# - Dependency-oriented edges point dependent -> dependency, so a changed
#   *target* impacts dependents via INBOUND traversal: DEPENDS_ON (task b
#   depends on task a - changing a breaks b), READS (reader depends on
#   the table it reads), CONSUMES, STORED_IN (dataset depends on its
#   location).
# - Flow/containment edges carry impact OUTBOUND only: INVOKES (workflow
#   -> its tasks), DEFINES (infra -> defined platform entity), GOVERNS
#   (catalog -> table), TRIGGERS.
# - Data-flow edges (WRITES, PRODUCES) carry impact BOTH ways: a changed
#   table affects the jobs writing it; a changed writer affects the data
#   downstream readers consume.
_IMPACT_INBOUND_ONLY = {R.DEPENDS_ON, R.READS, R.CONSUMES, R.STORED_IN}
_IMPACT_OUTBOUND_ONLY = {R.INVOKES, R.DEFINES, R.GOVERNS, R.TRIGGERS}


def impact_reachable(graph: DataPlatformGraph, entity_id: str) -> set[str]:
    """Entities impacted if ``entity_id`` changes, following each edge in
    its impact direction - not plain structural reachability."""
    seen: set[str] = set()
    frontier = [entity_id]
    while frontier:
        cur = frontier.pop()
        if cur in seen:
            continue
        seen.add(cur)
        out_hits = {r.dst for r in graph.outbound(cur) if r.kind not in _IMPACT_INBOUND_ONLY}
        in_hits = {r.src for r in graph.inbound(cur) if r.kind not in _IMPACT_OUTBOUND_ONLY}
        frontier.extend((out_hits | in_hits) - seen)
    return seen - {entity_id}
