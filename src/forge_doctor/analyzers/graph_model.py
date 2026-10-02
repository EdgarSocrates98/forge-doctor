"""Graph semantic model - paradigm-aware, evidence-only.

Separates the two paradigms the platform mixes: property graph
(vertices/edges, both carry properties) vs RDF (subject/predicate/
object triples, Neptune quads). Detection sources: query files by
extension (``.gremlin``/``.cypher``/``.sparql``/RDF data), Neptune
bulk-load CSV headers, and Python call sites (Gremlin ``g.V()`` chains
plus query strings handed to client methods). Static only - no engine
calls, no executed code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

from forge_doctor.analyzers.graph_queries import (
    _GREMLIN_TEXT_RE,
    GraphTraversal,
    looks_like_gremlin_text,
    looks_like_opencypher,
    looks_like_sparql,
    parse_gremlin_chain,
    parse_gremlin_text,
    parse_opencypher,
    parse_sparql,
)

if TYPE_CHECKING:
    from forge_doctor.analyzers.index import CallSite
    from forge_doctor.core.context import ProjectContext

_CACHE_ATTR = "_forge_doctor_graph_model"

_PARADIGM_BY_LANG = {
    "gremlin": "property_graph",
    "opencypher": "property_graph",
    "sparql": "rdf",
    "rdf_data": "rdf",
    "bulk_load": "property_graph",
}

_EXT_LANGUAGE = {
    ".gremlin": "gremlin",
    ".cypher": "opencypher",
    ".cql": "opencypher",
    ".opencypher": "opencypher",
    ".sparql": "sparql",
    ".rq": "sparql",
}

_EXT_RDF_DATA = {".ttl", ".nt", ".n3", ".rdf"}

_GREMLIN_ROOT_RE = re.compile(r"\b\w+\.(V|E|addV|addE|inject|withSideEffect)\b")

# Neptune bulk-load CSV column markers (property-graph format).
_BULK_MARKERS = {"~id", "~from", "~to", "~label"}

# Steps that prove a chain is a traversal even on an aliased root.
_TRAVERSAL_VOCAB = {
    "has",
    "hasLabel",
    "hasId",
    "out",
    "in",
    "both",
    "outE",
    "inE",
    "bothE",
    "repeat",
    "times",
    "until",
    "emit",
    "valueMap",
    "values",
    "property",
    "path",
    "dedup",
    "where",
    "select",
    "project",
    "fold",
    "unfold",
}


@dataclass(frozen=True)
class GraphWorkload:
    """One detected graph artifact (query file, data file, bulk load)."""

    kind: str  # query | data | bulk_load | traversal_chain | query_string
    paradigm: str  # property_graph | rdf | unknown
    file: Path
    line: int
    detail: str = ""


@dataclass
class GraphProjectModel:
    """All graph facts in a project; built once per scan."""

    workloads: list[GraphWorkload] = field(default_factory=list)
    traversals: list[GraphTraversal] = field(default_factory=list)
    vertex_labels: dict[str, list[tuple[Path, int]]] = field(default_factory=dict)
    edge_labels: dict[str, list[tuple[Path, int]]] = field(default_factory=dict)
    properties: dict[str, list[tuple[Path, int]]] = field(default_factory=dict)
    predicates: dict[str, list[tuple[Path, int]]] = field(default_factory=dict)
    # (edge label, src label|None, dst label|None, file, line)
    edge_endpoints: list[tuple[str, str | None, str | None, Path, int]] = field(
        default_factory=list
    )

    @property
    def has_graph(self) -> bool:
        return bool(self.workloads or self.traversals)

    @property
    def languages(self) -> set[str]:
        return {t.language for t in self.traversals} | {
            w.detail for w in self.workloads if w.kind == "query"
        }

    @property
    def paradigms(self) -> set[str]:
        out = {_PARADIGM_BY_LANG.get(lang, "unknown") for lang in self.languages}
        out |= {w.paradigm for w in self.workloads}
        return out - {"unknown"} if len(out) > 1 else out

    @property
    def writes(self) -> list[GraphTraversal]:
        return [t for t in self.traversals if t.writes]


def _bulk_csv_kind(header: str) -> str | None:
    cols = {c.strip().lower() for c in header.split(",")}
    if not cols & _BULK_MARKERS:
        return None
    return "edge" if "~from" in cols and "~to" in cols else "vertex"


def _file_workloads(ctx: ProjectContext, relative: Path) -> list[GraphWorkload]:
    suffix = relative.suffix.lower()
    out: list[GraphWorkload] = []
    if suffix in _EXT_LANGUAGE:
        out.append(
            GraphWorkload(
                kind="query",
                paradigm=_PARADIGM_BY_LANG[_EXT_LANGUAGE[suffix]],
                file=relative,
                line=1,
                detail=_EXT_LANGUAGE[suffix],
            )
        )
    elif suffix in _EXT_RDF_DATA:
        out.append(
            GraphWorkload(kind="data", paradigm="rdf", file=relative, line=1, detail="rdf_data")
        )
    elif suffix == ".csv":
        text = ctx.read_text(relative, limit=4096)
        if text:
            kind = _bulk_csv_kind(text.splitlines()[0] if text.splitlines() else "")
            if kind:
                out.append(
                    GraphWorkload(
                        kind="bulk_load",
                        paradigm="property_graph",
                        file=relative,
                        line=1,
                        detail=f"neptune_csv_{kind}",
                    )
                )
    return out


def _file_traversals(ctx: ProjectContext, relative: Path) -> list[GraphTraversal]:
    """Parse query/data files into traversal shapes."""
    suffix = relative.suffix.lower()
    text = ctx.read_text(relative, limit=200_000)
    if text is None:
        return []
    out: list[GraphTraversal] = []
    if suffix == ".gremlin":
        for m in _GREMLIN_TEXT_RE.finditer(text):
            start_line = text[: m.start()].count("\n") + 1
            parsed = parse_gremlin_text(m.group(1), relative, start_line)
            if parsed.parsed:
                out.append(parsed)
            else:
                out.append(
                    GraphTraversal(
                        language="gremlin",
                        file=relative,
                        line=start_line,
                        start="",
                        start_selective=False,
                        parsed=False,
                        raw=m.group(1)[:200],
                    )
                )
    elif suffix in {".cypher", ".cql", ".opencypher"}:
        cursor = 0
        for piece in re.split(r";", text):
            if looks_like_opencypher(piece):
                out.append(
                    parse_opencypher(
                        piece,
                        relative,
                        text[:cursor].count("\n") + 1,
                    )
                )
            cursor += len(piece) + 1
    elif suffix in {".sparql", ".rq"} and looks_like_sparql(text):
        out.append(parse_sparql(text, relative, 1))
    return out


def _site_step(site: CallSite) -> str:
    """Case-preserved step name from a call site's dotted chain head."""
    head = site.dotted.split("(", 1)[0]
    return head.rsplit(".", 1)[-1] or site.name


def _site_traversals(ctx: ProjectContext) -> list[GraphTraversal]:
    """Gremlin chains + query strings discovered through the call index."""
    from forge_doctor.analyzers.index import project_index

    index = project_index(ctx)
    out: list[GraphTraversal] = []
    for relative, module in sorted(index.modules.items(), key=lambda kv: kv[0].as_posix()):
        # Gremlin chains: group same-line call sites; the longest dotted
        # string on a line holds the whole chain (inside-out). Anonymous
        # ``__.<step>(...)`` calls (repeat/until bodies) feed args only.
        chains: dict[int, list[CallSite]] = {}
        anon: dict[int, list[CallSite]] = {}
        for site in module.calls:
            if _GREMLIN_ROOT_RE.search(site.dotted) or re.match(
                r"^\w+\.(V|E|addV|addE)$", site.dotted
            ):
                chains.setdefault(site.line, []).append(site)
            elif site.dotted.startswith("__."):
                anon.setdefault(site.line, []).append(site)
        for line, sites in sorted(chains.items()):
            sites.sort(key=lambda s: len(s.dotted), reverse=True)
            head = sites[0]
            args_by_step: dict[str, list[tuple[str, ...]]] = {}
            for site in sites + anon.get(line, []):
                step_name = _site_step(site)
                args_by_step.setdefault(step_name, []).append(site.args)
            line_text = ""
            text = ctx.read_text(relative)
            if text is not None:
                lines = text.splitlines()
                if line <= len(lines):
                    line_text = lines[line - 1]
            trav = parse_gremlin_chain(head.dotted, args_by_step, relative, line, raw=line_text)
            # Honesty guard: a lone ``x.V()`` is only a traversal when the
            # root is a known traversal alias or real steps follow.
            root = trav.start.split(".", 1)[0]
            if root not in {"g", "gt", "t", "__"} and not (
                len(trav.steps) > 1 and any(s in _TRAVERSAL_VOCAB for s in trav.steps[1:])
            ):
                continue
            out.append(trav)
        # Query strings handed to client calls.
        for site in module.calls:
            for arg in site.args:
                snippet = arg.strip()
                if len(snippet) < 6:
                    continue
                if looks_like_gremlin_text(snippet):
                    out.append(parse_gremlin_text(snippet, relative, site.line))
                elif looks_like_opencypher(snippet):
                    out.append(parse_opencypher(snippet, relative, site.line))
                elif looks_like_sparql(snippet):
                    out.append(parse_sparql(snippet, relative, site.line))
    return out


def graph_model(ctx: ProjectContext) -> GraphProjectModel:
    """Build (once, memoized on ctx) the project's graph model."""
    cached = getattr(ctx, _CACHE_ATTR, None)
    if cached is not None:
        return cast(GraphProjectModel, cached)

    model = GraphProjectModel()
    for relative in sorted(ctx.files, key=lambda p: p.as_posix()):
        model.workloads.extend(_file_workloads(ctx, relative))
        model.traversals.extend(_file_traversals(ctx, relative))
    model.traversals.extend(_site_traversals(ctx))
    for trav in model.traversals:
        for label in trav.vertex_labels:
            model.vertex_labels.setdefault(label, []).append((trav.file, trav.line))
        for label in trav.edge_labels:
            model.edge_labels.setdefault(label, []).append((trav.file, trav.line))
        for prop in trav.properties:
            model.properties.setdefault(prop, []).append((trav.file, trav.line))
        for pred in trav.predicates:
            model.predicates.setdefault(pred, []).append((trav.file, trav.line))
        for e_label, src, dst in trav.edge_endpoints:
            model.edge_endpoints.append((e_label, src, dst, trav.file, trav.line))
    setattr(ctx, _CACHE_ATTR, model)
    return model
