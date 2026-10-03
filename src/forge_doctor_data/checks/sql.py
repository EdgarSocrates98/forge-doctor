"""SQL checks (SQL###) over the shared SqlIndex - requires the [sql] extra."""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge_doctor_data.analyzers.sql_ast import SqlIndex, SqlStatement, analyze_sql
from forge_doctor_data.core.models import Severity
from forge_doctor_data.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor_data.core.context import ProjectContext
    from forge_doctor_data.core.models import CheckResult


def _index(ctx: ProjectContext) -> SqlIndex:
    return analyze_sql(ctx)


class _SqlCheck(CheckBase):
    category = "sql"

    def statements(self, ctx: ProjectContext) -> list[SqlStatement]:
        return _index(ctx).statements


class SqlSurface(_SqlCheck):
    """SQL000: how much SQL the project actually contains."""

    id = "SQL000"
    title = "SQL surface"
    why = "Anchor: sizes the SQL surface feeding the other SQL checks."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the count to size the SQL surface."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        index = _index(ctx)
        if not index.statements:
            if index.unparsed:
                return [
                    self.result(
                        Severity.INFO,
                        f"no SQL statements parsed ({index.unparsed} skipped as unparseable)",
                    )
                ]
            return [self.result(Severity.PASS, "no SQL statements detected")]
        message = f"{len(index.statements)} SQL statements analyzed"
        if index.unparsed:
            message += f" ({index.unparsed} skipped as unparseable)"
        return [self.result(Severity.INFO, message)]


class SelectStar(_SqlCheck):
    """SQL001: ``SELECT *`` hides the real projection from readers and engines."""

    id = "SQL001"
    title = "SELECT *"
    why = "Wildcard reads pull every column - brittle on schema drift, heavier on IO."
    when_ok = "Exploratory notebooks and tiny throwaway queries."
    fix = "Project the columns you actually need."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.WARNING,
                "SELECT * - project explicit columns",
                file=s.file,
                line=s.line,
                evidence=self.evidence_at(ctx, s.file, s.line),
            )
            for s in self.statements(ctx)
            if s.wildcard
        ]


class CartesianJoin(_SqlCheck):
    """SQL002: CROSS JOIN / comma joins multiply rows without a predicate."""

    id = "SQL002"
    title = "Cartesian join"
    why = "CROSS/comma joins multiply rows - usually a missing ON predicate."
    when_ok = "Deliberate cartesian products (calendar grids, small dims)."
    fix = "Add the join predicate, or comment why the product is intended."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        results = []
        for s in self.statements(ctx):
            if not s.cross_join:
                continue
            message = (
                "implicit comma join (FROM a, b)"
                if s.implicit_join
                else "CROSS JOIN without ON predicate"
            )
            results.append(
                self.result(
                    Severity.WARNING,
                    message,
                    file=s.file,
                    line=s.line,
                    evidence=self.evidence_at(ctx, s.file, s.line),
                )
            )
        return results


class NonSargablePredicate(_SqlCheck):
    """SQL003: a function wrapping a column defeats indexes/predicate pushdown."""

    id = "SQL003"
    title = "Non-sargable predicate"
    why = "f(column) = value can't use indexes or partition pruning - full scan."
    when_ok = "Tiny tables, or predicates engines rewrite internally."
    fix = "Rewrite on the constant side (e.g. range bounds instead of f(col))."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.WARNING,
                "function-wrapped column in WHERE comparison - rewrite as a sargable predicate",
                file=s.file,
                line=s.line,
                evidence=self.evidence_at(ctx, s.file, s.line),
            )
            for s in self.statements(ctx)
            if s.non_sargable
        ]


CHECKS: list[Check] = [
    SqlSurface(),
    SelectStar(),
    CartesianJoin(),
    NonSargablePredicate(),
]
