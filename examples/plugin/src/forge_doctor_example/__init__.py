"""Minimal forge-doctor plugin: one check, zero dependencies."""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge_doctor.core.models import Severity
from forge_doctor.plugins.protocol import CheckBase

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.models import CheckResult


class ExampleTodoCheck(CheckBase):
    """EXAMPLE001: counts TODO/FIXME comments left in Python sources."""

    id = "EXAMPLE001"
    title = "TODO comments"
    category = "example"
    why = "TODOs are deferred decisions; a big pile is silent debt."
    when_ok = "Deliberate markers with tracking links."
    fix = "Resolve or link each TODO to an issue."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        hits = 0
        for relative in ctx.files:
            if relative.suffix != ".py":
                continue
            source = ctx.read_text(relative)
            if source is not None:
                hits += sum(
                    line.count("TODO") + line.count("FIXME") for line in source.splitlines()
                )
        if not hits:
            return []
        return [
            self.result(
                Severity.INFO,
                f"{hits} TODO/FIXME comment(s) found",
                recommendation="Track them as issues or resolve them.",
            )
        ]


CHECKS = [ExampleTodoCheck()]
