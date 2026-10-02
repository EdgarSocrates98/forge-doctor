"""Public contract implemented by checks and external plugins.

External packages register through the ``forge_doctor.checks`` entry-point
group. An entry point resolves to a ``Check`` instance, a ``Check`` subclass,
a callable returning one, or - Plugin SDK v2 - a :class:`PluginDescriptor`
(or callable returning one) that bundles identity, compatibility, and checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from forge_doctor.core.models import Confidence, EvidenceKind, Severity

if TYPE_CHECKING:
    from collections.abc import Sequence

    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.models import CheckResult

# Plugin API versions this engine accepts.
SUPPORTED_API_VERSIONS = ("1", "2")
CURRENT_API_VERSION = "2"


@runtime_checkable
class Check(Protocol):
    """A single diagnostic rule.

    ``id`` is the stable public identifier (``REP001`` ...); it must never be
    derived from message text. ``category`` groups output and enables
    ``--check <category>`` selection.
    """

    id: str
    title: str
    category: str
    why: str
    when_ok: str
    fix: str

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        """Execute the check against the project context."""
        ...


@dataclass(frozen=True)
class PluginIdentity:
    """Formal identity of a plugin distribution - the allowlist key.

    Derived from the installed distribution metadata (entry point ``dist``),
    never from class names, so ``allow = ["forge-doctor-example"]`` matches
    the distribution exactly as documented.
    """

    distribution: str
    version: str | None = None
    api_version: str = "1"
    entry_point: str | None = None


@dataclass(frozen=True)
class PluginDescriptor:
    """Plugin SDK v2 manifest - what a plugin declares about itself.

    ``api_version`` gates loading: plugins declaring an unsupported API are
    rejected before their checks run. ``requires_forge_doctor`` is a PEP 440
    specifier (``">=0.4,<1.0"``) checked against the running engine.
    """

    name: str
    version: str
    api_version: str = "2"
    checks: Sequence[Check] = field(default_factory=tuple)
    requires_forge_doctor: str | None = None
    capabilities: tuple[str, ...] = ("checks",)

    def check_compatibility(self, engine_version: str) -> str | None:
        """None when compatible; human-readable reason otherwise."""
        if self.api_version not in SUPPORTED_API_VERSIONS:
            return (
                f"api_version {self.api_version!r} unsupported "
                f"(supported: {', '.join(SUPPORTED_API_VERSIONS)})"
            )
        if self.requires_forge_doctor:
            from packaging.specifiers import InvalidSpecifier, SpecifierSet
            from packaging.version import InvalidVersion, Version

            try:
                spec = SpecifierSet(self.requires_forge_doctor)
            except InvalidSpecifier:
                return f"invalid requires_forge_doctor {self.requires_forge_doctor!r}"
            try:
                if Version(engine_version) not in spec:
                    return (
                        f"requires forge-doctor {self.requires_forge_doctor}, "
                        f"running {engine_version}"
                    )
            except InvalidVersion:
                return f"unparseable engine version {engine_version!r}"
        return None


class CheckBase:
    """Convenience base: carries metadata and a ``result`` helper.

    ``why``/``when_ok``/``fix`` feed ``forge-doctor explain <ID>`` - keep
    them one line each; they document intent, not every edge case.
    ``confidence`` describes detector certainty, not severity: HIGH means
    the evidence is conclusive, MEDIUM/LOW mark heuristics.
    """

    id: str
    title: str
    category: str
    why: str = ""
    when_ok: str = ""
    fix: str = ""
    confidence = Confidence.HIGH
    # Source-plane the check's facts come from; most built-ins read code.
    evidence_kind = EvidenceKind.STATIC
    tags: tuple[str, ...] = ()
    docs_uri: str | None = None
    # Incremental scans: which evidence domains this check observes
    # (see core.incremental). None = resolved from the module map, or
    # "unbounded" for plugins (conservative: always rerun).
    evidence_domains: tuple[str, ...] | None = None

    def result(
        self,
        severity: Severity,
        message: str,
        recommendation: str | None = None,
        file: Path | None = None,
        line: int | None = None,
        column: int | None = None,
        evidence: str | None = None,
        confidence: Confidence | None = None,
        evidence_kind: EvidenceKind | None = None,
        tags: tuple[str, ...] = (),
        symbol: str | None = None,
    ) -> CheckResult:
        from forge_doctor.core.models import CheckResult

        return CheckResult(
            check_id=self.id,
            title=self.title,
            severity=severity,
            category=self.category,
            message=message,
            file=file,
            line=line,
            column=column,
            recommendation=recommendation,
            confidence=confidence or self.confidence,
            evidence=evidence,
            evidence_kind=evidence_kind or self.evidence_kind,
            tags=tags or self.tags,
            docs_uri=self.docs_uri,
            symbol=symbol,
        )

    def evidence_at(self, ctx: ProjectContext, file: Path, line: int | None) -> str | None:
        """Source line at ``file:line`` - the Evidence Engine's raw material."""
        if line is None:
            return None
        text = ctx.read_text(file)
        if text is None:
            return None
        lines = text.splitlines()
        if not 1 <= line <= len(lines):
            return None
        snippet = lines[line - 1].strip()
        return snippet[:200] if snippet else None
