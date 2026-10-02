"""Cross-domain rule engine (PLAT###) - deterministic, typed, small.

A ``CrossDomainRule`` combines facts from several semantic models, the
canonical ``DataPlatformGraph``, and the ``CapabilityRegistry`` into a
platform finding. Rules declare the graph shape they need up front
(``required_entity_kinds`` / ``required_relationships``) so the runner
can short-circuit without evaluating: a missing prerequisite means no
finding, never a fuzzy match.

Every hit carries ``facts`` - the contributing facts that made the rule
fire - so the emitted finding explains itself ("retries=5 task +
append-only sink + no idempotency evidence") rather than asserting a
bare risk label.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from forge_doctor.core.capabilities import (
    CapabilityRegistry,
    CapabilityResult,
    capability_registry,
)
from forge_doctor.core.models import Confidence, EvidenceKind, Severity
from forge_doctor.core.platform_graph import DataPlatformGraph
from forge_doctor.core.platform_graph import (
    EntityKind as K,
)
from forge_doctor.core.platform_graph import (
    RelKind as R,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from forge_doctor.core.context import ProjectContext


@dataclass(frozen=True)
class ContributingFact:
    """One fact that contributed to a cross-domain hit."""

    plane: str  # orchestration|compute|sink|capability|storage|config
    label: str  # short fact name: "retries=5", "append sink"
    detail: str  # human-readable value
    file: Path | None = None
    line: int | None = None

    def render(self) -> str:
        loc = f"{self.file.as_posix()}:{self.line}" if self.file is not None else ""
        where = f" ({loc})" if loc else ""
        return f"{self.plane}: {self.label} = {self.detail}{where}"


@dataclass(frozen=True)
class CrossDomainHit:
    """One fired rule occurrence; ``facts`` is the explanation."""

    message: str
    facts: tuple[ContributingFact, ...] = ()
    file: Path | None = None
    line: int | None = None
    severity: Severity | None = None  # per-hit downgrade (e.g. INFO)


@dataclass
class RuleContext:
    """Read-only view handed to rule predicates."""

    ctx: ProjectContext
    graph: DataPlatformGraph
    caps: CapabilityRegistry
    _kinds: set[K] = field(default_factory=set)
    _rels: set[R] = field(default_factory=set)

    def __post_init__(self) -> None:
        self._kinds = {e.kind for e in self.graph.entities()}
        self._rels = {r.kind for r in self.graph.relationships()}

    def has_kind(self, kind: K) -> bool:
        return kind in self._kinds

    def has_relationship(self, kind: R) -> bool:
        return kind in self._rels

    def capability(self, capability: str, **attrs: str) -> CapabilityResult:
        kwargs: dict[str, Any] = dict(attrs)
        return self.caps.evaluate(capability, **kwargs)


@dataclass(frozen=True)
class CrossDomainRule:
    """A platform rule: declared prerequisites + a typed predicate."""

    id: str
    title: str
    description: str
    why: str
    when_ok: str
    fix: str
    severity: Severity = Severity.WARNING
    confidence: Confidence = Confidence.MEDIUM
    evidence_kind: EvidenceKind = EvidenceKind.DERIVED
    required_entity_kinds: frozenset[K] = frozenset()
    required_relationships: frozenset[R] = frozenset()
    required_capabilities: tuple[str, ...] = ()
    evaluate: Callable[[RuleContext], list[CrossDomainHit]] = field(
        default=lambda _rc: [], repr=False, compare=False
    )

    def prerequisites_met(self, rc: RuleContext) -> bool:
        """All declared requirements present in the graph/registry."""
        if not self.required_entity_kinds <= rc._kinds:
            return False
        if not self.required_relationships <= rc._rels:
            return False
        return all(cap in rc.caps.capabilities() for cap in self.required_capabilities)

    def run(self, rc: RuleContext) -> list[CrossDomainHit]:
        if not self.prerequisites_met(rc):
            return []
        hits = self.evaluate(rc)
        return sorted(
            hits,
            key=lambda h: (
                h.file.as_posix() if h.file is not None else "",
                h.line or 0,
                h.message,
            ),
        )


def rule_context(ctx: ProjectContext) -> RuleContext:
    """Build a RuleContext: platform graph + capability registry, cached."""
    from forge_doctor.analyzers.platform_graph_builder import build_platform_graph

    return RuleContext(ctx=ctx, graph=build_platform_graph(ctx), caps=capability_registry())
