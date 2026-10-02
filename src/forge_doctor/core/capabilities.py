"""Capability Engine: versioned platform capability facts as data, not code.

Checks ask the registry "does this platform/context support X" instead of
scattering ``if glue_version >= ...`` knowledge through the tree. Facts live
in ``knowledge/capabilities/*.json`` packs (schema_version 2) - every entry
carries a ``source`` and entries without one are rejected at load.

Entry schema (one object per fact):

.. code-block:: json

    {
      "id": "DYNAMODB_TRANSACTIONS",
      "platform": "dynamodb_global_table",   // optional; defaults to filename
      "status": "unsupported",               // supported|unsupported|conditional
      "when": {"variant": "MRSC"},           // optional context gate
      "versions": {"4.0": "supported"},      // optional per-version map
      "conditions": [                        // optional attribute requirements
        {"attribute": "format_version", "op": "gte", "value": "2",
         "status_on_fail": "unsupported", "reason": "row ops need v2"}
      ],
      "limitations": ["..."],
      "reason": "human explanation",
      "source": "https://docs.aws.amazon.com/..."
    }

Honesty contract: absence of proof is never proof of absence. Unknown
platform, unknown capability, or a version outside every declared
``versions`` map returns UNKNOWN - never UNSUPPORTED.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import cache
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

_VALID_STATUS = {"supported", "unsupported", "conditional"}

_CONTEXT_FIELDS = frozenset(
    {
        "platform",
        "version",
        "engine",
        "runtime",
        "format",
        "catalog",
        "governance_model",
        "operation",
        "variant",
    }
)


class CapabilityStatus(Enum):
    """Tri-state-plus answer: UNKNOWN means "we cannot prove it either way"."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    CONDITIONAL = "conditional"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CapabilityContext:
    """The environment a capability question is asked against.

    ``attributes`` carries open-ended facts (e.g. ``format_version=2``);
    named fields cover the dimensions pack authors gate on most.
    """

    platform: str
    version: str | None = None
    engine: str | None = None
    runtime: str | None = None
    format: str | None = None
    catalog: str | None = None
    governance_model: str | None = None
    operation: str | None = None
    variant: str | None = None
    attributes: tuple[tuple[str, str], ...] = ()

    def attribute(self, key: str) -> str | None:
        named = getattr(self, key, None)
        if isinstance(named, str) and named:
            return named
        return dict(self.attributes).get(key)


@dataclass(frozen=True)
class CapabilityResult:
    """Answer to one capability question, with provenance for reporting."""

    capability: str
    platform: str
    status: CapabilityStatus
    reason: str = ""
    limitations: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    source: str | None = None
    pack: str = ""
    pack_version: str = ""
    verified_at: str = ""
    # Provenance of the deciding fact (spec 226): which pack entry won,
    # which ``when`` clause gated it, and - for UNKNOWN - the evidence
    # that would have decided.
    entry_id: str = ""
    matched_when: tuple[tuple[str, str], ...] = ()
    missing_evidence: tuple[str, ...] = ()

    @property
    def supported(self) -> bool:
        return self.status is CapabilityStatus.SUPPORTED


@dataclass(frozen=True)
class _Condition:
    attribute: str
    op: str  # eq|ne|gte|lte|gt|lt|in|not_in|present
    value: str = ""
    status_on_fail: CapabilityStatus = CapabilityStatus.UNSUPPORTED
    reason: str = ""

    @property
    def text(self) -> str:
        return f"{self.attribute} {self.op} {self.value}".strip()


@dataclass(frozen=True)
class _Entry:
    """One validated pack fact."""

    id: str
    platform: str
    status: CapabilityStatus
    pack: str  # "capabilities/dynamodb"
    pack_version: str
    verified_at: str
    source: str
    when: tuple[tuple[str, str], ...] = ()
    versions: tuple[tuple[str, CapabilityStatus], ...] = ()
    conditions: tuple[_Condition, ...] = ()
    limitations: tuple[str, ...] = ()
    reason: str = ""


def _version_key(version: str) -> tuple[int, ...]:
    """Numeric sort key tolerant of 'x' wildcards and junk segments."""
    from forge_doctor.core.knowledge import _version_sort

    return _version_sort(version)


def _compare(a: str, b: str) -> int:
    ka, kb = _version_key(a), _version_key(b)
    return (ka > kb) - (ka < kb)


def _check_condition(cond: _Condition, ctx: CapabilityContext) -> CapabilityStatus | None:
    """None when satisfied; the failing status otherwise; CONDITIONAL when
    the attribute is absent (cannot prove satisfaction or violation)."""
    actual = ctx.attribute(cond.attribute)
    if actual is None:
        return CapabilityStatus.CONDITIONAL
    op = cond.op
    if op == "present":
        return None
    if op in {"in", "not_in"}:
        members = {piece.strip() for piece in cond.value.split(",")}
        ok = actual in members if op == "in" else actual not in members
        return None if ok else cond.status_on_fail
    if op in {"gte", "lte", "gt", "lt"}:
        cmp = _compare(actual, cond.value)
        ok = {
            "gte": cmp >= 0,
            "lte": cmp <= 0,
            "gt": cmp > 0,
            "lt": cmp < 0,
        }[op]
        return None if ok else cond.status_on_fail
    ok = (actual == cond.value) if op == "eq" else (actual != cond.value)
    return None if ok else cond.status_on_fail


def _parse_entry(
    raw: Any, default_platform: str, pack: str, meta: dict[str, str]
) -> tuple[_Entry | None, str | None]:
    """Validate one pack object; ``(entry, issue)`` - exactly one is set."""
    if not isinstance(raw, dict):
        return None, f"{pack}: entry is not an object"
    ident = raw.get("id")
    if not isinstance(ident, str) or not ident:
        return None, f"{pack}: entry missing id"
    status_raw = str(raw.get("status", "")).lower()
    if status_raw not in _VALID_STATUS:
        return None, f"{pack}: {ident} invalid status {status_raw!r}"
    source = raw.get("source")
    if not isinstance(source, str) or not source.startswith(("http://", "https://")):
        return None, f"{pack}: {ident} missing source"
    platform = raw.get("platform", default_platform)
    if not isinstance(platform, str) or not platform:
        return None, f"{pack}: {ident} invalid platform"

    when = raw.get("when", {})
    if not isinstance(when, dict):
        return None, f"{pack}: {ident} invalid when clause"

    versions: list[tuple[str, CapabilityStatus]] = []
    raw_versions = raw.get("versions", {})
    if not isinstance(raw_versions, dict):
        return None, f"{pack}: {ident} invalid versions map"
    for ver, vstatus in raw_versions.items():
        vstat = str(vstatus).lower()
        if vstat not in _VALID_STATUS:
            return None, f"{pack}: {ident} version {ver!r} invalid status"
        versions.append((str(ver), CapabilityStatus(vstat)))

    conditions: list[_Condition] = []
    for raw_cond in raw.get("conditions", []):
        if not isinstance(raw_cond, dict) or not raw_cond.get("attribute"):
            return None, f"{pack}: {ident} malformed condition"
        on_fail = str(raw_cond.get("status_on_fail", "unsupported")).lower()
        if on_fail not in _VALID_STATUS:
            return None, f"{pack}: {ident} invalid status_on_fail"
        conditions.append(
            _Condition(
                attribute=str(raw_cond["attribute"]),
                op=str(raw_cond.get("op", "eq")),
                value=str(raw_cond.get("value", "")),
                status_on_fail=CapabilityStatus(on_fail),
                reason=str(raw_cond.get("reason", "")),
            )
        )

    limitations = raw.get("limitations", [])
    entry = _Entry(
        id=ident,
        platform=platform,
        status=CapabilityStatus(status_raw),
        pack=pack,
        pack_version=meta["pack_version"],
        verified_at=meta["verified_at"],
        source=source,
        when=tuple(sorted((str(k), str(v)) for k, v in when.items())),
        versions=tuple(sorted(versions)),
        conditions=tuple(conditions),
        limitations=tuple(str(lim) for lim in limitations) if isinstance(limitations, list) else (),
        reason=str(raw.get("reason", "")),
    )
    return entry, None


class CapabilityRegistry:
    """Read-only view over ``knowledge/capabilities/*.json`` facts.

    Never emits findings itself - checks consume :class:`CapabilityResult`
    and decide whether a fact becomes a finding.
    """

    def __init__(self, packs: Iterable[tuple[str, str, dict[str, Any]]] | None = None) -> None:
        """``packs`` are ``(platform-default, pack-id, payload)`` triples.

        None loads the bundled ``knowledge/capabilities`` domain; tests pass
        synthetic payloads directly.
        """
        self._entries: list[_Entry] = []
        self._issues: list[str] = []
        if packs is None:
            from forge_doctor.core.knowledge import list_packs

            packs = [
                (name, f"capabilities/{name}", pack)
                for domain, name, pack in list_packs()
                if domain == "capabilities"
            ]
        for default_platform, pack_id, pack in sorted(packs, key=lambda p: p[1]):
            self._load_pack(default_platform, pack_id, pack)
        self._entries.sort(key=lambda e: (e.platform, e.id, e.when))
        self._issues.sort()

    # -- loading -----------------------------------------------------------

    def _load_pack(self, default_platform: str, pack_id: str, pack: dict[str, Any]) -> None:
        if not pack:
            self._issues.append(f"{pack_id}: missing or unreadable")
            return
        if pack.get("schema_version") != 2:
            self._issues.append(
                f"{pack_id}: schema_version {pack.get('schema_version')} (expected 2)"
            )
            return
        meta = {
            "pack_version": str(pack.get("pack_version", "-")),
            "verified_at": str(pack.get("verified_at", "")),
        }
        entries = pack.get("capabilities", [])
        if not isinstance(entries, list):
            self._issues.append(f"{pack_id}: capabilities is not a list")
            return
        seen: set[tuple[str, str, tuple[tuple[str, str], ...]]] = set()
        for raw in entries:
            entry, issue = _parse_entry(raw, default_platform, pack_id, meta)
            if issue:
                self._issues.append(issue)
                continue
            assert entry is not None
            key = (entry.platform, entry.id, entry.when)
            if key in seen:
                self._issues.append(
                    f"{pack_id}: duplicate capability {entry.id} ({entry.platform})"
                )
                continue
            seen.add(key)
            self._entries.append(entry)

    @property
    def validation_issues(self) -> tuple[str, ...]:
        """Load-time problems (missing source, bad schema, duplicates...)."""
        return tuple(self._issues)

    # -- queries -----------------------------------------------------------

    def evaluate(
        self,
        capability: str,
        context: CapabilityContext | None = None,
        **kwargs: Any,
    ) -> CapabilityResult:
        """Resolve ``capability`` for a platform/context.

        Accepts either a :class:`CapabilityContext` or kwargs; kwargs matching
        context fields are used directly, the rest land in ``attributes``.
        """
        ctx = context or self._context_from_kwargs(kwargs)
        matched = self._matching(ctx, capability)
        if not matched:
            declared = [
                e for e in self._entries if e.platform == ctx.platform and e.id == capability
            ]
            missing = sorted({f"{k}={v}" for e in declared for k, v in e.when})
            if not missing and declared:
                missing = ["entry prerequisites (context attributes)"]
            return self._unknown(
                ctx,
                capability,
                f"no capability facts for {ctx.platform}/{capability}",
                missing_evidence=tuple(missing),
            )
        results = [self._apply(entry, ctx) for entry in matched]
        covered = [r for r in results if r[1] is not None]
        if not covered:
            known = sorted(
                {v for e in matched for v, _ in e.versions},
                key=_version_key,
            )
            missing = [f"version not covered (known: {', '.join(known)})"] if known else []
            return self._unknown(
                ctx,
                capability,
                "no facts cover this version/context variant",
                missing_evidence=tuple(missing),
            )
        # Most specific entry (longest when-clause) wins; deterministic
        # tie-break on status precedence then nothing further - entries are
        # already sorted so the order is stable.
        status_rank = {
            CapabilityStatus.UNSUPPORTED: 0,
            CapabilityStatus.CONDITIONAL: 1,
            CapabilityStatus.SUPPORTED: 2,
        }
        entry, status, reason, conditions = max(
            covered,
            key=lambda item: (
                len(item[0].when),
                -status_rank.get(item[1] or CapabilityStatus.UNKNOWN, 3),
            ),
        )
        assert status is not None  # filtered above
        return CapabilityResult(
            capability=capability,
            platform=ctx.platform,
            status=status,
            reason=reason or entry.reason,
            limitations=entry.limitations,
            conditions=conditions,
            source=entry.source,
            pack=entry.pack,
            pack_version=entry.pack_version,
            verified_at=entry.verified_at,
            entry_id=entry.id,
            matched_when=entry.when,
        )

    def supports(
        self,
        platform: str,
        capability: str,
        **kwargs: Any,
    ) -> CapabilityResult:
        """``evaluate`` convenience with platform first."""
        return self.evaluate(capability, platform=platform, **kwargs)

    def limitations(self, platform: str, capability: str | None = None) -> list[str]:
        """All declared limitations for a platform (optionally one capability)."""
        out: list[str] = []
        for entry in self._entries:
            if entry.platform != platform:
                continue
            if capability is not None and entry.id != capability:
                continue
            out.extend(entry.limitations)
        return sorted(set(out))

    def capabilities_for(self, platform: str) -> list[str]:
        """Capability ids declared for ``platform`` (sorted, deduped)."""
        return sorted({e.id for e in self._entries if e.platform == platform})

    def platforms(self) -> list[str]:
        return sorted({e.platform for e in self._entries})

    def capabilities(self) -> list[str]:
        """All capability ids declared across every platform."""
        return sorted({e.id for e in self._entries})

    def explain(
        self,
        platform: str,
        capability: str,
        **kwargs: Any,
    ) -> CapabilityResult:
        """Same as ``evaluate``; kept as a named API for CLI/introspection."""
        return self.evaluate(capability, platform=platform, **kwargs)

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _context_from_kwargs(kwargs: dict[str, Any]) -> CapabilityContext:
        fields = {k: str(v) for k, v in kwargs.items() if k in _CONTEXT_FIELDS and v is not None}
        attrs = tuple(
            sorted((str(k), str(v)) for k, v in kwargs.items() if k not in _CONTEXT_FIELDS)
        )
        platform = fields.pop("platform", "")
        return CapabilityContext(platform=platform, attributes=attrs, **fields)

    def _matching(self, ctx: CapabilityContext, capability: str) -> list[_Entry]:
        """Entries whose id matches and whose ``when`` clause is satisfied."""
        out: list[_Entry] = []
        for entry in self._entries:
            if entry.platform != ctx.platform or entry.id != capability:
                continue
            if all(ctx.attribute(k) == v for k, v in entry.when):
                out.append(entry)
        return out

    def _apply(
        self, entry: _Entry, ctx: CapabilityContext
    ) -> tuple[_Entry, CapabilityStatus | None, str, tuple[str, ...]]:
        """Resolve one entry against the context.

        ``status=None`` means the entry's ``versions`` map does not cover
        ``ctx.version`` - absence of proof, contributing UNKNOWN.
        """
        status = entry.status
        reason = entry.reason
        if entry.versions:
            if ctx.version is None:
                return entry, None, "", ()
            override = dict(entry.versions).get(ctx.version)
            if override is None:
                return entry, None, "", ()
            status = override
        # A proven-unsupported fact can't be softened by un-evaluatable
        # conditions - skip them only when the fact already fails.
        if status is CapabilityStatus.UNSUPPORTED:
            return entry, status, reason, ()
        unmet: list[str] = []
        for cond in entry.conditions:
            fail = _check_condition(cond, ctx)
            if fail is None:
                continue
            unmet.append(cond.text)
            status = fail
            if cond.reason:
                reason = cond.reason
        return entry, status, reason, tuple(unmet)

    @staticmethod
    def _unknown(
        ctx: CapabilityContext,
        capability: str,
        reason: str,
        missing_evidence: tuple[str, ...] = (),
    ) -> CapabilityResult:
        return CapabilityResult(
            capability=capability,
            platform=ctx.platform,
            status=CapabilityStatus.UNKNOWN,
            reason=reason,
            missing_evidence=missing_evidence,
        )


@cache
def capability_registry() -> CapabilityRegistry:
    """The bundled registry - built once from shipped knowledge packs."""
    return CapabilityRegistry()
