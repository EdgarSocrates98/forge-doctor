"""Static SQL analysis via sqlglot (optional ``[sql]`` extra).

One model feeds every SQL### check: ``.sql`` files are split into
statements by the sqlglot tokenizer (exact line numbers, per-statement
error isolation) and ``*.sql("...")`` call sites reuse the string-literal
args already captured in :class:`~forge_doctor_data.analyzers.index.CallSite`.
Never executes or imports analyzed code.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from forge_doctor_data.analyzers.index import ProjectIndex
    from forge_doctor_data.core.context import ProjectContext

SQLGLOT_AVAILABLE = importlib.util.find_spec("sqlglot") is not None

_CACHE_ATTR = "_forge_doctor_data_sql_index"


@dataclass(frozen=True)
class SqlStatement:
    """Facts extracted from one parsed SQL statement."""

    file: Path
    line: int
    source: str  # "file" | "call"
    dialect: str  # dialect that parsed the statement
    kind: str  # parsed expression type, lowercased (select|create|merge|...)
    text: str
    tables_read: tuple[str, ...]
    tables_written: tuple[str, ...]
    wildcard: bool = False
    cross_join: bool = False
    implicit_join: bool = False
    non_sargable: bool = False
    # MERGE-only: source table + target-side ON-predicate columns.
    merge_source: str = ""
    merge_on_cols: tuple[str, ...] = ()


@dataclass
class SqlIndex:
    """All SQL facts for a project, sorted by ``(file, line)``."""

    statements: list[SqlStatement] = field(default_factory=list)
    unparsed: int = 0


def _table_name(table: Any) -> str:
    """Qualified table name without the alias (catalog.db.name)."""
    parts = (table.catalog, table.db, table.name)
    return ".".join(p for p in parts if p)


def _wrapped_column(node: Any, stop: Any) -> bool:
    """True when ``node`` contains a column under a Func before ``stop``."""
    exp = _exp()
    for col in node.find_all(exp.Column):
        cur = col.parent
        while cur is not None and cur is not stop:
            if isinstance(cur, exp.Func):
                return True
            cur = cur.parent
    return False


def _has_column(node: Any) -> bool:
    return next(node.find_all(_exp().Column), None) is not None


def _exp() -> Any:
    from sqlglot import exp

    return exp


def _facts(statement: Any, dialect: str) -> dict[str, Any]:
    """Extract the SqlStatement fact fields from one parsed expression."""
    exp = _exp()
    cte_names = {c.alias for c in statement.find_all(exp.CTE)}

    written: list[str] = []
    skip: set[int] = set()
    if isinstance(statement, (exp.Insert, exp.Create, exp.Merge)):
        target = statement.this
        table = target if isinstance(target, exp.Table) else target.find(exp.Table)
        if isinstance(table, exp.Table):
            written.append(_table_name(table))
            skip = {id(t) for t in table.find_all(exp.Table)} | {id(table)}

    read = {
        _table_name(t)
        for t in statement.find_all(exp.Table)
        if id(t) not in skip and t.name not in cte_names
    }

    wildcard = any(
        star.find_ancestor(exp.Select) is not None and not isinstance(star.parent, exp.AggFunc)
        for star in statement.find_all(exp.Star)
    )

    cross_join = implicit_join = False
    for join in statement.find_all(exp.Join):
        kind = str(join.args.get("kind") or "").upper()
        if kind != "CROSS":
            continue
        cross_join = True
        # Explicit ``CROSS JOIN`` parses extra keys (``pivots``); the
        # comma form ``FROM a, b`` arrives as a bare kind-only join.
        if not join.args.get("on") and not join.args.get("using") and "pivots" not in join.args:
            implicit_join = True

    comparisons = (
        exp.EQ,
        exp.NEQ,
        exp.GT,
        exp.GTE,
        exp.LT,
        exp.LTE,
    )
    non_sargable = False
    for where in statement.find_all(exp.Where):
        for comp in where.this.find_all(*comparisons):
            left, right = comp.left, comp.right
            if (_wrapped_column(left, comp) and not _has_column(right)) or (
                _wrapped_column(right, comp) and not _has_column(left)
            ):
                non_sargable = True
                break

    facts: dict[str, Any] = {
        "tables_read": tuple(sorted(read)),
        "tables_written": tuple(sorted(written)),
        "wildcard": wildcard,
        "cross_join": cross_join,
        "implicit_join": implicit_join,
        "non_sargable": non_sargable,
    }

    if isinstance(statement, exp.Merge):
        using = statement.args.get("using")
        src = using.find(exp.Table) if isinstance(using, exp.Expression) else None
        facts["merge_source"] = _table_name(src) if isinstance(src, exp.Table) else ""
        target = statement.this
        target_table = target if isinstance(target, exp.Table) else target.find(exp.Table)
        target_names = {target_table.name} if isinstance(target_table, exp.Table) else set()
        alias = getattr(target, "alias", "")
        if alias:
            target_names.add(alias)
        on = statement.args.get("on")
        if isinstance(on, exp.Expression):
            cols = {
                col.name
                for col in on.find_all(exp.Column)
                if not col.table or col.table in target_names
            }
            facts["merge_on_cols"] = tuple(sorted(cols))

    return facts


def _parse_statement(text: str, dialects: tuple[str | None, ...]) -> tuple[Any, str] | None:
    """Try dialects in order; return (expression, dialect) or None."""
    import logging

    import sqlglot
    from sqlglot.errors import SqlglotError

    # sqlglot warns on stderr when it falls back to Command (e.g. CALL
    # syntax) - the fallback is intentional, so keep the output clean.
    logging.getLogger("sqlglot").setLevel(logging.ERROR)

    for dialect in dialects:
        try:
            parsed = sqlglot.parse(text, read=dialect)
        except SqlglotError:
            continue
        for statement in parsed:
            if statement is not None:
                return statement, dialect or "generic"
    return None


def _file_statements(
    ctx: ProjectContext, relative: Path, text: str
) -> tuple[list[SqlStatement], int]:
    """Tokenize a ``.sql`` file, split on top-level semicolons, parse each."""
    import sqlglot
    from sqlglot.errors import SqlglotError
    from sqlglot.tokens import TokenType

    try:
        tokens = sqlglot.Tokenizer().tokenize(text)
    except SqlglotError:
        return [], 1

    statements: list[SqlStatement] = []
    unparsed = 0
    chunk: list[Any] = []
    chunks: list[list[Any]] = []
    for token in tokens:
        if token.token_type == TokenType.SEMICOLON:
            chunks.append(chunk)
            chunk = []
        else:
            chunk.append(token)
    chunks.append(chunk)

    for part in chunks:
        part = [t for t in part if t.token_type != TokenType.SEMICOLON]
        if not part:
            continue
        sql = text[part[0].start : part[-1].end + 1]
        line = part[0].line
        parsed = _parse_statement(sql, ("spark", None))
        if parsed is None:
            unparsed += 1
            continue
        statement, dialect = parsed
        statements.append(
            SqlStatement(
                file=relative,
                line=line,
                source="file",
                dialect=dialect,
                kind=type(statement).__name__.lower(),
                text=sql.strip(),
                **_facts(statement, dialect),
            )
        )
    return statements, unparsed


def analyze_sql(ctx: ProjectContext) -> SqlIndex:
    """Build (once, memoized on ctx) the project's SQL statement index."""
    cached = getattr(ctx, _CACHE_ATTR, None)
    if cached is not None:
        return cast(SqlIndex, cached)
    if not SQLGLOT_AVAILABLE:
        empty = SqlIndex()
        setattr(ctx, _CACHE_ATTR, empty)
        return empty

    from forge_doctor_data.analyzers.index import project_index

    index: ProjectIndex = project_index(ctx)
    found = SqlIndex()

    for relative in sorted(ctx.files):
        if relative.suffix.lower() != ".sql":
            continue
        text = ctx.read_text(relative)
        if text is None:
            continue
        statements, unparsed = _file_statements(ctx, relative, text)
        found.statements.extend(statements)
        found.unparsed += unparsed

    for relative, module in sorted(index.modules.items()):
        for site in module.calls:
            if site.name != "sql" or not site.args:
                continue
            parsed = _parse_statement(site.args[0], ("spark", None))
            if parsed is None:
                found.unparsed += 1
                continue
            statement, dialect = parsed
            found.statements.append(
                SqlStatement(
                    file=relative,
                    line=site.line,
                    source="call",
                    dialect=dialect,
                    kind=type(statement).__name__.lower(),
                    text=site.args[0].strip(),
                    **_facts(statement, dialect),
                )
            )

    found.statements.sort(key=lambda s: (s.file.as_posix(), s.line, s.text))
    setattr(ctx, _CACHE_ATTR, found)
    return found
