"""Neptune query analyzers: per-language wrappers over graph_queries.

Phase 2's ``graph_queries`` module is the single engine-agnostic shape
extractor; this module adds the Neptune-facing analyzer objects the spec
names (``GremlinAnalyzer``/``OpenCypherAnalyzer``/``SPARQLAnalyzer``) and
per-project collection. Records are ``GraphTraversal`` instances - the
175 traversal model consumes them unchanged.

Dynamic query strings (built at runtime, passed as variables) are never
reconstructed: they surface only when the literal text is observable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from forge_doctor.analyzers.graph_queries import (
    _GREMLIN_TEXT_RE,
    GraphTraversal,
    looks_like_gremlin_text,
    looks_like_opencypher,
    looks_like_sparql,
    parse_gremlin_text,
    parse_opencypher,
    parse_sparql,
)

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext

_CACHE_ATTR = "_forge_doctor_neptune_queries"

_GREMLIN_EXTS = {".gremlin"}
_CYPHER_EXTS = {".cypher", ".cql", ".opencypher"}
_SPARQL_EXTS = {".sparql", ".rq"}


class GremlinAnalyzer:
    """Gremlin traversal shapes from ``*.gremlin`` files and strings."""

    language = "gremlin"

    def analyze_text(self, text: str, file: Path, line: int = 1) -> list[GraphTraversal]:
        out: list[GraphTraversal] = []
        for m in _GREMLIN_TEXT_RE.finditer(text):
            start_line = line + text[: m.start()].count("\n")
            out.append(parse_gremlin_text(m.group(1), file, start_line))
        if not out and looks_like_gremlin_text(text):
            out.append(parse_gremlin_text(text, file, line))
        return out


class OpenCypherAnalyzer:
    """openCypher shapes from ``*.cypher``/``*.cql`` files and strings."""

    language = "opencypher"

    def analyze_text(self, text: str, file: Path, line: int = 1) -> list[GraphTraversal]:
        out: list[GraphTraversal] = []
        cursor = 0
        for piece in re.split(r";", text):
            if looks_like_opencypher(piece):
                out.append(parse_opencypher(piece, file, line + text[:cursor].count("\n")))
            cursor += len(piece) + 1
        return out


class SPARQLAnalyzer:
    """SPARQL shapes from ``*.sparql``/``*.rq`` files and strings."""

    language = "sparql"

    def analyze_text(self, text: str, file: Path, line: int = 1) -> list[GraphTraversal]:
        if not looks_like_sparql(text):
            return []
        return [parse_sparql(text, file, line)]


_ANALYZERS = {
    "gremlin": GremlinAnalyzer(),
    "opencypher": OpenCypherAnalyzer(),
    "sparql": SPARQLAnalyzer(),
}

_EXT_LANG = {
    **{e: "gremlin" for e in _GREMLIN_EXTS},
    **{e: "opencypher" for e in _CYPHER_EXTS},
    **{e: "sparql" for e in _SPARQL_EXTS},
}
ANALYZERS_BY_EXT = {ext: _ANALYZERS[lang] for ext, lang in _EXT_LANG.items()}


@dataclass
class NeptuneQueryReport:
    """All Neptune query shapes observed in a project."""

    queries: list[GraphTraversal]

    @property
    def languages(self) -> set[str]:
        return {q.language for q in self.queries}

    @property
    def unparsed(self) -> list[GraphTraversal]:
        return [q for q in self.queries if not q.parsed]


def neptune_queries(ctx: ProjectContext) -> NeptuneQueryReport:
    """Neptune query shapes = the shared graph model's traversals.

    ``graph_model`` already extracts file queries, ``g.V()`` call-site
    chains, and literal query strings - reusing it keeps the Neptune
    view consistent with the graph domain and parses nothing twice.
    """
    cached = getattr(ctx, _CACHE_ATTR, None)
    if cached is not None:
        return cast(NeptuneQueryReport, cached)
    from forge_doctor.analyzers.graph_model import graph_model

    queries = list(graph_model(ctx).traversals)
    # Honest failure surface: a query-language file with content but no
    # parsed traversal is malformed input, not silence.
    covered = {q.file for q in queries}
    for relative in sorted(ctx.files, key=lambda p: p.as_posix()):
        if relative.suffix.lower() not in _EXT_LANG or relative in covered:
            continue
        text = ctx.read_text(relative, limit=200_000)
        if text is None or not text.strip():
            continue
        lang = _EXT_LANG[relative.suffix.lower()]
        queries.append(
            GraphTraversal(
                language=lang,
                file=relative,
                line=1,
                start="",
                start_selective=False,
                parsed=False,
                raw=text.strip()[:200],
            )
        )
    report = NeptuneQueryReport(queries=queries)
    setattr(ctx, _CACHE_ATTR, report)
    return report
