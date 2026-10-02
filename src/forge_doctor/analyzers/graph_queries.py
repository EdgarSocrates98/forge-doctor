"""Graph query analyzers: openCypher, Gremlin, SPARQL shape extraction.

Shared by the graph model (phase 2) and the Neptune domain (phase 4) -
deliberately engine-agnostic. These are shape extractors, not full
parsers: anything unrecognized degrades to ``parsed=False`` honestly
rather than reconstructing by heuristic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GraphTraversal:
    """One query/traversal's statically observable shape."""

    language: str  # gremlin | opencypher | sparql
    file: Path
    line: int
    start: str  # first step/pattern summary, "" when unknown
    start_selective: bool  # label/property/predicate bound at the start
    steps: tuple[str, ...] = ()
    filters: tuple[str, ...] = ()
    first_filter_step: int | None = None  # index into steps, None = none
    directions: tuple[str, ...] = ()
    hop_count: int = 0  # edge/relationship steps observed (pre-dedup)
    has_variable_length: bool = False
    hop_bounds: str = ""  # "unbounded" | "bounded" | "1..3" | ""
    result_bounded: bool = False  # LIMIT / limit() / tail() present
    projection_size: int | None = None
    projection_star: bool = False
    writes: bool = False
    vertex_labels: tuple[str, ...] = ()
    edge_labels: tuple[str, ...] = ()
    properties: tuple[str, ...] = ()
    # (edge label, src vertex label, dst vertex label); None = unlabeled
    edge_endpoints: tuple[tuple[str, str | None, str | None], ...] = ()
    predicates: tuple[str, ...] = ()
    parsed: bool = True
    raw: str = ""


# --------------------------------------------------------------------------
# openCypher
# --------------------------------------------------------------------------

_CYPHER_CLAUSE = re.compile(
    r"\b(OPTIONAL\s+MATCH|MATCH|CREATE|MERGE|DETACH\s+DELETE|DELETE|SET|"
    r"REMOVE|RETURN|WHERE|WITH|UNWIND|CALL)\b",
    re.I,
)
_NODE_RE = re.compile(r"\(\s*(\w+)?\s*(?::\s*([\w$`]+))?\s*(?:\{([^}]*)\})?\s*\)")
# ``{key: v, key2: v}`` map keys on node/rel patterns; ``var.prop`` accesses.
_CYPHER_MAPKEY_RE = re.compile(r"(?:\{|,)\s*(\w+)\s*:")
_CYPHER_PROP_RE = re.compile(r"\b\w+\.(\w+)")
# Relationship pattern: ``-[v:TYPE*lo..hi]->`` / ``<-[...]-`` / ``-[...]-``.
# groups: 1=leading arrow, 2=var, 3=type(s), 4=hop-min, 5=hop-max, 6=trailing.
_REL_RE = re.compile(
    r"(<-|-)\[\s*(\w+)?\s*(?::\s*([\w$`|]+))?\s*"
    r"(?:\*\s*(\d+)?(?:\s*\.\.\s*(\d+)?)?)?[^]]*\](->|-)"
)
_CYPHER_START = re.compile(r"^\s*(?:OPTIONAL\s+MATCH|MATCH|MERGE|CREATE)\s*\(", re.I)


def parse_opencypher(text: str, file: Path, line: int) -> GraphTraversal:
    """Extract traversal shape from an openCypher query string."""
    clauses = {m.group(1).upper() for m in _CYPHER_CLAUSE.finditer(text)}
    if not clauses & {"MATCH", "OPTIONAL MATCH", "MERGE", "CREATE"}:
        return GraphTraversal(
            language="opencypher",
            file=file,
            line=line,
            start="",
            start_selective=False,
            parsed=False,
            raw=text[:200],
        )
    vertex_labels: list[str] = []
    edge_labels: list[str] = []
    edge_endpoints: list[tuple[str, str | None, str | None]] = []
    directions: list[str] = []
    properties: list[str] = []
    hops: list[str] = []
    var_len = False
    unbounded = False
    # Ordered node/rel walk so each edge records its endpoint labels.
    tokens = sorted(
        [(m.start(), "n", m) for m in _NODE_RE.finditer(text)]
        + [(m.start(), "r", m) for m in _REL_RE.finditer(text)]
    )
    prev_label: str | None = None
    pending_rel: re.Match[str] | None = None
    for _, kind, m in tokens:
        if kind == "n":
            label = m.group(2)
            if m.group(3):
                properties.extend(_CYPHER_MAPKEY_RE.findall("{" + m.group(3) + "}"))
            if label:
                vertex_labels.append(label)
            if pending_rel is not None:
                for t in (pending_rel.group(3) or "").split("|"):
                    if t:
                        edge_endpoints.append((t, prev_label, label))
                pending_rel = None
            prev_label = label
        else:
            rel = m
            if rel.group(3):
                edge_labels.extend(t for t in rel.group(3).split("|") if t)
            pending_rel = rel
            if rel.group(1) == "<-" or rel.group(6) == "->":
                directions.append("in" if rel.group(1) == "<-" else "out")
            else:
                directions.append("both")
            if "*" in rel.group(0):
                var_len = True
                lo, hi = rel.group(4), rel.group(5)
                if hi:
                    hops.append(f"{lo or ''}..{hi}")
                else:
                    unbounded = True  # ``[*]`` or ``[*lo..]`` - no upper bound
                    hops.append(f"{lo or ''}..")
    if pending_rel is not None:  # trailing rel with no closing node
        for t in (pending_rel.group(3) or "").split("|"):
            if t:
                edge_endpoints.append((t, prev_label, None))
    properties.extend(_CYPHER_MAPKEY_RE.findall(text))
    properties.extend(_CYPHER_PROP_RE.findall(text))
    start_selective = False
    first_match = re.search(r"\bMATCH\s*(.*?)(?=\bMATCH\b|\bWHERE\b|$)", text, re.I | re.S)
    if first_match:
        segment = first_match.group(1)
        if ":" in segment or "{" in segment or re.search(r"\bWHERE\b", text, re.I):
            start_selective = True
    returns = re.findall(
        r"\bRETURN\s+(.*?)(?=\b(?:ORDER|SKIP|LIMIT|UNION|MATCH|RETURN|OPTIONAL|WITH|CREATE|MERGE|DELETE|SET|REMOVE)\b|$)",
        text,
        re.I | re.S,
    )
    proj_size: int | None = None
    proj_star = False
    if returns:
        body = returns[-1].strip()
        proj_star = body.startswith("*")
        proj_size = 1 if proj_star else len([p for p in body.split(",") if p.strip()])
    steps = tuple(
        sorted(
            {m.group(3) for m in _REL_RE.finditer(text) if m.group(3)},
        )
    )
    return GraphTraversal(
        language="opencypher",
        file=file,
        line=line,
        start=(first_match.group(1).strip()[:80] if first_match else ""),
        start_selective=start_selective,
        steps=steps,
        filters=("WHERE",) if re.search(r"\bWHERE\b", text, re.I) else (),
        directions=tuple(dict.fromkeys(directions)),
        hop_count=len(directions),
        has_variable_length=var_len,
        hop_bounds="unbounded" if unbounded else (",".join(hops) if hops else ""),
        result_bounded=bool(re.search(r"\bLIMIT\b", text, re.I)),
        projection_size=proj_size,
        projection_star=proj_star,
        writes=bool(clauses & {"CREATE", "MERGE", "DELETE", "DETACH DELETE", "SET", "REMOVE"}),
        vertex_labels=tuple(dict.fromkeys(vertex_labels)),
        edge_labels=tuple(dict.fromkeys(edge_labels)),
        properties=tuple(dict.fromkeys(properties)),
        edge_endpoints=tuple(edge_endpoints),
        parsed=True,
        raw=text[:200],
    )


def looks_like_opencypher(text: str) -> bool:
    return bool(_CYPHER_START.search(text)) or (
        re.search(r"\bMATCH\s*\(", text, re.I) is not None
        and re.search(r"\bRETURN\b", text, re.I) is not None
    )


# --------------------------------------------------------------------------
# Gremlin
# --------------------------------------------------------------------------

_GREMLIN_STARTS = ("V", "E", "addV", "addE", "inject", "withSideEffect")
_GREMLIN_FILTERS = {"has", "hasLabel", "hasId", "hasNot", "where", "filter", "is"}
# Python GLV escapes the ``in`` keyword as ``in_`` (also ``as_``/``from_``).
_GREMLIN_DIRS = {
    "out": "out",
    "in": "in",
    "in_": "in",
    "both": "both",
    "outE": "out",
    "inE": "in",
    "inE_": "in",
    "bothE": "both",
}
_GREMLIN_WRITES = {"addV", "addE", "property", "drop", "mergeV", "mergeE"}
_GREMLIN_STEP_RE = re.compile(r"[A-Za-z_]\w*")

# ``dotted`` inside-out chains: out(hasLabel(g.V)) -> unnest to steps.
_NESTED_RE = re.compile(r"^([A-Za-z_]\w*)\((.*)\)$", re.S)


def _unnest_dotted(dotted: str) -> tuple[str, list[str]]:
    """``out(hasLabel(g.V))`` -> ``("g", ["V", "hasLabel", "out"])``."""
    steps: list[str] = []
    core = dotted
    while True:
        m = _NESTED_RE.match(core)
        if not m:
            break
        steps.append(m.group(1))
        core = m.group(2)
    steps.reverse()  # steps now run outermost->...; core is innermost
    parts = core.split(".")
    root = parts[0]
    steps = parts[1:] + steps
    return root, steps


def parse_gremlin_chain(
    dotted: str,
    args_by_step: dict[str, list[tuple[str, ...]]],
    file: Path,
    line: int,
    raw: str = "",
) -> GraphTraversal:
    """Traversal shape from a callsite inside-out chain + per-step args.

    ``args_by_step`` maps step name -> list of arg tuples (one per same-line
    call site); args are consumed in order for repeated step names.
    """
    root, steps = _unnest_dotted(dotted)
    used: dict[str, int] = {}
    vertex_labels: list[str] = []
    edge_labels: list[str] = []
    filters: list[str] = []
    directions: list[str] = []
    writes = False
    first_filter: int | None = None
    hop_bounds = ""
    var_len = False
    bounded_recursion = False
    limit_seen = False

    def args_for(step: str) -> tuple[str, ...]:
        idx = used.get(step, 0)
        pool = args_by_step.get(step, [])
        if idx < len(pool):
            used[step] = idx + 1
            return pool[idx]
        return ()

    start_args: tuple[str, ...] = ()
    properties: list[str] = []
    for i, step in enumerate(steps):
        args = args_for(step)
        low = step.lower()
        if i == 0 and step in {"V", "E"}:
            start_args = args
        if step in _GREMLIN_DIRS:
            directions.append(_GREMLIN_DIRS[step])
            edge_labels.extend(a for a in args)
        if step in {"has", "property", "values", "valueMap", "properties"}:
            properties.extend(a.strip("\"'") for a in args[:1] if a)
        if step in _GREMLIN_FILTERS:
            filters.extend(f"{step}({a})" for a in args)
            if first_filter is None:
                first_filter = i
            if step == "hasLabel":
                vertex_labels.extend(args)
        if step == "addV":
            vertex_labels.extend(args[:1])
            writes = True
        if step == "addE":
            edge_labels.extend(args[:1])
            writes = True
        if step in _GREMLIN_WRITES:
            writes = True
        if step == "repeat":
            var_len = True
        if step == "times":
            bounded_recursion = True
            if args:
                hop_bounds = args[0]
            elif raw:  # times() takes ints - literal args aren't captured
                m = re.search(r"\btimes\s*\(\s*(\d+)", raw)
                if m:
                    hop_bounds = m.group(1)
        if step == "until":
            bounded_recursion = True
        if step in {"limit", "range", "tail"}:
            limit_seen = True
        if low == "values" or step == "valueMap":
            pass
    start_selective = bool(
        start_args
        or (first_filter is not None and first_filter <= 1)
        or (steps and steps[0] in {"addV", "addE"})
    )
    if var_len:
        if not bounded_recursion:
            hop_bounds = "unbounded"
        elif not hop_bounds:
            hop_bounds = "bounded"
    return GraphTraversal(
        language="gremlin",
        file=file,
        line=line,
        start=f"{root}.{steps[0]}()" if steps else root,
        start_selective=start_selective,
        steps=tuple(steps),
        filters=tuple(filters),
        first_filter_step=first_filter,
        directions=tuple(dict.fromkeys(directions)),
        hop_count=len(directions),
        has_variable_length=var_len,
        hop_bounds=hop_bounds,
        result_bounded=limit_seen,
        projection_star=False,
        projection_size=None if not limit_seen else 0,
        writes=writes,
        vertex_labels=tuple(dict.fromkeys(vertex_labels)),
        edge_labels=tuple(dict.fromkeys(edge_labels)),
        properties=tuple(dict.fromkeys(properties)),
        parsed=True,
        raw=raw or dotted[:200],
    )


_GREMLIN_TEXT_RE = re.compile(
    r"(?<![\w.])((?:g|__)\s*\.\s*(?:"
    + "|".join(_GREMLIN_STARTS)
    + r")\s*\([^;\n]*(?:\.\s*\w+\s*\([^()]*\))*)",
    re.S,
)


def parse_gremlin_text(text: str, file: Path, line: int) -> GraphTraversal:
    """Traversal shape from a gremlin *string* (submit-style scripts)."""
    step_re = re.compile(r"\.\s*(\w+)\s*\(([^()]*)\)")
    steps: list[str] = []
    args_by_step: dict[str, list[tuple[str, ...]]] = {}
    start = text.strip()
    m = re.match(r"^(g|__)\s*\.\s*(\w+)\s*\(([^()]*)\)", start)
    root = "g"
    offset = 0
    if m:
        steps.append(m.group(2))
        args_by_step[m.group(2)] = [tuple(a.strip() for a in m.group(3).split(",") if a.strip())]
        root = m.group(1)
        offset = m.end()
    for sm in step_re.finditer(start, offset):
        name, argtext = sm.group(1), sm.group(2)
        steps.append(name)
        args_by_step.setdefault(name, []).append(
            tuple(a.strip() for a in argtext.split(",") if a.strip())
        )
    if not steps:
        return GraphTraversal(
            language="gremlin",
            file=file,
            line=line,
            start="",
            start_selective=False,
            parsed=False,
            raw=text[:200],
        )
    if len(steps) > 1:
        dotted = f"{steps[-1]}(" + ".".join([root, *steps[:-1]]) + ")"
    else:
        dotted = f"{root}.{steps[0]}"
    return parse_gremlin_chain(dotted, args_by_step, file, line, raw=text[:200])


def looks_like_gremlin_text(text: str) -> bool:
    """``g.V(...)``/``__.out(...)`` script prefix."""
    return bool(re.match(r"^\s*(g|__)\s*\.\s*(V|E|addV|addE|inject|withSideEffect)\s*\(", text))


# --------------------------------------------------------------------------
# SPARQL
# --------------------------------------------------------------------------

_SPARQL_FORM = re.compile(r"\b(SELECT|CONSTRUCT|ASK|DESCRIBE|INSERT|DELETE)\b", re.I)
_PREDICATE_RE = re.compile(r"[?\w]+\s+<?([a-zA-Z][\w:.-]*)>?\s+[?\w\"'<]")
_PP_RE = re.compile(r"\b(\w+:\w+|\w+)\s*([*+]|\?\d*)\b")


def parse_sparql(text: str, file: Path, line: int) -> GraphTraversal:
    """Traversal shape from a SPARQL query/update string."""
    forms = {m.group(1).upper() for m in _SPARQL_FORM.finditer(text)}
    if not forms and not re.search(r"\bWHERE\s*\{", text, re.I):
        return GraphTraversal(
            language="sparql",
            file=file,
            line=line,
            start="",
            start_selective=False,
            parsed=False,
            raw=text[:200],
        )
    predicates = tuple(dict.fromkeys(_PREDICATE_RE.findall(text)))
    var_len = bool(re.search(r"[*+](?![\w)])", text))
    proj_size: int | None = None
    proj_star = False
    sel = re.search(r"\bSELECT\s+(.*?)\bWHERE\b", text, re.I | re.S)
    if sel:
        body = sel.group(1).strip()
        proj_star = "*" in body
        proj_size = len(re.findall(r"[?$]\w+", body)) or (1 if proj_star else None)
    filters = re.findall(r"\bFILTER\b", text, re.I)
    writes = bool(forms & {"INSERT", "DELETE"})
    return GraphTraversal(
        language="sparql",
        file=file,
        line=line,
        start=(sorted(forms)[0] if forms else "WHERE"),
        start_selective=bool(re.search(r"[?]\w+\s+<[\w:.-]+>", text)),
        steps=(),
        filters=tuple(filters),
        directions=(),
        hop_count=len(predicates),
        has_variable_length=var_len,
        hop_bounds="unbounded" if var_len else "",
        result_bounded=bool(re.search(r"\bLIMIT\b", text, re.I)),
        projection_size=proj_size,
        projection_star=proj_star,
        writes=writes,
        predicates=predicates,
        parsed=True,
        raw=text[:200],
    )


def looks_like_sparql(text: str) -> bool:
    head = text.lstrip()[:400]
    if re.search(r"\bPREFIX\s+\w+:", head):
        return True
    return bool(
        re.search(r"\b(ASK|CONSTRUCT|DESCRIBE)\b", head)
        or (re.search(r"\bSELECT\s+[?*]", head) and re.search(r"\bWHERE\s*\{", head))
        or re.search(r"\b(INSERT|DELETE)\s+DATA\b", head)
    )
