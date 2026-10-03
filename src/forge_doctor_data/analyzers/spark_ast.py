"""Static PySpark pattern analysis via ``ast`` - never imports target code.

AST v2 light: a thin symbol pass tracks names that probably hold a
DataFrame (assigned from ``spark.*`` calls, ``df``-style names, or chained
from other DataFrames) so findings carry a ``receiver`` and literal receivers
(``[1].collect()``, ``"x".count()``) stop producing false positives.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

_ACTIONS = {
    "collect",
    "topandas",
    "count",
    "show",
    "save",
    "write",
    "take",
    "head",
    "first",
    "foreach",
}
_CACHE_CALLS = {"cache", "persist"}
_SESSION_NAMES = {"spark", "sparksession", "sc", "sparkcontext"}
_LITERAL_NODES = (
    ast.Constant,
    ast.List,
    ast.Tuple,
    ast.Set,
    ast.Dict,
    ast.JoinedStr,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.Lambda,
)


@dataclass(frozen=True)
class SparkFinding:
    """One detected pattern occurrence."""

    pattern: str
    line: int
    receiver: str | None = None


def uses_pyspark(tree: ast.Module) -> bool:
    """True when the module references pyspark - gate against false positives."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            alias.name.split(".")[0] == "pyspark" for alias in node.names
        ):
            return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "pyspark":
            return True
    return False


def dataframe_names(tree: ast.Module) -> set[str]:
    """Names that probably hold a DataFrame.

    Seeds: ``*df*``/``*spark*`` substrings and session names. Propagates
    through ``x = df.<anything>()``/``x = spark.<anything>()`` assignments
    to a fixpoint so chains like ``df2 = df.filter(...)`` count.
    """
    names: set[str] = set()

    def looks_df(name: str) -> bool:
        lowered = name.lower()
        return "df" in lowered or "spark" in lowered or lowered in _SESSION_NAMES

    for _ in range(4):  # bounded fixpoint - enough for realistic chains
        grown = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            root = _expr_root_name(node.value.func)
            if root is None or not (looks_df(root) or root in names):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id not in names:
                    names.add(target.id)
                    grown = True
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and looks_df(node.id):
                names.add(node.id)
        if not grown:
            break
    return names


class SparkAnalyzer(ast.NodeVisitor):
    """Collects Spark anti-pattern occurrences with line numbers."""

    def __init__(self) -> None:
        self.findings: list[SparkFinding] = []
        self.df_names: set[str] = set()
        self._loop_depth = 0

    def analyze_source(self, source: str, path: Path) -> list[SparkFinding]:
        self.findings = []
        self._loop_depth = 0
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            return []
        if not uses_pyspark(tree):
            return []
        self.df_names = dataframe_names(tree)
        self.visit(tree)
        return list(self.findings)

    def is_probable_dataframe(self, name: str | None) -> bool:
        """Check-facing helper: receiver resolves to a likely DataFrame."""
        if name is None:
            return False
        lowered = name.lower()
        return name in self.df_names or "df" in lowered or lowered in _SESSION_NAMES

    def visit_For(self, node: ast.For) -> None:
        self._loop_depth += 1
        self.generic_visit(node)
        self._loop_depth -= 1

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._loop_depth += 1
        self.generic_visit(node)
        self._loop_depth -= 1

    def visit_While(self, node: ast.While) -> None:
        self._loop_depth += 1
        self.generic_visit(node)
        self._loop_depth -= 1

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node)
        receiver = _receiver_name(node)
        # Literal receivers ([1].collect(), "x".count()) can never be DataFrames.
        literal = _receiver_is_literal(node)
        if name == "collect" and not literal:
            self._add("collect", node, receiver)
        elif name == "topandas" and not literal:
            self._add("toPandas", node, receiver)
        elif name in {"repartition", "coalesce"}:
            if len(node.args) == 1 and _is_literal_one(node.args[0]) and not literal:
                self._add(f"{name}(1)", node, receiver)
        elif name in _CACHE_CALLS and not literal:
            self._add(name, node, receiver)
        elif name == "unpersist" and not literal:
            self._add("unpersist", node, receiver)
        elif name == "udf":
            self._add("python_udf", node, receiver)
        elif name == "crossjoin":
            self._add("crossJoin", node, receiver)
        elif name == "join" and _join_without_condition(node):
            self._add("join_no_condition", node, receiver)
        elif name in {"orderby", "sort"}:
            self._add("global_sort", node, receiver)
        elif name == "withcolumn" and self._loop_depth > 0:
            self._add("withColumn_in_loop", node, receiver)
        elif name in _ACTIONS and self._loop_depth > 0:
            self._add("action_in_loop", node, receiver)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == "rdd" and not isinstance(node.value, _LITERAL_NODES):
            self._add("rdd", node, _expr_root_name(node.value))
        self.generic_visit(node)

    def _add(self, pattern: str, node: ast.expr, receiver: str | None = None) -> None:
        self.findings.append(SparkFinding(pattern=pattern, line=node.lineno, receiver=receiver))


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr.lower()
    if isinstance(func, ast.Name):
        return func.id.lower()
    return None


def _receiver_name(node: ast.Call) -> str | None:
    """Receiver name for ``x.y.z(...)`` -> ``x``; ``None`` for bare calls."""
    func = node.func
    if isinstance(func, ast.Attribute):
        return _expr_root_name(func.value)
    return None


def _expr_root_name(node: ast.expr) -> str | None:
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        current = current.value
    while isinstance(current, ast.Call):
        inner = current.func
        current = inner.value if isinstance(inner, ast.Attribute) else inner
    if isinstance(current, ast.Attribute):
        return _expr_root_name(current.value)
    if isinstance(current, ast.Name):
        return current.id
    return None


def _receiver_is_literal(node: ast.Call) -> bool:
    func = node.func
    return isinstance(func, ast.Attribute) and isinstance(func.value, _LITERAL_NODES)


def _join_without_condition(node: ast.Call) -> bool:
    """``df.join(other)`` with no join condition -> Cartesian risk."""
    if len(node.args) > 1:
        return False
    return not any(k.arg in {"on", "using", "condition"} for k in node.keywords)


def _is_literal_one(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value == 1
