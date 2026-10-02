"""GRAPH### checks over GraphProjectModel - modeling + traversal families.

Heuristics stay honest: MEDIUM/LOW confidence, never ERROR. The model
reports structure and traversal shape - never estimated cost (no
cardinalities without runtime data) and never "use a graph database".

Numbering follows spec 174/175: 001-010 modeling, 020-025 traversal.
GRAPH026 and GRAPH030 are local extensions beyond the spec'd set.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge_doctor.analyzers.graph_model import (
    GraphProjectModel,
    graph_model,
)
from forge_doctor.core.models import Confidence, EvidenceKind, Severity
from forge_doctor.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from pathlib import Path

    from forge_doctor.analyzers.graph_queries import GraphTraversal
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.models import CheckResult


def _model(ctx: ProjectContext) -> GraphProjectModel:
    return graph_model(ctx)


class _GraphCheck(CheckBase):
    category = "graph"
    # Graph facts are STATIC (parsed queries/files); correlations are DERIVED.


_GENERIC_EDGES = {
    "RELATED",
    "LINKS",
    "LINKED",
    "CONNECTED_TO",
    "EDGE",
    "REL",
    "RELATIONSHIP",
    "HAS",
    "ASSOC",
    "CONNECTED",
}


class GraphUsage(_GraphCheck):
    """GRAPH001 anchor: how much of the project touches graph workloads."""

    id = "GRAPH001"
    title = "Graph workload detected"
    why = "Anchor: sizes the graph surface feeding the other GRAPH checks."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the counts to size the surface."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.has_graph:
            return [self.result(Severity.PASS, "no graph workloads detected")]
        langs = ",".join(sorted(model.languages)) or "none"
        paradigms = ",".join(sorted(model.paradigms)) or "unknown"
        return [
            self.result(
                Severity.INFO,
                f"{len(model.traversals)} traversals, {len(model.workloads)} "
                f"graph artifacts; languages={langs}; paradigms={paradigms}; "
                f"{len(model.vertex_labels)} vertex labels, "
                f"{len(model.edge_labels)} edge labels",
            )
        ]


class DisconnectedComponents(_GraphCheck):
    """GRAPH002: vertex labels cluster into disconnected components."""

    id = "GRAPH002"
    title = "Disconnected graph components"
    why = "Vertex labels that never share a traversal/edge form separate "
    "components; either the model is split or linking edges are missing."
    when_ok = "Vertex types connect into one component, or the split is "
    "documented."
    fix = "Confirm the component split is intentional; add linking edges "
    "or document the boundary."
    confidence = Confidence.LOW
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        labels = set(model.vertex_labels)
        if len(labels) < 2:
            return []
        # union-find over vertex labels joined by shared traversals/edges
        parent = {v: v for v in labels}

        def find(v: str) -> str:
            while parent[v] != v:
                parent[v] = parent[parent[v]]
                v = parent[v]
            return v

        def union(a: str | None, b: str | None) -> None:
            if a and b and a in parent and b in parent:
                parent[find(a)] = find(b)

        for _, src, dst, _f, _l in model.edge_endpoints:
            union(src, dst)
        for t in model.traversals:
            vlabels = [v for v in t.vertex_labels]
            for v in vlabels[1:]:
                union(vlabels[0], v)
        components = {find(v) for v in labels}
        if len(components) < 2:
            return []
        return [
            self.result(
                Severity.INFO,
                f"{len(labels)} vertex labels form {len(components)} "
                "disconnected components by traversal evidence - either the "
                "model splits or linking edges were not found",
            )
        ]


class OrphanVertex(_GraphCheck):
    """GRAPH003: vertex label never touched by any edge/traversal."""

    id = "GRAPH003"
    title = "Likely orphan vertex type"
    why = "A declared vertex label with no adjacent relationship may be "
    "dead schema or a traversal gap."
    when_ok = "Every vertex type participates in at least one edge type."
    fix = "Confirm the label is intentional; remove or wire it up."
    confidence = Confidence.LOW

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.edge_labels:
            return []
        results = []
        for label, sites in sorted(model.vertex_labels.items()):
            adjacency = any(label in t.vertex_labels and t.edge_labels for t in model.traversals)
            if not adjacency:
                file, line = sites[0]
                results.append(
                    self.result(
                        Severity.INFO,
                        f"vertex label {label!r} never appears alongside an "
                        "edge type in any traversal",
                        file=file,
                        line=line,
                        evidence_kind=EvidenceKind.DERIVED,
                    )
                )
        return results


class UndefinedVertexType(_GraphCheck):
    """GRAPH004: edge traversed with unlabeled (undefined) vertex endpoints."""

    id = "GRAPH004"
    title = "Edge references undefined vertex type"
    why = "An edge type traversed between anonymous/unlabeled vertices "
    "leaves the endpoint types undefined - the schema link can't be "
    "verified statically."
    when_ok = "Edges traverse labeled vertex types, or schema lives in "
    "bulk-load artifacts."
    fix = "Label the endpoint vertex types, or declare the schema."
    confidence = Confidence.LOW

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.vertex_labels:
            return []  # no vertex-type evidence at all - nothing to compare
        results = []
        seen: set[str] = set()
        for e_label, src, dst, file, line in model.edge_endpoints:
            if e_label in seen or (src and dst):
                continue
            seen.add(e_label)
            results.append(
                self.result(
                    Severity.INFO,
                    f"edge type {e_label!r} is traversed without a vertex "
                    "type on at least one endpoint",
                    file=file,
                    line=line,
                    evidence_kind=EvidenceKind.DERIVED,
                )
            )
        return results


class DirectionInconsistency(_GraphCheck):
    """GRAPH005: same edge type traversed in opposite directions."""

    id = "GRAPH005"
    title = "Inconsistent relationship direction"
    why = "The same edge label traversed both ways can mean the write side "
    "or a query has the direction wrong."
    when_ok = "Each relationship type has a consistent canonical direction."
    fix = "Confirm direction at write time; fix the reversed traversal."
    confidence = Confidence.LOW

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        dirs: dict[str, set[str]] = {}
        for t in model.traversals:
            for label in t.edge_labels:
                dirs.setdefault(label, set()).update(t.directions)
        return [
            self.result(
                Severity.INFO,
                f"edge type {label!r} is traversed in both directions; "
                "confirm the canonical direction is intentional",
                evidence_kind=EvidenceKind.DERIVED,
            )
            for label, d in sorted(dirs.items())
            if "out" in d and "in" in d
        ]


class RedundantRelationship(_GraphCheck):
    """GRAPH006: a relationship modeled both as edge type and vertex label."""

    id = "GRAPH006"
    title = "Relationship represented redundantly"
    why = "The same label as an edge type and a vertex label duplicates "
    "the relationship; readers can't tell which is canonical."
    when_ok = "A relationship is either an edge type or a property, not both."
    fix = "Pick one representation (edge for traversal, property for data)."
    confidence = Confidence.LOW

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        shared = set(model.edge_labels) & set(model.vertex_labels)
        return [
            self.result(
                Severity.INFO,
                f"{label!r} appears as both an edge type and a vertex label",
                evidence_kind=EvidenceKind.DERIVED,
            )
            for label in sorted(shared)
        ]


class GenericRelationship(_GraphCheck):
    """GRAPH007: relationship type too generic to be meaningful."""

    id = "GRAPH007"
    title = "Overly generic relationship type"
    why = "Labels like RELATED/LINKS carry no domain meaning and make "
    "traversal intent impossible to review."
    when_ok = "Edge labels name the domain relationship (WORKS_AT, OWNS)."
    fix = "Rename the edge type to the real relationship."
    confidence = Confidence.MEDIUM

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.INFO,
                f"edge type {label!r} is too generic to describe the relationship",
                file=sites[0][0],
                line=sites[0][1],
            )
            for label, sites in sorted(_model(ctx).edge_labels.items())
            if label.upper() in _GENERIC_EDGES
        ]


class PropertyFanout(_GraphCheck):
    """GRAPH008: many distinct property keys on graph elements (INFO-gated).

    Property fan-out is data-dependent - statically we only observe the
    key vocabulary, never per-vertex cardinalities. This is an INFO hint
    to confirm wide-property modeling is intentional.
    """

    id = "GRAPH008"
    title = "Wide property vocabulary (fan-out candidate)"
    why = "A large distinct property-key vocabulary can mean per-element "
    "fan-out; only runtime data confirms it."
    when_ok = "Property keys stay focused per label, or wide modeling is "
    "intentional."
    fix = "Confirm the wide property vocabulary; split or reify only if "
    "it's real."
    confidence = Confidence.LOW
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if len(model.properties) < 8:
            return []
        return [
            self.result(
                Severity.INFO,
                f"{len(model.properties)} distinct property keys observed "
                "across graph traversals - static fan-out hint only; "
                "cardinality needs runtime data",
            )
        ]


class SupernodePattern(_GraphCheck):
    """GRAPH009: vertex type adjacent to many distinct edge types (INFO).

    Same data caveat as GRAPH008: a statically dense vertex type is a
    supernode *candidate*, not a confirmed hotspot.
    """

    id = "GRAPH009"
    title = "Probable supernode pattern"
    why = "A vertex type touching many distinct edge types is a static "
    "supernode candidate - whether it's a hotspot needs runtime data."
    when_ok = "High-degree hubs are intentional and indexed."
    fix = "Confirm the hub pattern; consider modeling detail on edges."
    confidence = Confidence.LOW
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        adjacency: dict[str, set[str]] = {}
        for t in model.traversals:
            for v in t.vertex_labels:
                adjacency.setdefault(v, set()).update(t.edge_labels)
        return [
            self.result(
                Severity.INFO,
                f"vertex label {v!r} touches {len(edges)} distinct edge "
                "types - static supernode candidate, not a confirmed hotspot",
            )
            for v, edges in sorted(adjacency.items())
            if len(edges) >= 5
        ]


class RelationalShape(_GraphCheck):
    """GRAPH010: graph artifacts exist but nothing traverses them."""

    id = "GRAPH010"
    title = "Graph modeled as relational rows without traversal usage"
    why = "Schema/bulk-load evidence without any traversal suggests the "
    "data is stored as a graph but queried relationally (or not at all)."
    when_ok = "Traversal evidence exists, or artifacts are staged for load."
    fix = "Confirm the artifacts are consumed; no graph engine advice implied."
    confidence = Confidence.LOW
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        has_artifacts = any(w.kind in {"bulk_load", "data"} for w in model.workloads)
        if not has_artifacts or model.traversals:
            return []
        return [
            self.result(
                Severity.INFO,
                "graph bulk-load/data files present but no traversal or query usage was found",
            )
        ]


# --- traversal family (spec 175: GRAPH020-025) -------------------------------


def _traversal_result(
    check: CheckBase, trav: GraphTraversal, sev: Severity, msg: str
) -> CheckResult:
    return check.result(
        sev,
        msg,
        file=trav.file,
        line=trav.line,
        evidence=trav.raw or None,
    )


class UnselectiveStart(_GraphCheck):
    """GRAPH020: traversal starts over the whole graph (g.V(), bare MATCH)."""

    id = "GRAPH020"
    title = "Traversal without selective starting point"
    why = "Starting at every vertex/edge makes cost scale with total graph "
    "size; a label/property/id bound at the start narrows the search."
    when_ok = "Start steps bind a label, property, or id."
    fix = "Add a selective has()/label predicate to the start."
    confidence = Confidence.MEDIUM

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            _traversal_result(
                self,
                t,
                Severity.WARNING,
                f"{t.language} traversal starts unselectively ({t.start}) - "
                "cost scales with total graph size",
            )
            for t in _model(ctx).traversals
            if t.parsed and not t.start_selective and t.start
        ]


class UnboundedResult(_GraphCheck):
    """GRAPH021: multi-hop traversal with no result bound (LIMIT/limit())."""

    id = "GRAPH021"
    title = "Traversal without a result bound"
    why = "A traversal that walks edges without LIMIT/limit()/tail() "
    "returns an unbounded working set; the cost depends on data shape."
    when_ok = "Hops pair with a result bound, or unbounded reads are "
    "intentional (exports, full scans)."
    fix = "Add LIMIT/limit()/tail() or confirm the unbounded read is "
    "intended."
    confidence = Confidence.LOW

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            _traversal_result(
                self,
                t,
                Severity.INFO,
                f"{t.language} traversal walks the graph with no result "
                "bound (LIMIT/limit()/tail() absent)",
            )
            for t in _model(ctx).traversals
            if t.parsed
            and (t.directions or t.steps)
            and not t.result_bounded
            and not t.writes
            and not t.has_variable_length  # GRAPH022 covers that case
        ]


class VariableLengthUnbounded(_GraphCheck):
    """GRAPH022: recursive/variable-length path without a depth bound.

    Covers openCypher ``[*]``/``[*1..]`` (no ``hi``) and Gremlin
    ``repeat()`` without ``times()``/``until()``.
    """

    id = "GRAPH022"
    title = "Recursive/variable-length traversal without depth bound"
    why = "An unbounded [*]/repeat() expansion explores paths of arbitrary "
    "depth - the worst shape for a large graph."
    when_ok = "Variable-length patterns carry an explicit hop bound "
    "([*1..3], times(n), until(...))."
    fix = "Bound the pattern or the loop."
    confidence = Confidence.MEDIUM

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            _traversal_result(
                self,
                t,
                Severity.WARNING,
                f"{t.language} variable-length traversal has no depth bound",
            )
            for t in _model(ctx).traversals
            if t.parsed and t.has_variable_length and t.hop_bounds == "unbounded"
        ]


class HighFanout(_GraphCheck):
    """GRAPH023: multi-hop traversal with no filtering (fan-out risk)."""

    id = "GRAPH023"
    title = "High-fanout traversal risk"
    why = "Three or more hops with no filters is a fan-out candidate; "
    "runtime cost can't be known without explain/profile evidence."
    when_ok = "Multi-hop traversals filter early."
    fix = "Filter earlier or reduce hop count; verify with explain/profile."
    confidence = Confidence.LOW

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            _traversal_result(
                self,
                t,
                Severity.INFO,
                f"{t.language} traversal takes {t.hop_count}+ "
                "hops with no filter - static fan-out risk",
            )
            for t in _model(ctx).traversals
            if t.parsed and t.hop_count >= 3 and not t.filters
        ]


class LateFiltering(_GraphCheck):
    """GRAPH024: first filter applied only after several traversal steps."""

    id = "GRAPH024"
    title = "Late filtering"
    why = "Filtering after the traversal fans out work the filter could "
    "have avoided; earliest-possible predicates keep the working set small."
    when_ok = "Filters sit at or before the first hop."
    fix = "Move the filter to the start step (has()/WHERE) where possible."
    confidence = Confidence.MEDIUM

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            _traversal_result(
                self,
                t,
                Severity.INFO,
                f"{t.language} traversal's first filter lands at step "
                f"{t.first_filter_step} - a static selectivity risk; runtime "
                "cost is not known without explain/profile evidence",
            )
            for t in _model(ctx).traversals
            if t.parsed and t.first_filter_step is not None and t.first_filter_step >= 3
        ]


class RepeatedTraversal(_GraphCheck):
    """GRAPH025: identical traversal shape repeated across call sites."""

    id = "GRAPH025"
    title = "Repeated identical traversal pattern"
    why = "The same traversal shape at multiple call sites is a candidate "
    "for a shared access path (and a hint the shape is load-bearing)."
    when_ok = "Traversal logic is consolidated or the repetition is "
    "intentional."
    fix = "Extract the shared traversal or confirm the copies stay in sync."
    confidence = Confidence.LOW
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        shapes: dict[tuple[str, tuple[str, ...], tuple[str, ...]], set[tuple[Path, int]]] = {}
        for t in model.traversals:
            if not t.parsed:
                continue
            key = (t.language, t.steps, t.edge_labels)
            shapes.setdefault(key, set()).add((t.file, t.line))
        return [
            self.result(
                Severity.INFO,
                f"{lang} traversal shape {list(steps or edges)} repeats at {len(sites)} call sites",
                file=sorted(sites)[0][0],
            )
            for (lang, steps, edges), sites in sorted(shapes.items())
            if len(sites) >= 2
        ]


class FullGraphStarts(_GraphCheck):
    """GRAPH026 (extension): repeated unselective starts project-wide."""

    id = "GRAPH026"
    title = "Excessive full-graph starting traversals"
    why = "Several unselective starts suggest a scan-first query style "
    "that scales with total graph size."
    when_ok = "Most traversals bind a selective start."
    fix = "Review the unselective starts reported by GRAPH020."
    confidence = Confidence.LOW
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        unselective = [
            t for t in _model(ctx).traversals if t.parsed and not t.start_selective and t.start
        ]
        if len(unselective) < 3:
            return []
        return [
            self.result(
                Severity.INFO,
                f"{len(unselective)} traversals start without a selective "
                "bound - review GRAPH020 findings",
            )
        ]


class MixedParadigms(_GraphCheck):
    """GRAPH030 (extension): property-graph and RDF evidence combined."""

    id = "GRAPH030"
    title = "Mixed graph paradigms"
    why = "Gremlin/openCypher query property graphs; SPARQL queries RDF - "
    "mixing without a documented boundary suggests the access expectation "
    "and the stored model may not match."
    when_ok = "One paradigm per store, or an explicit boundary between them."
    fix = "Document the per-store paradigm or split workloads by model."
    confidence = Confidence.LOW

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if model.paradigms != {"property_graph", "rdf"}:
            return []
        by_file: dict[Path, set[str]] = {}
        for t in model.traversals:
            by_file.setdefault(t.file, set()).add(
                "rdf" if t.language == "sparql" else "property_graph"
            )
        mixed = [f for f, p in by_file.items() if len(p) > 1]
        if mixed:
            return [
                self.result(
                    Severity.WARNING,
                    f"{f.as_posix()} mixes property-graph and RDF query "
                    "languages with no explicit boundary",
                    file=f,
                    evidence_kind=EvidenceKind.DERIVED,
                )
                for f in sorted(mixed)
            ]
        return [
            self.result(
                Severity.INFO,
                "project contains both property-graph and RDF evidence; "
                "verify each store/query targets the right paradigm",
                evidence_kind=EvidenceKind.DERIVED,
            )
        ]


CHECKS: list[Check] = [
    GraphUsage(),
    DisconnectedComponents(),
    OrphanVertex(),
    UndefinedVertexType(),
    DirectionInconsistency(),
    RedundantRelationship(),
    GenericRelationship(),
    PropertyFanout(),
    SupernodePattern(),
    RelationalShape(),
    UnselectiveStart(),
    UnboundedResult(),
    VariableLengthUnbounded(),
    HighFanout(),
    LateFiltering(),
    RepeatedTraversal(),
    FullGraphStarts(),
    MixedParadigms(),
]
