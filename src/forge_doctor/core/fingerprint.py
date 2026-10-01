"""Semantic fingerprint v3 - position-independent finding identity.

v2 hashed ``check_id|file|line|column|message``: moving a statement ten lines
down or rewording a message produced phantom "new" findings and broke
baselines/diffs. v3 anchors identity to *what* was found, not *where the text
happens to be*:

    v3 | check_id | relative_file | symbol_path | evidence_anchor

- ``symbol_path``: dotted enclosing function/class (``Pipeline.run``), resolved
  from the target file's AST when a line is known.
- ``evidence_anchor``: ``ast.dump`` of the smallest simple statement covering
  the finding line (stable across reformatting); falls back to the normalized
  evidence/source line, then to the normalized message for file-less results.

Identical anchors in the same file (two ``df.collect()`` in one function) get a
stable ``#n`` ordinal assigned in a post-pass - order-dependent, but resilient
to line moves and message edits, which is what baselines need.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import re
from pathlib import Path
from typing import TYPE_CHECKING

from forge_doctor.core.models import CheckResult

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext

FINGERPRINT_VERSION = 3

_AST_CACHE_ATTR = "_fd_fp_ast_cache"
_WS_RE = re.compile(r"\s+")

# Compound statements are skipped as anchors - their dumps embed the whole body.
_COMPOUND = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.With,
    ast.AsyncWith,
    ast.If,
    ast.Try,
    ast.TryStar,
    ast.Match,
)


def normalize_text(text: str) -> str:
    """Whitespace-insensitive normalization for evidence anchors."""
    return _WS_RE.sub(" ", text.strip())


def compute_fingerprint(result: CheckResult, ctx: ProjectContext) -> str:
    """Full v3 fingerprint: AST symbol + evidence anchors when resolvable."""
    material = "|".join(
        [
            f"v{FINGERPRINT_VERSION}",
            result.check_id,
            result.file.as_posix() if result.file is not None else "",
            result.symbol or "",
            _anchor(result, ctx),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def assign_fingerprints(results: list[CheckResult], ctx: ProjectContext) -> list[CheckResult]:
    """Resolve symbol/anchor for every result and disambiguate collisions.

    Two occurrences sharing check/file/symbol/anchor (e.g. two identical calls
    in one function) are ordered by (line, column, message) and the nth gets a
    ``#n`` material suffix - deterministic across scans and immune to edits
    elsewhere in the file.
    """
    resolved: list[CheckResult] = []
    for result in results:
        symbol = result.symbol or _symbol_path(result, ctx)
        fingerprint = compute_fingerprint(
            result if symbol == result.symbol else dataclasses.replace(result, symbol=symbol),
            ctx,
        )
        resolved.append(
            dataclasses.replace(result, symbol=symbol, fingerprint=fingerprint)
            if symbol != result.symbol
            else dataclasses.replace(result, fingerprint=fingerprint)
        )

    groups: dict[str, list[int]] = {}
    for index, result in enumerate(resolved):
        groups.setdefault(result.fingerprint or "", []).append(index)

    for indexes in groups.values():
        if len(indexes) < 2:
            continue
        indexes.sort(
            key=lambda i: (
                resolved[i].line or 0,
                resolved[i].column or 0,
                resolved[i].message,
            )
        )
        for ordinal, index in enumerate(indexes[1:], start=1):
            result = resolved[index]
            resolved[index] = dataclasses.replace(
                result,
                fingerprint=hashlib.sha256(f"{result.fingerprint}|#{ordinal}".encode()).hexdigest()[
                    :16
                ],
            )
    return resolved


def resolve_symbol(result: CheckResult, ctx: ProjectContext) -> str | None:
    """Public helper for ``trace``: dotted symbol enclosing the finding."""
    return _symbol_path(result, ctx)


def _anchor(result: CheckResult, ctx: ProjectContext) -> str:
    if result.file is not None and result.line is not None:
        stmt = _smallest_statement(_parse_module(ctx, result.file), result.line)
        if stmt is not None:
            return ast.dump(stmt, annotate_fields=False)
        line_text = _source_line(ctx, result.file, result.line)
        if line_text:
            return line_text
    return _anchor_fallback(result)


def _anchor_fallback(result: CheckResult) -> str:
    """Evidence- then message-derived anchor for results without AST context."""
    if result.evidence:
        return normalize_text(result.evidence)
    if result.file is not None and result.line is not None:
        return normalize_text(result.message)
    # File-less results (one per dependency, per repo property, ...): the
    # message is the only distinguishing axis, so it stays in the material.
    return normalize_text(result.message) if result.file is None else ""


def _parse_module(ctx: ProjectContext, file: Path) -> ast.Module | None:
    cache: dict[Path, ast.Module | None] | None = getattr(ctx, _AST_CACHE_ATTR, None)
    if cache is None:
        cache = {}
        setattr(ctx, _AST_CACHE_ATTR, cache)
    if file in cache:
        return cache[file]
    module: ast.Module | None = None
    source = ctx.read_text(file)
    if source is not None:
        try:
            module = ast.parse(source, filename=str(file))
        except SyntaxError:
            module = None
    cache[file] = module
    return module


def _symbol_path(result: CheckResult, ctx: ProjectContext) -> str | None:
    if result.file is None or result.line is None:
        return None
    tree = _parse_module(ctx, result.file)
    if tree is None:
        return None
    enclosing = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and node.lineno <= result.line <= (node.end_lineno or node.lineno)
    ]
    return ".".join(enclosing) if enclosing else "<module>"


def _smallest_statement(tree: ast.Module | None, line: int) -> ast.stmt | None:
    if tree is None:
        return None
    best: ast.stmt | None = None
    best_span: int | None = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.stmt) or isinstance(node, _COMPOUND):
            continue
        end = node.end_lineno or node.lineno
        if node.lineno <= line <= end:
            span = end - node.lineno
            if best_span is None or span < best_span:
                best, best_span = node, span
    return best


def _source_line(ctx: ProjectContext, file: Path, line: int) -> str:
    text = ctx.read_text(file)
    if text is None:
        return ""
    lines = text.splitlines()
    if not 1 <= line <= len(lines):
        return ""
    return normalize_text(lines[line - 1])
