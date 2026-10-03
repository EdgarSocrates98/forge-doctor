"""Static lineage - dataset reads/writes extracted from the semantic index.

No target code is executed: spark.read.*, write/save/insert calls, spark.sql
strings (tokenized), and Glue ``from_catalog`` calls are resolved from the
indexed call sites. The result is a directed graph of job nodes (modules) and
dataset nodes (tables/paths) with ``reads``/``writes`` edges.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from forge_doctor_data.api import SCHEMA_VERSION

if TYPE_CHECKING:
    from forge_doctor_data.analyzers.index import ProjectIndex, PyModuleIndex
    from forge_doctor_data.core.context import ProjectContext

_READ_METHODS = {"table", "parquet", "csv", "json", "orc", "avro", "text", "load"}
_WRITE_METHODS = {
    "savetable",
    "saveastable",
    "insertinto",
    "writeto",
    "save",
    "parquet",
    "csv",
    "json",
    "orc",
    "avro",
    "text",
    "mode",
}
_CATALOG_WRITE_KWARGS = {"database", "table_name", "table"}

_SQL_READ_RE = re.compile(r"(?i)\b(?:from|join)\s+([a-z_][\w.]*)\b")
_SQL_WRITE_RE = re.compile(
    r"(?i)\b(?:insert\s+(?:into|overwrite)\s+(?:table\s+)?|create\s+table\s+)"
    r"([a-z_][\w.]*)\b"
)

_GRAPH_ATTR = "_fd_lineage_graph"


@dataclass(frozen=True)
class LineageEdge:
    """One read or write relationship."""

    source: str  # dataset name (reads) or job module (writes)
    target: str  # job module (reads) or dataset name (writes)
    kind: str  # "reads" | "writes"
    file: str
    line: int
    detail: str = ""


@dataclass
class LineageGraph:
    """Dataset + job nodes with typed edges; deterministic ordering."""

    jobs: set[str] = field(default_factory=set)
    datasets: set[str] = field(default_factory=set)
    edges: list[LineageEdge] = field(default_factory=list)
    namespace: str = "project"  # project name, used as the OL namespace

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "nodes": {
                "jobs": sorted(self.jobs),
                "datasets": sorted(self.datasets),
            },
            "edges": [
                {
                    "source": e.source,
                    "target": e.target,
                    "type": e.kind,
                    "file": e.file,
                    "line": e.line,
                }
                for e in self.edges
            ],
        }

    def to_dot(self) -> str:
        lines = ["digraph lineage {"]
        for dataset in sorted(self.datasets):
            lines.append(f'  "{dataset}" [shape=box, style=filled, fillcolor="#e8f4fd"];')
        for job in sorted(self.jobs):
            lines.append(f'  "{job}" [shape=ellipse, style=filled, fillcolor="#fff3cd"];')
        for edge in self.edges:
            style = "solid" if edge.kind == "reads" else "bold"
            lines.append(
                f'  "{edge.source}" -> "{edge.target}" [label="{edge.kind}", style={style}];'
            )
        lines.append("}")
        return "\n".join(lines)

    def to_mermaid(self) -> str:
        lines = ["flowchart LR"]
        for dataset in sorted(self.datasets):
            safe = _mm_id(dataset)
            lines.append(f"  {safe}[[{dataset}]]")
        for job in sorted(self.jobs):
            safe = _mm_id(job)
            lines.append(f"  {safe}({job})")
        for edge in self.edges:
            arrow = "-->" if edge.kind == "reads" else "==>"
            lines.append(f"  {_mm_id(edge.source)} {arrow}|{edge.kind}| {_mm_id(edge.target)}")
        return "\n".join(lines)

    def to_openlineage(self) -> dict[str, Any]:
        """Spec-shaped OpenLineage RunEvents - one COMPLETE event per job.

        Deterministic ``run.runId`` (uuid5 of namespace+job) so identical
        projects produce identical events; ``eventTime`` marks when the
        static analysis ran.
        """
        import uuid
        from datetime import UTC, datetime

        producer = "https://github.com/EdgarSocrates98/forge-doctor-data"
        schema_url = "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent"
        jobs: dict[str, dict[str, Any]] = {}
        for edge in self.edges:
            job = edge.target if edge.kind == "reads" else edge.source
            entry = jobs.setdefault(job, {"inputs": set(), "outputs": set()})
            dataset = edge.source if edge.kind == "reads" else edge.target
            entry["inputs" if edge.kind == "reads" else "outputs"].add(dataset)
        event_time = datetime.now(UTC).isoformat()
        events = []
        for job, entry in sorted(jobs.items()):
            run_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{self.namespace}/{job}")
            events.append(
                {
                    "eventType": "COMPLETE",
                    "eventTime": event_time,
                    "producer": producer,
                    "schemaURL": schema_url,
                    "run": {"runId": str(run_id)},
                    "job": {"namespace": self.namespace, "name": job},
                    "inputs": [
                        {"namespace": _dataset_namespace(d, self.namespace), "name": d}
                        for d in sorted(entry["inputs"])
                    ],
                    "outputs": [
                        {"namespace": _dataset_namespace(d, self.namespace), "name": d}
                        for d in sorted(entry["outputs"])
                    ],
                }
            )
        return {"schema_version": SCHEMA_VERSION, "producer": producer, "events": events}


def _mm_id(name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    return safe or "node"


def _dataset_namespace(name: str, default: str) -> str:
    """OL dataset namespace: scheme/authority for URIs, else the job ns."""
    for scheme in ("s3://", "abfss://", "dbfs:", "gs://", "wasbs://"):
        if name.startswith(scheme):
            parts = name.split("/")
            return "/".join(parts[:3]) if scheme.endswith("://") else scheme
    return default


def _dataset_kind(name: str) -> str:
    if "." in name and not name.startswith(("/", "s3://", "abfss://", "dbfs:")):
        return "table"
    if "/" in name or "://" in name:
        return "path"
    return "table"


def _sql_tables(sql: str) -> tuple[set[str], set[str]]:
    reads = {m.group(1) for m in _SQL_READ_RE.finditer(sql)}
    writes = {m.group(1) for m in _SQL_WRITE_RE.finditer(sql)}
    # DROP TABLE, WITH-clause names, and obvious non-tables.
    reads -= {"select", "where", "lateral"}
    return reads - writes, writes


def build_lineage(ctx: ProjectContext) -> LineageGraph:
    """Walk the project index; emit job->dataset edges for reads/writes."""
    from forge_doctor_data.analyzers.index import project_index

    cached = getattr(ctx, _GRAPH_ATTR, None)
    if isinstance(cached, LineageGraph):
        return cached

    index = project_index(ctx)
    project_name = (
        (ctx.pyproject or {}).get("project", {}).get("name")
        or (ctx.pyproject or {}).get("tool", {}).get("poetry", {}).get("name")
        or ctx.root.name
    )
    graph = LineageGraph(namespace=str(project_name))
    for relative, module in sorted(index.modules.items()):
        if not (module.uses_pyspark or module.uses_glue):
            continue
        job = module.module
        graph.jobs.add(job)
        _module_edges(index, module, relative, job, graph)
    setattr(ctx, _GRAPH_ATTR, graph)
    return graph


def _module_edges(
    index: ProjectIndex,
    module: PyModuleIndex,
    relative: Path,
    job: str,
    graph: LineageGraph,
) -> None:
    file = relative.as_posix()
    for call in module.calls:
        dotted = call.dotted.lower()
        name = call.name
        # spark.read.<fmt>(...) / spark.read.format("x").load(path)
        if ("spark.read" in dotted or dotted.endswith("read.load")) and (
            name in _READ_METHODS or dotted.endswith(".load")
        ):
            for arg in call.args[:1]:
                graph.datasets.add(arg)
                graph.edges.append(LineageEdge(arg, job, "reads", file, call.line, detail=name))
            continue
        if name == "sql" and call.args:
            reads, writes = _sql_tables(call.args[0])
            for table in sorted(reads):
                graph.datasets.add(table)
                graph.edges.append(LineageEdge(table, job, "reads", file, call.line, detail="sql"))
            for table in sorted(writes):
                graph.datasets.add(table)
                graph.edges.append(LineageEdge(job, table, "writes", file, call.line, detail="sql"))
            continue
        # df.write.saveAsTable / insertInto / writeTo / write.<fmt>(path)
        if name in {"savetable", "saveastable", "insertinto", "writeto"}:
            for arg in call.args[:1]:
                graph.datasets.add(arg)
                graph.edges.append(LineageEdge(job, arg, "writes", file, call.line, detail=name))
            continue
        if (
            name in _WRITE_METHODS
            and (".write" in dotted or dotted.endswith(".save"))
            and call.args
        ):
            target = call.args[0]
            if "." in dotted and name not in {"save"}:  # write.<fmt>(path)
                graph.datasets.add(target)
                graph.edges.append(LineageEdge(job, target, "writes", file, call.line, detail=name))
            continue
        # Glue: create_dynamic_frame.from_catalog(database=..., table_name=...)
        if name == "from_catalog" or dotted.endswith("from_catalog"):
            kwargs = dict(call.kwargs)
            dataset = f"{kwargs.get('database', '?')}.{kwargs.get('table_name', '?')}"
            graph.datasets.add(dataset)
            graph.edges.append(LineageEdge(dataset, job, "reads", file, call.line, detail="glue"))
