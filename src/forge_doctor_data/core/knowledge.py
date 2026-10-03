"""Knowledge packs: version/compat data shipped as JSON inside the wheel.

The engine stays stable while knowledge changes - packs are data, not code.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Any


@cache
def load_pack(domain: str, name: str = "versions") -> dict[str, Any]:
    """Load ``knowledge/<domain>/<name>.json``; empty dict when missing."""
    resource = files("forge_doctor_data") / "knowledge" / domain / f"{name}.json"
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
    root = files("forge_doctor_data") / "knowledge"
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


# ---------------------------------------------------------------------------
# Supply-chain tooling: scaffold / semantic diff / conformance / publish
# ---------------------------------------------------------------------------

SCAFFOLD_KINDS = ("versions", "errors", "capabilities", "compatibility")


def _today() -> date:
    return date.today()


def _next_pack_version(day: date) -> str:
    return f"{day.year}.{day.month}.{day.day}"


def scaffold_pack(domain: str, kind: str = "versions") -> dict[str, Any]:
    """A new pack skeleton with correct provenance fields and one example."""
    if kind not in SCAFFOLD_KINDS:
        raise ValueError(f"unknown pack kind {kind!r} (choose from {SCAFFOLD_KINDS})")
    base: dict[str, Any] = {
        "schema_version": 2,
        "pack_version": _next_pack_version(_today()),
        "verified_at": _today().isoformat(),
        "sources": ["https://example.com/TODO-official-docs"],
        "domain": domain,
    }
    if kind == "versions":
        base["current"] = "1.0"
        base["versions"] = {"1.0": {"status": "supported"}}
    elif kind == "errors":
        base["errors"] = [
            {
                "id": f"{domain[:4].upper()}-E001",
                "title": "Example error signature",
                "patterns": ["re:example.*error"],
                "examples": ["ExampleError: this is an example error line"],
                "causes": ["describe the root cause"],
                "severity": "error",
            }
        ]
    elif kind == "capabilities":
        base["capabilities"] = [
            {
                "id": "EXAMPLE_CAPABILITY",
                "platform": domain,
                "status": "supported",
                "versions": {"1.0": "supported"},
                "conditions": [],
                "reason": "Why this capability is available.",
                "limitations": [],
                "source": "https://example.com/TODO-official-docs",
            }
        ]
    else:  # compatibility
        base["targets"] = {"1.0": {"changes": []}}
    return base


def write_scaffold(root: Path, domain: str, kind: str = "versions") -> Path:
    """Write a scaffold under ``<root>/<domain>/<kind>.json``; refuses to
    overwrite an existing pack."""
    target = root / domain / f"{kind}.json"
    if target.exists():
        raise FileExistsError(f"{target} already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(scaffold_pack(domain, kind), indent=2) + "\n", encoding="utf-8")
    return target


# -- semantic diff -----------------------------------------------------------


def _index_section(content: Any) -> dict[str, Any] | None:
    """Index a pack section by entry key/id; ``None`` for plain scalars."""
    if isinstance(content, dict):
        return {str(k): v for k, v in content.items()}
    if isinstance(content, list) and all(isinstance(e, dict) and "id" in e for e in content):
        return {str(e["id"]): e for e in content}
    return None


def _flatten(value: Any, prefix: str = "") -> dict[str, str]:
    """``{"a": {"b": 1}}`` -> ``{"a.b": "1"}`` for leaf-level diffs."""
    if isinstance(value, dict):
        out: dict[str, str] = {}
        for key in sorted(value):
            out.update(_flatten(value[key], f"{prefix}.{key}" if prefix else str(key)))
        return out
    if isinstance(value, list):
        out = {}
        for i, item in enumerate(value):
            out.update(_flatten(item, f"{prefix}[{i}]"))
        return out
    return {prefix: json.dumps(value, sort_keys=True)}


def diff_packs(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    """Semantic diff of two pack payloads.

    Returns deterministic rows: ``{section, id, change, details}`` where
    change is ``added``/``removed``/``changed``; ``details`` lists
    leaf-level ``path: old -> new`` strings for changed entries.
    Meta fields (schema_version/pack_version/verified_at/sources) are
    excluded - this is a content diff, not a bookkeeping diff.
    """
    _META = {"schema_version", "pack_version", "verified_at", "sources", "domain"}
    rows: list[dict[str, Any]] = []
    for section in sorted(set(old) | set(new)):
        if section in _META:
            continue
        old_idx = _index_section(old.get(section))
        new_idx = _index_section(new.get(section))
        if old_idx is None or new_idx is None:
            if old.get(section) != new.get(section):
                rows.append(
                    {
                        "section": section,
                        "id": section,
                        "change": "changed",
                        "details": [
                            f"{json.dumps(old.get(section), sort_keys=True)} -> "
                            f"{json.dumps(new.get(section), sort_keys=True)}"
                        ],
                    }
                )
            continue
        for ident in sorted(set(new_idx) - set(old_idx)):
            rows.append({"section": section, "id": ident, "change": "added", "details": []})
        for ident in sorted(set(old_idx) - set(new_idx)):
            rows.append({"section": section, "id": ident, "change": "removed", "details": []})
        for ident in sorted(set(old_idx) & set(new_idx)):
            if old_idx[ident] == new_idx[ident]:
                continue
            flat_old, flat_new = _flatten(old_idx[ident]), _flatten(new_idx[ident])
            details = [
                f"{k}: {flat_old.get(k, '<absent>')} -> {flat_new.get(k, '<absent>')}"
                for k in sorted(set(flat_old) | set(flat_new))
                if flat_old.get(k) != flat_new.get(k)
            ]
            rows.append(
                {
                    "section": section,
                    "id": ident,
                    "change": "changed",
                    "details": details,
                }
            )
    return rows


def load_pack_ref(ref: str) -> dict[str, Any] | None:
    """Resolve ``<path>.json`` or ``domain/name`` to a pack payload."""
    path = Path(ref)
    if path.is_file() or ref.endswith(".json"):
        try:
            payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            return payload
        except (OSError, json.JSONDecodeError):
            return None
    if "/" in ref:
        domain, _, name = ref.partition("/")
        pack = load_pack(domain, name or "versions")
        return pack or None
    pack = load_pack(ref)
    return pack or None


# -- conformance -------------------------------------------------------------


def _pattern_matches(pattern: str, text: str) -> bool:
    from forge_doctor_data.core.diagnose import _match_positions

    return bool(_match_positions(text, pattern))


def conformance(
    packs: list[tuple[str, str, Any]] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Pack conformance suite: ``{issues, warnings}`` across all packs.

    Hard issues: malformed error entries, non-compiling ``re:``
    patterns, examples that match no pattern, and capability assertions
    that mis-evaluate on a sample context. Warnings: entries lacking a
    positive ``examples`` fixture. (Structural/provenance issues come
    from ``verify_pack`` and are merged in.)
    """
    resolved: list[tuple[str, str, Any]] = list(list_packs()) if packs is None else packs
    issues: list[str] = []
    warnings: list[str] = []

    for domain, name, pack in resolved:
        ident = f"{domain}/{name}"
        if pack is None or not isinstance(pack, dict):
            issues.append(f"{ident}: missing or unreadable")
            continue
        meta = pack_meta(pack)
        if meta["schema_version"] != 2:
            issues.append(f"{ident}: schema_version {meta['schema_version']} (expected 2)")
        if not meta["pack_version"] or meta["pack_version"] == "-":
            issues.append(f"{ident}: no pack_version")
        verified = meta["verified_at"]
        if not verified:
            issues.append(f"{ident}: no verified_at")
        else:
            try:
                verified_date = date.fromisoformat(str(verified))
                day = today or date.today()
                if day - verified_date > timedelta(days=STALE_DAYS):
                    issues.append(f"{ident}: verified_at {verified} older than {STALE_DAYS}d")
            except ValueError:
                issues.append(f"{ident}: malformed verified_at {verified!r}")
        if not meta["sources"]:
            issues.append(f"{ident}: no sources[]")
        errors = pack.get("errors")
        if isinstance(errors, list):
            _check_errors(ident, errors, issues, warnings)

    # Capability assertions: validate structure via the real registry and
    # re-evaluate every declared version on a sample context.
    cap_packs = [
        (name, f"capabilities/{name}", pack)
        for domain, name, pack in resolved
        if domain == "capabilities" and pack
    ]
    if cap_packs:
        from forge_doctor_data.core.capabilities import (
            CapabilityContext,
            CapabilityRegistry,
            CapabilityStatus,
        )

        registry = CapabilityRegistry(packs=cap_packs)
        issues.extend(registry.validation_issues)
        for default_platform, pack_id, pack in cap_packs:
            for cap in pack.get("capabilities", []):
                if not isinstance(cap, dict) or "id" not in cap:
                    continue
                versions = cap.get("versions", {})
                if not isinstance(versions, dict):
                    continue
                constrained = bool(cap.get("conditions") or cap.get("when"))
                for ver, declared in sorted(versions.items()):
                    result = registry.evaluate(
                        str(cap["id"]),
                        context=CapabilityContext(
                            platform=str(cap.get("platform", default_platform)),
                            version=str(ver),
                        ),
                    )
                    if not constrained and result.status is CapabilityStatus.UNKNOWN:
                        issues.append(
                            f"{pack_id}: {cap['id']}@{ver} evaluates unknown (declared {declared})"
                        )
                    elif not constrained and result.status.value != str(declared).lower():
                        issues.append(
                            f"{pack_id}: {cap['id']}@{ver} declared {declared} "
                            f"but evaluates {result.status.value}"
                        )
    return {"issues": sorted(set(issues)), "warnings": sorted(set(warnings))}


def _check_errors(
    ident: str,
    errors: list[Any],
    issues: list[str],
    warnings: list[str],
) -> None:
    for entry in errors:
        if not isinstance(entry, dict):
            issues.append(f"{ident}: error entry is not an object")
            continue
        eid = entry.get("id") or "<missing id>"
        patterns = entry.get("patterns", [])
        if not patterns or not isinstance(patterns, list):
            issues.append(f"{ident}: {eid} has no patterns")
            continue
        for pattern in patterns:
            if isinstance(pattern, str) and pattern.startswith("re:"):
                try:
                    re.compile(pattern[3:])
                except re.error as exc:
                    issues.append(f"{ident}: {eid} broken regex {pattern!r}: {exc}")
        examples = entry.get("examples", [])
        if not isinstance(examples, list):
            issues.append(f"{ident}: {eid} examples must be a list")
            continue
        # A plain-substring pattern matches its own literal text, so an
        # entry with at least one always has a derivable positive fixture;
        # only re:-only entries need an explicit examples fixture.
        only_regex = all(isinstance(p, str) and p.startswith("re:") for p in patterns)
        if not examples:
            if only_regex:
                warnings.append(f"{ident}: {eid} has no positive example")
            continue
        for example in examples:
            if not isinstance(example, str) or not any(
                _pattern_matches(p, example) for p in patterns if isinstance(p, str)
            ):
                issues.append(f"{ident}: {eid} example matches no pattern: {example!r}")


# -- publish -----------------------------------------------------------------


def publish_checklist(domain: str, today: date | None = None) -> dict[str, Any]:
    """Publish readiness for one domain: freshness fields + provenance.

    Dry-run only — nothing is written; distribution stays manual.
    """
    day = today or date.today()
    rows: list[dict[str, Any]] = []
    found = False
    for d, name, pack in list_packs():
        if d != domain:
            continue
        found = True
        issues = verify_pack(d, name, today=day)
        meta = pack_meta(pack)
        rows.append(
            {
                "pack": f"{d}/{name}",
                "schema_version": meta["schema_version"],
                "pack_version": meta["pack_version"],
                "verified_at": meta["verified_at"],
                "sources": len(meta["sources"]),
                "issues": issues,
                "ok": not issues,
            }
        )
    if not found:
        return {"domain": domain, "packs": [], "ready": False, "error": "no packs"}
    return {
        "domain": domain,
        "packs": rows,
        "ready": all(r["ok"] for r in rows),
        "next_pack_version": _next_pack_version(day),
        "note": "dry-run validation only - distribution is manual",
    }


def bump_pack(root: Path, domain: str, today: date | None = None) -> list[Path]:
    """Rewrite ``pack_version``/``verified_at`` for every pack in a domain
    directory. Used by ``knowledge publish --bump``."""
    day = today or date.today()
    domain_dir = root / domain
    written: list[Path] = []
    if not domain_dir.is_dir():
        return written
    for path in sorted(domain_dir.glob("*.json")):
        try:
            pack = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        pack["pack_version"] = _next_pack_version(day)
        pack["verified_at"] = day.isoformat()
        path.write_text(json.dumps(pack, indent=2) + "\n", encoding="utf-8")
        written.append(path)
    return written
