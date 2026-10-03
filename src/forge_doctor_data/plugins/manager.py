"""Plugin lifecycle management: scaffold, install, integrity pinning.

Deterministic and offline except ``install_plugin``, which shells out to
the environment's installer only at the user's explicit request.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import Distribution, distributions
from pathlib import Path

LOCK_FILE = ".forge-doctor-data/plugins.lock"
LOCK_VERSION = 1


@dataclass(frozen=True)
class PluginPin:
    """One locked plugin: distribution + content digest at lock time."""

    name: str
    version: str | None
    digest: str  # sha256 of the sorted file digests


@dataclass(frozen=True)
class VerifyResult:
    name: str
    status: str  # ok | changed | missing | added (installed but not locked)
    detail: str = ""


def _dist_files_digest(dist: Distribution) -> str:
    """sha256 over sorted (path, sha256) of every installed file.

    Reads the dist's RECORD entries; per-entry digests are recomputed
    from file content so tampering with RECORD alone is caught.
    """
    entries: list[tuple[str, str]] = []
    for file in dist.files or ():
        path = file if isinstance(file, Path) else Path(str(file))
        try:
            located = Path(str(dist.locate_file(path)))
        except Exception:
            continue
        if not located.is_file():
            continue
        digest = hashlib.sha256(located.read_bytes()).hexdigest()
        entries.append((path.as_posix(), digest))
    entries.sort()
    blob = "\n".join(f"{h} {p}" for p, h in entries).encode()
    return hashlib.sha256(blob).hexdigest()


def _plugin_distributions() -> dict[str, Distribution]:
    """Installed distributions that register a forge_doctor_data.checks entry point."""
    from forge_doctor_data.plugins.discovery import ENTRY_POINT_GROUP

    found: dict[str, Distribution] = {}
    for dist in distributions():
        for ep in dist.entry_points:
            if ep.group == ENTRY_POINT_GROUP:
                found[dist.name or ep.value] = dist
                break
    return found


def lock_plugins(root: Path) -> list[PluginPin]:
    """Write ``.forge-doctor-data/plugins.lock`` pinning every installed plugin."""
    dists = _plugin_distributions()
    pins = [
        PluginPin(name=name, version=dist.version, digest=_dist_files_digest(dist))
        for name, dist in sorted(dists.items())
    ]
    lock_path = root / LOCK_FILE
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(
        json.dumps(
            {
                "lock_version": LOCK_VERSION,
                "plugins": [
                    {"name": p.name, "version": p.version, "sha256": p.digest} for p in pins
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return pins


def _read_lock(root: Path) -> dict[str, PluginPin]:
    lock_path = root / LOCK_FILE
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    pins: dict[str, PluginPin] = {}
    for entry in data.get("plugins", []):
        name = entry.get("name")
        if name:
            pins[name] = PluginPin(name, entry.get("version"), entry.get("sha256"))
    return pins


def verify_plugins(root: Path) -> list[VerifyResult]:
    """Compare installed plugin dists against the lock file.

    ``missing`` = locked but not installed; ``added`` = installed but not
    locked; ``changed`` = digest drift (files modified since lock).
    """
    locked = _read_lock(root)
    installed = _plugin_distributions()
    results: list[VerifyResult] = []
    for name in sorted(set(locked) | set(installed)):
        pin = locked.get(name)
        dist = installed.get(name)
        if pin is None:
            results.append(VerifyResult(name, "added", "installed but not in lock"))
            continue
        if dist is None:
            results.append(VerifyResult(name, "missing", "locked but not installed"))
            continue
        digest = _dist_files_digest(dist)
        if digest != pin.digest:
            results.append(VerifyResult(name, "changed", "files differ from lock"))
        elif pin.version and dist.version != pin.version:
            results.append(
                VerifyResult(name, "changed", f"version {dist.version} != {pin.version}")
            )
        else:
            results.append(VerifyResult(name, "ok"))
    return results


# ---------------------------------------------------------------- scaffold

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")


def _module_name(dist_name: str) -> str:
    return dist_name.replace("-", "_")


def scaffold_plugin(dist_name: str, dest: Path) -> list[Path]:
    """Create a plugin package skeleton under ``dest/<dist_name>``.

    Refuses to overwrite existing files. Returns written paths.
    """
    if not _NAME_RE.match(dist_name):
        raise ValueError(
            f"invalid distribution name {dist_name!r} - use lowercase letters, digits, hyphens"
        )
    module = _module_name(dist_name)
    pkg = module.removeprefix("forge_doctor_data_")
    check_id_prefix = pkg[:4].upper().replace("_", "") or "MYP"
    target = dest / dist_name
    files = {
        "pyproject.toml": f'''[build-system]
requires = ["poetry-core>=2.0"]
build-backend = "poetry.core.masonry.api"

[project]
name = "{dist_name}"
version = "0.1.0"
description = "Forge Doctor Data plugin: {pkg}"
requires-python = ">=3.11"
dependencies = ["forge-doctor-data>=0.7,<2.0"]

[project.entry-points."forge_doctor_data.checks"]
{pkg} = "{module}.checks:describe"
''',
        f"{module}/__init__.py": "",
        f"{module}/checks.py": f'''"""{dist_name} - Forge Doctor Data plugin checks."""

from forge_doctor_data.sdk import (
    CURRENT_API_VERSION,
    CheckBase,
    PluginDescriptor,
    Severity,
)


class FirstCheck(CheckBase):
    """Example check - replace with real diagnostics."""

    id = "{check_id_prefix}001"
    title = "Example {pkg} check"
    category = "{pkg}"

    def run(self, ctx):
        if ctx.has_file("README.md"):
            return [self.result(Severity.PASS, "README.md present")]
        return [
            self.result(
                Severity.INFO, "no README.md", "Add a README.", file=None
            )
        ]


def describe() -> PluginDescriptor:
    return PluginDescriptor(
        name="{dist_name}",
        version="0.1.0",
        api_version=CURRENT_API_VERSION,
        checks=(FirstCheck(),),
        requires_forge_doctor_data=">=0.7,<2.0",
    )
''',
        "tests/test_checks.py": f"""from pathlib import Path

from {module}.checks import FirstCheck, describe


def test_descriptor_is_compatible() -> None:
    import forge_doctor_data

    assert describe().check_compatibility(forge_doctor_data.__version__) is None


def test_check_runs(tmp_path: Path) -> None:
    from forge_doctor_data.sdk import ProjectContext

    (tmp_path / "README.md").write_text("# x")
    results = FirstCheck().run(ProjectContext(root=tmp_path))
    assert results[0].severity.value == "pass"
""",
        "README.md": f"# {dist_name}\n\nForge Doctor Data plugin providing `{pkg}` checks.\n",
    }
    written: list[Path] = []
    for rel, content in files.items():
        path = target / rel
        if path.exists():
            raise FileExistsError(f"refusing to overwrite {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append(path)
    return written


# ------------------------------------------------------------------ install


def _default_runner(cmd: list[str]) -> int:
    return subprocess.run(cmd, check=False).returncode


def install_plan(dist: str) -> list[str]:
    """Resolve the install command: pipx inject when pipx exists, else pip."""
    if shutil.which("pipx"):
        return ["pipx", "inject", "forge-doctor-data", dist]
    import sys

    return [sys.executable, "-m", "pip", "install", dist]


def install_plugin(
    dist: str,
    *,
    runner: Callable[[list[str]], int] = _default_runner,
) -> tuple[list[str], int]:
    """Install a plugin distribution into the forge-doctor-data environment.

    Returns ``(command, exit_code)``. Network access happens inside the
    environment's installer - never inside the scan engine.
    """
    cmd = install_plan(dist)
    return cmd, runner(cmd)
