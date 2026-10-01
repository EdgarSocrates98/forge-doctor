"""Project Intelligence Graph - repo structure, jobs, datasets, IaC, DAGs.

Combines the semantic index (jobs), lineage (datasets + read/write edges),
IaC resources (deploys edges), and Airflow-style orchestrators (triggers
edges) into one graph consumable as JSON/dot/mermaid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext

_AIRFLOW_HINTS = ("from airflow", "import airflow", "@dag", "DAG(")


@dataclass(frozen=True)
class GraphNode:
    id: str
    kind: str  # repo | job | dataset | infra | orchestrator
    label: str
    detail: str = ""


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    kind: str  # reads | writes | deploys | triggers | contains


@dataclass
class ProjectGraph:
    nodes: dict[str, GraphNode] = field(default_factory=dict)
    edges: list[GraphEdge] = field(default_factory=list)

    def add_node(self, node: GraphNode) -> None:
        self.nodes.setdefault(node.id, node)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "nodes": [
                {"id": n.id, "kind": n.kind, "label": n.label, "detail": n.detail}
                for n in sorted(self.nodes.values(), key=lambda n: (n.kind, n.id))
            ],
            "edges": [
                {"source": e.source, "target": e.target, "type": e.kind}
                for e in sorted(self.edges, key=lambda e: (e.kind, e.source, e.target))
            ],
        }

    def to_dot(self) -> str:
        shapes = {
            "repo": "doubleoctagon",
            "job": "ellipse",
            "dataset": "box",
            "infra": "component",
            "orchestrator": "diamond",
        }
        lines = ["digraph intelligence {"]
        for node in sorted(self.nodes.values(), key=lambda n: n.id):
            lines.append(
                f'  "{node.id}" [shape={shapes.get(node.kind, "ellipse")}, label="{node.label}"];'
            )
        for edge in self.edges:
            lines.append(f'  "{edge.source}" -> "{edge.target}" [label="{edge.kind}"];')
        lines.append("}")
        return "\n".join(lines)

    def to_mermaid(self) -> str:
        import re

        def mm(node_id: str) -> str:
            return re.sub(r"[^a-zA-Z0-9_]", "_", node_id) or "n"

        lines = ["flowchart LR"]
        for node in sorted(self.nodes.values(), key=lambda n: n.id):
            shape_open, shape_close = {
                "job": ("(", ")"),
                "dataset": ("[[", "]]"),
                "infra": ("[", "]"),
                "orchestrator": ("{", "}"),
            }.get(node.kind, ("[", "]"))
            lines.append(f'  {mm(node.id)}{shape_open}"{node.label}"{shape_close}')
        for edge in self.edges:
            lines.append(f"  {mm(edge.source)} -->|{edge.kind}| {mm(edge.target)}")
        return "\n".join(lines)


def build_graph(ctx: ProjectContext) -> ProjectGraph:
    """Assemble the intelligence graph from index + lineage + IaC + DAGs."""
    from forge_doctor.analyzers.hcl_lite import project_iac
    from forge_doctor.analyzers.index import project_index
    from forge_doctor.core.lineage import build_lineage

    graph = ProjectGraph()
    repo_id = "repo:" + ctx.root.name
    graph.add_node(GraphNode(repo_id, "repo", ctx.root.name))

    index = project_index(ctx)
    for module in index.modules.values():
        is_job = module.uses_pyspark or module.uses_glue
        if is_job:
            job_id = f"job:{module.module}"
            kinds = [
                k for k, yes in (("spark", module.uses_pyspark), ("glue", module.uses_glue)) if yes
            ]
            graph.add_node(GraphNode(job_id, "job", module.module, detail="+".join(kinds)))
            graph.edges.append(GraphEdge(repo_id, job_id, "contains"))

    # Airflow-style orchestrators: files importing airflow or living in dags/.
    for relative, module in index.modules.items():
        if module.uses_pyspark or module.uses_glue:
            continue
        text = (ctx.read_text(relative) or "")[:8000]
        if "airflow" in text.lower() or "dags" in relative.parts:
            orch_id = f"orch:{module.module}"
            graph.add_node(GraphNode(orch_id, "orchestrator", module.module, detail="airflow-ish"))
            # triggers edges: dag file references a job module stem.
            for other in index.modules.values():
                stem = other.file.stem
                if stem and stem in text and other is not module:
                    job_id = f"job:{other.module}"
                    if job_id in graph.nodes:
                        graph.edges.append(GraphEdge(orch_id, job_id, "triggers"))

    # Datasets via static lineage.
    lineage = build_lineage(ctx)
    for dataset in lineage.datasets:
        graph.add_node(GraphNode(f"ds:{dataset}", "dataset", dataset))
    for edge in lineage.edges:
        if edge.kind == "reads":
            graph.edges.append(GraphEdge(f"ds:{edge.source}", f"job:{edge.target}", "reads"))
        else:
            graph.edges.append(GraphEdge(f"job:{edge.source}", f"ds:{edge.target}", "writes"))

    # IaC resources + deploys edges (resource name contains a job module stem).
    for resource in project_iac(ctx.files, ctx.root):
        infra_id = f"infra:{resource.type}:{resource.name}"
        graph.add_node(
            GraphNode(
                infra_id,
                "infra",
                resource.name,
                detail=resource.type,
            )
        )
        for module in index.modules.values():
            if not (module.uses_pyspark or module.uses_glue):
                continue
            stem = module.file.stem.lower()
            if stem and stem in resource.name.lower():
                graph.edges.append(GraphEdge(infra_id, f"job:{module.module}", "deploys"))
    return graph
