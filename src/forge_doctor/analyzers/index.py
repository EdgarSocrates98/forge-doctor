"""Semantic Analysis Index - each Python file parsed once, queried by all.

The index is the shared substrate the prompt calls "Project Index": imports,
call sites, assignments, symbol ranges, and DataFrame-producer functions per
module, plus a bounded cross-module pass so ``from reader import load_orders``
followed by ``df = load_orders(spark)`` marks ``df`` in the importing file -
lightweight symbol/DataFrame propagation without executing a line.

Every module condenses to JSON-serializable *facts*; the incremental cache
reuses them for unchanged files, so a rescan parses only what changed.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge_doctor.core.cache import ScanCache
    from forge_doctor.core.context import ProjectContext

INDEX_ATTR = "_fd_project_index"

_SESSION_ROOTS = {"spark", "sparksession", "sc", "sparkcontext"}

# Call shapes that produce DataFrames when rooted at a session/df name:
# spark.read.X(...), spark.sql(...), spark.table(...), df.join(...), etc.
_DF_VERBS = {
    "parquet",
    "csv",
    "json",
    "orc",
    "avro",
    "text",
    "load",
    "table",
    "sql",
    "createdataframe",
    "range",
    "readstream",
    "select",
    "filter",
    "join",
    "union",
    "withcolumn",
    "withcolumnrenamed",
    "groupby",
    "agg",
    "transform",
    "alias",
    "distinct",
    "drop",
    "dropna",
    "fillna",
    "limit",
    "orderby",
    "repartition",
    "coalesce",
    "cache",
    "persist",
    "sample",
    "sort",
    "sortwithinpartitions",
    "where",
}


def _looks_like_df_call(dotted: str) -> bool:
    """``spark.read.parquet()`` / ``df.join()`` -> likely a DataFrame result."""
    root = dotted.split(".", 1)[0].lower()
    name = dotted.rsplit(".", 1)[-1].split("(")[0].lower()
    if root in _SESSION_ROOTS:
        return ".read" in dotted or name in _DF_VERBS or ".sql" in dotted
    if "df" in root:
        return name in _DF_VERBS
    return False


@dataclass(frozen=True)
class ImportEntry:
    module: str
    name: str | None  # from-imported symbol, None for `import x`
    asname: str | None
    line: int
    is_from: bool


@dataclass(frozen=True)
class CallSite:
    name: str  # terminal call name, lowercased (``collect``)
    dotted: str  # full dotted func path (``spark.read.parquet``)
    receiver: str | None  # receiver root name for method calls
    line: int
    column: int
    symbol: str | None  # enclosing function/class path
    args: tuple[str, ...] = ()  # string-literal positional args
    kwargs: tuple[tuple[str, str], ...] = ()  # string-literal keyword args


@dataclass(frozen=True)
class AssignEntry:
    target: str
    value_root: str | None  # root name of the value expression
    value_call: str | None  # dotted call path when value is a call
    line: int
    symbol: str | None


@dataclass(frozen=True)
class ReturnFact:
    """Shape of one ``return`` inside a function - facts, not AST."""

    root: str | None = None  # name the returned expression resolves to
    call: str | None = None  # dotted call path when the return is a call


@dataclass(frozen=True)
class FunctionInfo:
    name: str
    qualified: str
    line: int
    end_line: int
    returns_df: bool = False
    returns: tuple[ReturnFact, ...] = ()


@dataclass
class PyModuleIndex:
    """Everything analyzers need from one ``ast.parse`` - or its cache facts."""

    file: Path
    module: str  # dotted module name, best effort (``pkg.job``)
    tree: ast.Module | None
    imports: list[ImportEntry] = field(default_factory=list)
    calls: list[CallSite] = field(default_factory=list)
    assigns: list[AssignEntry] = field(default_factory=list)
    functions: dict[str, FunctionInfo] = field(default_factory=dict)
    classes: dict[str, tuple[int, int]] = field(default_factory=dict)
    df_names: set[str] = field(default_factory=set)
    # Names proven df-ish ONLY by producer propagation (not name heuristics).
    # Recomputed every scan - deliberately not serialized into cache facts.
    propagated_names: set[str] = field(default_factory=set)
    uses_pyspark: bool = False
    uses_glue: bool = False
    spark_buckets: dict[str, list[list[Any]]] | None = None
    glue_buckets: dict[str, list[int]] | None = None
    fresh: bool = True  # False when restored from cache (tree may be None)

    def to_facts(self) -> dict[str, Any]:
        """JSON-serializable form persisted in the incremental cache."""
        return {
            "module": self.module,
            "imports": [[i.module, i.name, i.asname, i.line, i.is_from] for i in self.imports],
            "calls": [
                [
                    c.name,
                    c.dotted,
                    c.receiver,
                    c.line,
                    c.column,
                    c.symbol,
                    list(c.args),
                    list(map(list, c.kwargs)),
                ]
                for c in self.calls
            ],
            "assigns": [
                [a.target, a.value_root, a.value_call, a.line, a.symbol] for a in self.assigns
            ],
            "functions": {
                name: [
                    fn.line,
                    fn.end_line,
                    fn.qualified,
                    fn.returns_df,
                    [[r.root, r.call] for r in fn.returns],
                ]
                for name, fn in self.functions.items()
            },
            "classes": {name: list(span) for name, span in self.classes.items()},
            "df_names": sorted(self.df_names),
            "uses_pyspark": self.uses_pyspark,
            "uses_glue": self.uses_glue,
            "spark_buckets": self.spark_buckets,
            "glue_buckets": self.glue_buckets,
        }

    @classmethod
    def from_facts(cls, file: Path, facts: dict[str, Any]) -> PyModuleIndex:
        index = cls(
            file=file,
            module=str(facts.get("module") or _module_name(file)),
            tree=None,
            fresh=False,
        )
        index.imports = [
            ImportEntry(module=m, name=n, asname=a, line=ln, is_from=f)
            for m, n, a, ln, f in facts.get("imports", [])
        ]
        index.calls = []
        for row in facts.get("calls", []):
            n, d, r, li, c, s = row[:6]
            args = tuple(row[6]) if len(row) > 6 else ()
            kwargs = tuple((k, v) for k, v in row[7]) if len(row) > 7 else ()
            index.calls.append(
                CallSite(
                    name=n,
                    dotted=d,
                    receiver=r,
                    line=li,
                    column=c,
                    symbol=s,
                    args=args,
                    kwargs=kwargs,
                )
            )
        index.assigns = [
            AssignEntry(target=t, value_root=vr, value_call=vc, line=li, symbol=s)
            for t, vr, vc, li, s in facts.get("assigns", [])
        ]
        for name, (line, end, qual, rdf, rets) in facts.get("functions", {}).items():
            index.functions[name] = FunctionInfo(
                # Keys are qualified names; name is the bare identifier.
                name=name.rsplit(".", 1)[-1],
                qualified=qual,
                line=line,
                end_line=end,
                returns_df=rdf,
                returns=tuple(ReturnFact(root=r, call=c) for r, c in rets),
            )
        index.classes = {
            name: (span[0], span[1]) for name, span in facts.get("classes", {}).items()
        }
        index.df_names = set(facts.get("df_names", []))
        index.uses_pyspark = bool(facts.get("uses_pyspark"))
        index.uses_glue = bool(facts.get("uses_glue"))
        index.spark_buckets = facts.get("spark_buckets")
        index.glue_buckets = facts.get("glue_buckets")
        return index


def _export_signature(index: PyModuleIndex) -> str:
    """Hash of a module's exported semantic surface, post-propagation.

    Dependents consume a module through its functions' producer/return
    shapes (``is_producer_call``) and through call-site semantics like
    literal path/table args - so the signature covers functions (qualified
    name, ``returns_df``, return facts) and call sites (name, dotted path,
    receiver, symbol context, literal args) with positions stripped.
    Comment-only or pure formatting edits leave it unchanged; semantic
    edits - including transitive ones inherited from the module's own deps
    via propagation - move it.
    """
    functions = sorted(
        (
            fn.qualified,
            fn.returns_df,
            tuple((r.root or "", r.call or "") for r in fn.returns),
        )
        for fn in index.functions.values()
    )
    calls = sorted(
        (
            c.name,
            c.dotted,
            c.receiver or "",
            c.symbol or "",
            c.args,
            c.kwargs,
        )
        for c in index.calls
    )
    payload = json.dumps(
        {"functions": functions, "calls": calls},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class ProjectIndex:
    """Whole-project view: modules, module lookup, DataFrame producers."""

    def __init__(self, ctx: ProjectContext, cache: ScanCache | None = None) -> None:
        self.modules: dict[Path, PyModuleIndex] = {}
        self._by_module: dict[str, PyModuleIndex] = {}
        py_files = sorted(f for f in ctx.files if f.suffix == ".py")
        # File-path-derived module map: import resolution without parsing.
        self._file_for_module: dict[str, Path] = {}
        for relative in py_files:
            self._file_for_module.setdefault(_module_name(relative), relative)
            self._file_for_module.setdefault(relative.stem, relative)
        self._shas: dict[Path, str | None] = {}
        # Entries of modules served from cache; dep signatures are validated
        # only after propagation - a dep's semantic state isn't final before.
        self._restored: dict[Path, dict[str, Any]] = {}
        if cache is not None:
            self._shas = {relative: cache.sha256(relative) for relative in py_files}
        for relative in py_files:
            index = self._module(ctx, cache, relative)
            self.modules[relative] = index
            self._by_module[index.module] = index
            self._by_module.setdefault(relative.stem, index)
        self._propagate_producers()
        if cache is not None:
            self._fix_stale(ctx, cache)
            self._record_dep_sigs(cache)

    def _dep_files(self, index: PyModuleIndex) -> dict[str, Path | None]:
        """Module name -> project file each import resolves to (or None)."""
        deps: dict[str, Path | None] = {}
        for entry in index.imports:
            target = self._file_for_module.get(entry.module) or self._file_for_module.get(
                entry.module.rsplit(".", 1)[-1]
            )
            deps[entry.module] = target
        return deps

    def _record_dep_sigs(self, cache: ScanCache) -> None:
        """Persist each module's dep resolution + dep export signatures."""
        for relative, index in self.modules.items():
            dep_sigs: dict[str, dict[str, Any]] = {}
            for modname, dep_file in self._dep_files(index).items():
                if dep_file is None:
                    dep_sigs[modname] = {"file": None, "sig": ""}
                    continue
                dep_index = self.modules.get(dep_file)
                dep_sigs[modname] = {
                    "file": dep_file.as_posix(),
                    "sig": _export_signature(dep_index) if dep_index is not None else "",
                }
            cache.set_dep_sigs(relative, dep_sigs)

    def _dep_sigs_current(self, index: PyModuleIndex, entry: dict[str, Any]) -> bool:
        """Cached facts stay valid only while dep resolution + signatures hold."""
        recorded = entry.get("dep_sigs")
        if not isinstance(recorded, dict):
            return False  # no provenance recorded - can't trust buckets
        for modname, dep_file in self._dep_files(index).items():
            rec = recorded.get(modname)
            expected_file = dep_file.as_posix() if dep_file is not None else None
            if not isinstance(rec, dict) or rec.get("file") != expected_file:
                return False
            if dep_file is not None:
                dep_index = self.modules.get(dep_file)
                if dep_index is None or rec.get("sig") != _export_signature(dep_index):
                    return False
        return True

    def _fix_stale(self, ctx: ProjectContext, cache: ScanCache) -> None:
        """Re-parse restorations whose deps' semantic signatures drifted.

        A dep's signature is only final after propagation, and re-parsing a
        stale dep can move the signature of *its* dependents - so this runs
        to a fixpoint bounded by module count (each pass re-parses at least
        one module).
        """
        for _ in range(len(self.modules) + 1):
            stale = [
                relative
                for relative, entry in self._restored.items()
                if not self._dep_sigs_current(self.modules[relative], entry)
            ]
            if not stale:
                return
            for relative in stale:
                del self._restored[relative]
                old = self.modules[relative]
                index = self._parse_module(ctx, relative)
                self.modules[relative] = index
                if self._by_module.get(index.module) is old:
                    self._by_module[index.module] = index
                if self._by_module.get(relative.stem) is old:
                    self._by_module[relative.stem] = index
                sha = self._shas.get(relative)
                if sha is not None:
                    cache.put(relative, sha, index.to_facts())
            self._propagate_producers()

    def _parse_module(self, ctx: ProjectContext, relative: Path) -> PyModuleIndex:
        source = ctx.read_text(relative)
        if source is None:
            return PyModuleIndex(file=relative, module=_module_name(relative), tree=None)
        return _index_source(relative, source)

    def _module(
        self, ctx: ProjectContext, cache: ScanCache | None, relative: Path
    ) -> PyModuleIndex:
        # Overlay (unsaved LSP buffer) files are parsed from memory and
        # never cached - their disk sha doesn't describe the content.
        overlaid = ctx.options.overlay is not None and relative.as_posix() in ctx.options.overlay
        sha = None if overlaid else (self._shas.get(relative) if cache is not None else None)
        if cache is not None and sha is not None:
            entry = cache.get_entry(relative, sha)
            if entry is None:
                cache.misses += 1
            else:
                facts = entry.get("facts")
                index = (
                    PyModuleIndex.from_facts(relative, facts) if isinstance(facts, dict) else None
                )
                if index is not None:
                    # Optimistic restore on sha match; dep signatures are
                    # validated post-propagation by _fix_stale.
                    self._restored[relative] = entry
                    cache.hits += 1
                    return index
                # Corrupt facts: re-parse so the analyzer layer recomputes.
                cache.misses += 1
        elif cache is not None:
            cache.misses += 1
        index = self._parse_module(ctx, relative)
        if cache is not None and sha is not None:
            cache.put(relative, sha, index.to_facts())
        return index

    def module(self, file: Path) -> PyModuleIndex | None:
        return self.modules.get(file)

    def resolve_import(self, importer: PyModuleIndex, name: str) -> PyModuleIndex | None:
        """Resolve a called name through the importer's ``from x import``."""
        for entry in importer.imports:
            if entry.is_from and (entry.asname or entry.name) == name:
                return self._by_module.get(entry.module) or self._by_module.get(
                    entry.module.rsplit(".", 1)[-1]
                )
        return None

    def resolve_module_attr(self, root: str, attr: str) -> FunctionInfo | None:
        """``import reader`` + ``reader.load_orders`` -> producer info."""
        target = self._by_module.get(root)
        if target is None:
            return None
        matches = self._functions_named(target, attr)
        return matches[0] if matches else None

    def _functions_named(self, index: PyModuleIndex, name: str) -> list[FunctionInfo]:
        """All functions whose bare name matches (functions keyed qualified)."""
        return [fn for fn in index.functions.values() if fn.name == name]

    def is_producer_call(self, importer: PyModuleIndex, site: CallSite) -> bool:
        """True when ``site`` invokes a function known to return a DataFrame."""
        if site.receiver is None:
            if any(fn.returns_df for fn in self._functions_named(importer, site.name)):
                return True
            # Imported producer: from reader import load_orders [as lo].
            for entry in importer.imports:
                if not entry.is_from or (entry.asname or entry.name) != site.name:
                    continue
                target = self.resolve_import(importer, site.name)
                if (
                    target is not None
                    and entry.name is not None
                    and any(fn.returns_df for fn in self._functions_named(target, entry.name))
                ):
                    return True
        else:
            # reader.load_orders(...)
            fn = self.resolve_module_attr(site.receiver, site.name)
            if fn is not None and fn.returns_df:
                return True
        return False

    def _propagate_producers(self) -> None:
        """Fixpoint: producers <-> names bound to producer calls, cross-module.

        Growth is monotonic (returns_df and df_names only ever grow), so the
        bound is the symbol count - not a magic iteration limit.
        """
        bound = sum(len(m.functions) + len(m.assigns) for m in self.modules.values()) + 1
        grown = True
        while grown and bound > 0:
            bound -= 1
            grown = False
            for index in self.modules.values():
                for key, fn in index.functions.items():
                    if fn.returns_df:
                        continue
                    if self._function_returns_df(index, fn):
                        index.functions[key] = dataclasses.replace(fn, returns_df=True)
                        grown = True
                for assign in index.assigns:
                    if assign.value_call is None:
                        continue
                    site = CallSite(
                        name=assign.value_call.rsplit(".", 1)[-1].lower(),
                        dotted=assign.value_call,
                        receiver=(
                            assign.value_call.split(".", 1)[0] if "." in assign.value_call else None
                        ),
                        line=assign.line,
                        column=0,
                        symbol=assign.symbol,
                    )
                    # Producer evidence is tracked even when the name was
                    # already df-ish - the gate distinguishes proven names
                    # from name-heuristic ones.
                    if self.is_producer_call(index, site):
                        index.propagated_names.add(assign.target)
                        if assign.target not in index.df_names:
                            index.df_names.add(assign.target)
                            grown = True
            if not grown:
                break

    def _function_returns_df(self, index: PyModuleIndex, fn: FunctionInfo) -> bool:
        """A function is a producer if a return's root is df-ish or a producer call."""
        for fact in fn.returns:
            if fact.root is not None:
                lowered = fact.root.lower()
                if (
                    fact.root in index.df_names
                    or lowered in _SESSION_ROOTS
                    or "df" in lowered
                    or "spark" in lowered
                ):
                    return True
            if fact.call is not None:
                if _looks_like_df_call(fact.call):
                    return True
                site = CallSite(
                    name=fact.call.rsplit(".", 1)[-1].lower(),
                    dotted=fact.call,
                    receiver=(fact.call.split(".", 1)[0] if "." in fact.call else None),
                    line=fn.line,
                    column=0,
                    symbol=fn.qualified,
                )
                if self.is_producer_call(index, site):
                    return True
        return False


def project_index(ctx: ProjectContext, cache: ScanCache | None = None) -> ProjectIndex:
    """Memoized project index on the scan context."""
    cached = getattr(ctx, INDEX_ATTR, None)
    if isinstance(cached, ProjectIndex):
        return cached
    if cache is None:
        from forge_doctor.core.cache import scan_cache

        cache = scan_cache(ctx)
    index = ProjectIndex(ctx, cache)
    setattr(ctx, INDEX_ATTR, index)
    return index


def _module_name(relative: Path) -> str:
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    if parts and parts[0] in {"src", "app"}:
        parts.pop(0)
    return ".".join(parts)


def _dotted(node: ast.expr) -> str:
    """``a.b.c`` -> ``a.b.c``; best effort for calls/attrs."""
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    elif isinstance(current, ast.Call):
        base = _dotted(current.func)
        return ".".join(reversed(parts)) + ("" if not base else f"({base})")
    return ".".join(reversed(parts))


def _expr_root(node: ast.expr) -> str | None:
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        current = current.value
    while isinstance(current, ast.Call):
        inner = current.func
        current = inner.value if isinstance(inner, ast.Attribute) else inner
    if isinstance(current, ast.Attribute):
        return _expr_root(current.value)
    if isinstance(current, ast.Name):
        return current.id
    return None


def _receiver_root(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return _expr_root(func.value)
    return None


def _return_facts(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[ReturnFact, ...]:
    """Return shapes, skipping nested defs (they get their own entry)."""
    facts: list[ReturnFact] = []
    stack: list[ast.AST] = list(node.body)
    while stack:
        child = stack.pop()
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        if isinstance(child, ast.Return) and child.value is not None:
            value = child.value
            if isinstance(value, ast.Call):
                facts.append(ReturnFact(call=_dotted(value.func)))
            else:
                facts.append(ReturnFact(root=_expr_root(value)))
        else:
            stack.extend(ast.iter_child_nodes(child))
    return tuple(facts)


class _IndexVisitor(ast.NodeVisitor):
    def __init__(self, index: PyModuleIndex) -> None:
        self.index = index
        self._scope: list[str] = []

    @property
    def _symbol(self) -> str:
        return ".".join(self._scope) or "<module>"

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.index.imports.append(
                ImportEntry(
                    module=alias.name,
                    name=None,
                    asname=alias.asname,
                    line=node.lineno,
                    is_from=False,
                )
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            self.index.imports.append(
                ImportEntry(
                    module=node.module or "",
                    name=alias.name,
                    asname=alias.asname,
                    line=node.lineno,
                    is_from=True,
                )
            )

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.index.classes[node.name] = (node.lineno, node.end_lineno or node.lineno)
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualified = f"{self._symbol}.{node.name}"
        # Key by qualified name: ReaderA.load and ReaderB.load must not collide.
        self.index.functions[qualified] = FunctionInfo(
            name=node.name,
            qualified=qualified,
            line=node.lineno,
            end_line=node.end_lineno or node.lineno,
            returns=_return_facts(node),
        )
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_Call(self, node: ast.Call) -> None:
        dotted = _dotted(node.func)
        receiver = _receiver_root(node)
        self.index.calls.append(
            CallSite(
                name=dotted.rsplit(".", 1)[-1].split("(")[0].lower(),
                dotted=dotted,
                receiver=receiver,
                line=node.lineno,
                column=node.col_offset,
                symbol=self._symbol,
                args=tuple(
                    a.value
                    for a in node.args
                    if isinstance(a, ast.Constant) and isinstance(a.value, str)
                ),
                kwargs=tuple(
                    (kw.arg, kw.value.value)
                    for kw in node.keywords
                    if isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                    and kw.arg is not None
                ),
            )
        )
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        root = _expr_root(node.value)
        call = _dotted(node.value.func) if isinstance(node.value, ast.Call) else None
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.index.assigns.append(
                    AssignEntry(
                        target=target.id,
                        value_root=root,
                        value_call=call,
                        line=node.lineno,
                        symbol=self._symbol,
                    )
                )
        self.generic_visit(node)


def _index_source(relative: Path, source: str | None) -> PyModuleIndex:
    index = PyModuleIndex(file=relative, module=_module_name(relative), tree=None)
    if source is None:
        return index
    try:
        index.tree = ast.parse(source, filename=str(relative))
    except SyntaxError:
        return index
    _IndexVisitor(index).visit(index.tree)
    from forge_doctor.analyzers.glue_ast import uses_glue
    from forge_doctor.analyzers.spark_ast import uses_pyspark

    index.uses_pyspark = uses_pyspark(index.tree)
    index.uses_glue = uses_glue(index.tree)
    # Seed df-like names: heuristic names + names bound to df/spark roots.
    for assign in index.assigns:
        root = assign.value_root or ""
        lowered_target = assign.target.lower()
        if "df" in lowered_target or root.lower() in _SESSION_ROOTS or "df" in root.lower():
            index.df_names.add(assign.target)
    return index


def fingerprint_sha(source: str) -> str:
    """Content digest used by incremental cache invalidation."""
    return hashlib.sha256(source.encode("utf-8")).hexdigest()
