"""CycloneDX SBOM: the project, its deps, plugins, packs, and images.

forge-doctor-data is a scanner, not a vulnerability database - this emits the
inventory (CycloneDX 1.5) that real scanners (osv-scanner, trivy, depscan)
consume. No network calls.

v2: every locked package (transitives included) becomes a component and
the ``dependencies`` array mirrors the lock's own edges, so consumers see
root -> declared -> transitive depth. ``serialNumber`` is deterministic -
uuid5 of project name + version - identical across checkout locations.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING, Any

from forge_doctor_data import __version__

if TYPE_CHECKING:
    from forge_doctor_data.core.context import ProjectContext

_DEP_SPEC_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*(?:\[.*?\])?\s*(.*)$")
_FROM_RE = re.compile(r"^\s*FROM\s+(?:--\S+\s+)*(\S+)", re.IGNORECASE | re.MULTILINE)


@dataclass
class _LockPackage:
    name: str
    version: str = ""
    main: bool = True  # poetry groups/category say runtime vs dev
    deps: list[str] = field(default_factory=list)


def _declared_names(pyproject: dict[str, Any]) -> dict[str, str]:
    """Declared dependency names -> raw spec (project + poetry sections)."""
    declared: dict[str, str] = {}
    raw = pyproject.get("project", {}).get("dependencies", [])
    if isinstance(raw, list):
        for dep in raw:
            match = _DEP_SPEC_RE.match(str(dep))
            if match:
                declared[match.group(1).lower()] = (match.group(2) or "").strip()
    poetry = pyproject.get("tool", {}).get("poetry", {})
    poetry_deps = poetry.get("dependencies", {}) if isinstance(poetry, dict) else {}
    if isinstance(poetry_deps, dict):
        for name, spec in poetry_deps.items():
            if isinstance(spec, dict):
                spec = spec.get("version", "")
            declared[str(name).lower()] = str(spec or "")
    declared.pop("python", None)
    return declared


def _lock_packages(root: Path) -> dict[str, _LockPackage]:
    """poetry.lock ``[[package]]`` entries -> name -> package facts."""
    lock = root / "poetry.lock"
    if not lock.is_file():
        return {}
    try:
        payload = tomllib.loads(lock.read_text(encoding="utf-8", errors="replace"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    out: dict[str, _LockPackage] = {}
    for package in payload.get("package", []):
        if not isinstance(package, dict) or not package.get("name"):
            continue
        name = str(package["name"]).lower()
        groups = package.get("groups")
        if isinstance(groups, list):
            main = "main" in groups
        else:
            main = package.get("category", "main") in ("main", None)
        deps = package.get("dependencies", {})
        out[name] = _LockPackage(
            name=str(package["name"]),
            version=str(package.get("version", "")),
            main=main,
            deps=[str(d) for d in deps] if isinstance(deps, dict) else [],
        )
    return out


def _purl(name: str, version: str = "") -> str:
    key = name.lower()
    return f"pkg:pypi/{key}@{version}" if version else f"pkg:pypi/{key}"


def _plugin_components() -> list[dict[str, Any]]:
    from forge_doctor_data.plugins.discovery import discover_plugins

    components = []
    for plugin in discover_plugins():
        components.append(
            {
                "type": "library",
                "name": plugin.distribution or plugin.name,
                **({"version": plugin.version} if plugin.version else {}),
                "properties": [
                    {"name": "forge-doctor-data:entry-point", "value": plugin.name},
                    {"name": "forge-doctor-data:api-version", "value": plugin.api_version},
                ],
            }
        )
    return components


def _knowledge_components() -> list[dict[str, Any]]:
    from forge_doctor_data.core.knowledge import list_packs, pack_meta

    components = []
    for domain, name, pack in list_packs():
        meta = pack_meta(pack)
        components.append(
            {
                "type": "data",
                "name": f"forge-doctor-data-knowledge-{domain}-{name}",
                "version": str(meta["pack_version"]),
                "properties": [
                    {"name": "forge-doctor-data:domain", "value": domain},
                    {
                        "name": "forge-doctor-data:verified-at",
                        "value": str(meta["verified_at"] or ""),
                    },
                ],
            }
        )
    return components


def _image_components(ctx: ProjectContext) -> list[dict[str, Any]]:
    """Dockerfile FROM refs as container components."""
    images: list[dict[str, Any]] = []
    for relative in sorted(ctx.files):
        if relative.name.lower() != "dockerfile" and not relative.name.lower().startswith(
            "dockerfile."
        ):
            continue
        text = ctx.read_text(relative) or ""
        for i, image in enumerate(_FROM_RE.findall(text)):
            images.append(
                {
                    "type": "container",
                    "name": image,
                    "properties": [
                        {"name": "forge-doctor-data:file", "value": relative.as_posix()},
                        {"name": "forge-doctor-data:stage-index", "value": str(i)},
                    ],
                }
            )
    return images


def build_sbom(ctx: ProjectContext) -> dict[str, Any]:
    """CycloneDX 1.5 document for the scanned project."""
    import uuid
    from datetime import datetime

    pyproject = ctx.pyproject or {}
    project_name = (
        pyproject.get("project", {}).get("name")
        or pyproject.get("tool", {}).get("poetry", {}).get("name")
        or ctx.root.name
    )
    project_version = (
        pyproject.get("project", {}).get("version")
        or pyproject.get("tool", {}).get("poetry", {}).get("version")
        or "0"
    )
    authors = pyproject.get("project", {}).get("authors")
    author_names = (
        [a.get("name") for a in authors if isinstance(a, dict) and a.get("name")]
        if isinstance(authors, list)
        else []
    )

    declared = _declared_names(pyproject)
    locked = _lock_packages(ctx.root)

    components: list[dict[str, Any]] = []
    # Locked packages (declared + transitive) are the canonical inventory.
    for key in sorted(locked):
        package = locked[key]
        components.append(
            {
                "type": "library",
                "name": package.name,
                **({"version": package.version} if package.version else {}),
                "purl": _purl(package.name, package.version),
                "bom-ref": _purl(package.name, package.version),
                "scope": "required" if package.main else "optional",
            }
        )
    # Declared-but-unlocked specs still get a component (no version known).
    for key in sorted(set(declared) - set(locked)):
        spec = declared[key]
        version = spec if spec and spec[0].isdigit() else ""
        components.append(
            {
                "type": "library",
                "name": key,
                **({"version": version} if version else {}),
                "purl": _purl(key, version),
                "bom-ref": _purl(key, version),
                "scope": "required",
                "properties": [{"name": "forge-doctor-data:resolved", "value": "unlocked"}],
            }
        )

    components += _plugin_components() + _knowledge_components() + _image_components(ctx)

    # Dependency graph: root -> declared -> transitives, from lock edges.
    declared_refs = [
        _purl(locked[key].name, locked[key].version) if key in locked else _purl(key)
        for key in sorted(declared)
    ]
    dependencies: list[dict[str, Any]] = [{"ref": "root", "dependsOn": declared_refs}]
    for key in sorted(locked):
        package = locked[key]
        depends_on = [
            _purl(locked[d.lower()].name, locked[d.lower()].version)
            for d in sorted(package.deps)
            if d.lower() in locked
        ]
        dependencies.append({"ref": _purl(package.name, package.version), "dependsOn": depends_on})

    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        # Deterministic identity: project name+version, never the path.
        "serialNumber": (
            f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, f'{project_name}@{project_version}')}"
        ),
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(UTC).isoformat(),
            "tools": {
                "components": [
                    {
                        "type": "application",
                        "name": "forge-doctor-data",
                        "version": __version__,
                    }
                ]
            },
            "component": {
                "type": "application",
                "name": str(project_name),
                "bom-ref": "root",
                **({"version": str(project_version)} if project_version != "0" else {}),
                **({"authors": [{"name": n} for n in author_names]} if author_names else {}),
            },
        },
        "components": components,
        "dependencies": dependencies,
    }
