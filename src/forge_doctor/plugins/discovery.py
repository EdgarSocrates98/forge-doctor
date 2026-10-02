"""External check discovery via the ``forge_doctor.checks`` entry-point group.

Designed so ``pipx inject forge-doctor forge-doctor-<ext>`` just works: entry
points are loaded defensively; a broken plugin degrades to nothing rather
than crashing a scan.

Plugin SDK v2: an entry point may resolve to a :class:`PluginDescriptor`
(or a callable/class producing one) carrying ``api_version`` and an optional
``requires_forge_doctor`` specifier - incompatible descriptors are rejected
with a readable error instead of loading.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from typing import Any

from forge_doctor.plugins.protocol import (
    Check,
    PluginDescriptor,
    PluginIdentity,
)

ENTRY_POINT_GROUP = "forge_doctor.checks"


@dataclass(frozen=True)
class PluginInfo:
    """What we know about an installed external plugin without trusting it."""

    name: str
    distribution: str | None = None
    version: str | None = None
    api_version: str = "1"
    # None when the plugin is loadable; otherwise the reason it is not.
    status: str | None = None


@dataclass(frozen=True)
class LoadedCheck:
    """A check plus the identity of the plugin that produced it."""

    check: Check
    identity: PluginIdentity


def iter_entry_points() -> list[EntryPoint]:
    return list(entry_points(group=ENTRY_POINT_GROUP))


def _dist_info(ep: EntryPoint) -> tuple[str | None, str | None]:
    dist = getattr(ep, "dist", None)
    name = getattr(dist, "name", None) or getattr(
        getattr(dist, "metadata", None), "get", lambda _k: None
    )("name")
    return name, getattr(dist, "version", None)


def discover_plugins() -> list[PluginInfo]:
    """List installed plugins (metadata only - does not load code)."""
    plugins: list[PluginInfo] = []
    for ep in iter_entry_points():
        dist_name, dist_version = _dist_info(ep)
        plugins.append(
            PluginInfo(
                name=ep.name,
                distribution=dist_name,
                version=dist_version,
            )
        )
    return plugins


def _resolve_entry(ep: EntryPoint) -> Any:
    loaded = ep.load()
    return loaded() if isinstance(loaded, type) else loaded


# Check ids look like SPARK001/DBX-E001: uppercase stem + digits. Anything
# else in ``allow``/``trusted`` is treated as a distribution or entry-point
# identity, which is knowable WITHOUT executing plugin code.
_CHECK_ID_RE = re.compile(r"^[A-Z][A-Z0-9_]*-?\d+$")


def _looks_like_check_id(entry: str) -> bool:
    return bool(_CHECK_ID_RE.match(entry))


def _ep_trusted(
    ep: EntryPoint,
    trusted: tuple[str, ...],
    allow_identities: frozenset[str] | None,
    strict: bool = False,
) -> bool:
    """Pre-load trust gate. Uses entry-point metadata only - never loads.

    ``strict`` = default-deny: a plugin loads only when its distribution
    or entry point is in ``trusted``; ``allow`` identities do not grant
    load permission in strict mode.
    """
    if strict or trusted:
        dist_name, _ = _dist_info(ep)
        return ep.name in trusted or (dist_name is not None and dist_name in trusted)
    if allow_identities is not None:
        dist_name, _ = _dist_info(ep)
        return ep.name in allow_identities or (
            dist_name is not None and dist_name in allow_identities
        )
    return True


def split_allow(allow: tuple[str, ...]) -> tuple[frozenset[str] | None, frozenset[str]]:
    """Split legacy ``allow`` into (identity entries, check-id entries).

    Identity entries (distribution/entry-point names) can gate BEFORE load.
    Check-id entries can only filter AFTER load; when ``allow`` contains
    only check ids there is no pre-load gate (legacy behavior) and a note
    is returned advising ``trusted`` instead.
    """
    identities = frozenset(e for e in allow if not _looks_like_check_id(e))
    check_ids = frozenset(e.upper() for e in allow if _looks_like_check_id(e))
    if not allow:
        return None, frozenset()
    return (identities or None), check_ids


def _descriptor_checks(
    descriptor: PluginDescriptor, ep: EntryPoint
) -> tuple[list[LoadedCheck], str | None]:
    """Expand a v2 descriptor into checks, or a rejection reason."""
    from forge_doctor import __version__

    reason = descriptor.check_compatibility(__version__)
    if reason is not None:
        return [], reason
    dist_name, dist_version = _dist_info(ep)
    identity = PluginIdentity(
        distribution=dist_name or descriptor.name,
        version=dist_version or descriptor.version,
        api_version=descriptor.api_version,
        entry_point=ep.name,
    )
    checks: list[LoadedCheck] = []
    for check in descriptor.checks:
        candidate = check() if isinstance(check, type) else check
        if isinstance(candidate, Check):
            checks.append(LoadedCheck(check=candidate, identity=identity))
        else:
            return [], f"{descriptor.name}: {candidate!r} is not a Check"
    return checks, None


def load_plugins(
    trusted: tuple[str, ...] = (),
    allow: tuple[str, ...] = (),
    strict: bool = False,
) -> tuple[list[LoadedCheck], list[PluginInfo], list[str]]:
    """Load plugin checks with identities. ``(checks, infos, errors)``.

    ``trusted`` (distribution/entry-point names) and identity entries in
    ``allow`` gate BEFORE ``ep.load()`` - untrusted plugins never execute
    code. Check-id entries in ``allow`` filter post-load at the registry.
    ``strict`` is default-deny: only ``trusted`` may load.
    """
    allow_identities, _check_ids = split_allow(allow)
    checks: list[LoadedCheck] = []
    infos: list[PluginInfo] = []
    errors: list[str] = []
    untrusted_reason = (
        "untrusted: strict mode requires plugins.trusted - never loaded"
        if strict
        else "untrusted: not in plugins.trusted/allow - never loaded"
    )
    for ep in iter_entry_points():
        dist_name, dist_version = _dist_info(ep)
        if not _ep_trusted(ep, trusted, allow_identities, strict):
            infos.append(
                PluginInfo(
                    name=ep.name,
                    distribution=dist_name,
                    version=dist_version,
                    status=untrusted_reason,
                )
            )
            continue
        try:
            loaded = _resolve_entry(ep)
            if isinstance(loaded, PluginDescriptor):
                expanded, reason = _descriptor_checks(loaded, ep)
                if reason is not None:
                    infos.append(
                        PluginInfo(
                            name=ep.name,
                            distribution=dist_name or loaded.name,
                            version=dist_version or loaded.version,
                            api_version=loaded.api_version,
                            status=f"incompatible: {reason}",
                        )
                    )
                    continue
                checks.extend(expanded)
                infos.append(
                    PluginInfo(
                        name=ep.name,
                        distribution=dist_name or loaded.name,
                        version=dist_version or loaded.version,
                        api_version=loaded.api_version,
                    )
                )
            elif isinstance(loaded, Check):
                checks.append(
                    LoadedCheck(
                        check=loaded,
                        identity=PluginIdentity(
                            distribution=dist_name or ep.name,
                            version=dist_version,
                            api_version="1",
                            entry_point=ep.name,
                        ),
                    )
                )
                infos.append(
                    PluginInfo(
                        name=ep.name,
                        distribution=dist_name,
                        version=dist_version,
                        api_version="1",
                    )
                )
            elif callable(loaded):
                produced = loaded()
                if isinstance(produced, PluginDescriptor):
                    expanded, reason = _descriptor_checks(produced, ep)
                    if reason is not None:
                        infos.append(
                            PluginInfo(
                                name=ep.name,
                                distribution=dist_name or produced.name,
                                version=dist_version or produced.version,
                                api_version=produced.api_version,
                                status=f"incompatible: {reason}",
                            )
                        )
                        continue
                    checks.extend(expanded)
                    infos.append(
                        PluginInfo(
                            name=ep.name,
                            distribution=dist_name or produced.name,
                            version=dist_version or produced.version,
                            api_version=produced.api_version,
                        )
                    )
                elif isinstance(produced, Check):
                    checks.append(
                        LoadedCheck(
                            check=produced,
                            identity=PluginIdentity(
                                distribution=dist_name or ep.name,
                                version=dist_version,
                                api_version="1",
                                entry_point=ep.name,
                            ),
                        )
                    )
                    infos.append(
                        PluginInfo(
                            name=ep.name,
                            distribution=dist_name,
                            version=dist_version,
                            api_version="1",
                        )
                    )
                else:
                    errors.append(f"{ep.name}: produced non-Check {produced!r}")
            else:
                errors.append(f"{ep.name}: not a Check")
        except Exception as exc:
            errors.append(f"{ep.name}: {exc}")
    return checks, infos, errors


def load_plugin_checks(
    trusted: tuple[str, ...] = (),
    allow: tuple[str, ...] = (),
    strict: bool = False,
) -> tuple[list[Check], list[str]]:
    """Legacy shape used by older call sites: ``(checks, load_errors)``."""
    loaded, _infos, errors = load_plugins(trusted=trusted, allow=allow, strict=strict)
    checks: list[Check] = []
    for item in loaded:
        item.check.__fd_source__ = item.identity.distribution  # type: ignore[attr-defined]
        item.check.__fd_identity__ = item.identity  # type: ignore[attr-defined]
        checks.append(item.check)
    return checks, errors
