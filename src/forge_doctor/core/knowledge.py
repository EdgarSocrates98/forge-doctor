"""Knowledge packs: version/compat data shipped as JSON inside the wheel.

The engine stays stable while knowledge changes - packs are data, not code.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from functools import cache
from importlib.resources import files
from typing import Any


@cache
def load_pack(domain: str, name: str = "versions") -> dict[str, Any]:
    """Load ``knowledge/<domain>/<name>.json``; empty dict when missing."""
    resource = files("forge_doctor") / "knowledge" / domain / f"{name}.json"
    try:
        payload: dict[str, Any] = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload


def glue_versions() -> dict[str, dict[str, Any]]:
    pack = load_pack("glue")
    versions = pack.get("versions", {})
    return versions if isinstance(versions, dict) else {}


def glue_current() -> str:
    return str(load_pack("glue").get("current", "6.0"))


def glue_status(version: str) -> str:
    """``eol`` | ``aging`` | ``supported`` | ``current`` | ``unknown``."""
    entry = glue_versions().get(version)
    return str(entry.get("status", "unknown")) if isinstance(entry, dict) else "unknown"


def glue_migration_changes(source: str, target: str) -> list[dict[str, Any]]:
    """Ordered change list for migrating ``source`` -> ``target``.

    Every target version strictly above ``source`` contributes its changes,
    in ascending version order.
    """
    pack = load_pack("glue", "compatibility")
    targets = pack.get("targets", {})
    if not isinstance(targets, dict):
        return []
    changes: list[dict[str, Any]] = []
    for version in sorted(targets, key=_version_sort):
        if _version_sort(source) < _version_sort(version) <= _version_sort(target):
            entries = targets[version].get("changes", [])
            changes.extend(e for e in entries if isinstance(e, dict))
    return changes


def _version_sort(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in str(version).split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


# ---------------------------------------------------------------------------
# Pack provenance (schema_version 2): pack_version, verified_at, sources.
# ---------------------------------------------------------------------------

STALE_DAYS = 90


def list_packs() -> list[tuple[str, str, dict[str, Any]]]:
    """``(domain, name, pack)`` for every bundled knowledge file."""
    root = files("forge_doctor") / "knowledge"
    packs: list[tuple[str, str, dict[str, Any]]] = []
    try:
        domains = sorted(d.name for d in root.iterdir() if d.is_dir())
    except OSError:
        return packs
    for domain in domains:
        domain_dir = root / domain
        try:
            entries = sorted(e.name for e in domain_dir.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.endswith(".json"):
                packs.append((domain, entry[:-5], load_pack(domain, entry[:-5])))
    return packs


def pack_meta(pack: dict[str, Any]) -> dict[str, Any]:
    """Provenance fields with v1 defaults (v1 packs lack them entirely)."""
    return {
        "schema_version": pack.get("schema_version", 1),
        "pack_version": pack.get("pack_version", "-"),
        "verified_at": pack.get("verified_at"),
        "sources": pack.get("sources", []),
    }


def verify_pack(domain: str, name: str, today: date | None = None) -> list[str]:
    """Structural + staleness issues for one pack; empty list = healthy."""

    issues: list[str] = []
    pack = load_pack(domain, name)
    if not pack:
        return [f"{domain}/{name}: missing or unreadable"]
    meta = pack_meta(pack)
    if meta["schema_version"] != 2:
        issues.append(f"{domain}/{name}: schema_version {meta['schema_version']} (expected 2)")
    if not meta["pack_version"] or meta["pack_version"] == "-":
        issues.append(f"{domain}/{name}: no pack_version")
    verified = meta["verified_at"]
    if not verified:
        issues.append(f"{domain}/{name}: no verified_at")
    else:
        try:
            verified_date = date.fromisoformat(str(verified))
            if (today or date.today()) - verified_date > timedelta(days=STALE_DAYS):
                issues.append(
                    f"{domain}/{name}: verified_at {verified} is older than {STALE_DAYS}d"
                )
        except ValueError:
            issues.append(f"{domain}/{name}: malformed verified_at {verified!r}")
    if not meta["sources"]:
        issues.append(f"{domain}/{name}: no sources[]")
    return issues
