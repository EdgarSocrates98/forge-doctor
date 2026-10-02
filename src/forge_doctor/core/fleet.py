"""Fleet/estate intelligence: manifest-driven multi-repo analysis.

A fleet manifest is a YAML/JSON file listing repositories — or a
directory scanned via workspace discovery:

.. code-block:: yaml

    fleet: data-platform-estate
    root: ../repos            # optional: workspace discovery over a dir
    repos:                    # explicit paths relative to the manifest
      - ./ingestion
      - path: ./analytics
        name: analytics-team

Everything after discovery reuses :func:`merge_repos` — the same merged
``DataPlatformGraph`` + cross-repo DEFINES/IMPLEMENTS/INVOKES links the
workspace path produces. Read-only and offline: repos are local paths.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from forge_doctor.core.context import ProjectContext
from forge_doctor.core.contract import _load_yaml
from forge_doctor.core.workspace import (
    WorkspaceModel,
    WorkspaceRepo,
    discover_repos,
    merge_repos,
)


class FleetManifestError(ValueError):
    """Raised when a fleet manifest cannot be loaded or resolved."""


@dataclass(frozen=True)
class FleetRepo:
    """One manifest entry resolved to a repository directory."""

    name: str
    path: Path  # absolute


@dataclass(frozen=True)
class FleetManifest:
    """A parsed fleet manifest: named repos or a workspace root."""

    path: Path  # the manifest file (or the workspace dir itself)
    name: str
    repos: tuple[FleetRepo, ...] = ()
    workspace_root: Path | None = None  # set when `root:` discovery is used


def load_manifest(path: Path) -> FleetManifest:
    """Load a fleet manifest. ``path`` may be a YAML/JSON manifest file or
    a directory (→ workspace discovery over that directory)."""
    if path.is_dir():
        return FleetManifest(path=path, name=path.name, workspace_root=path)
    if not path.is_file():
        raise FleetManifestError(f"not a file or directory: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FleetManifestError(f"cannot read {path}: {exc}") from exc
    if path.suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise FleetManifestError(f"{path}: malformed JSON: {exc}") from exc
    else:
        data, err = _load_yaml(text)
        if err is not None:
            raise FleetManifestError(f"{path}: {err}")
    if isinstance(data, list):
        data = {"repos": data}
    if not isinstance(data, dict):
        raise FleetManifestError(f"{path}: manifest must be a mapping or list")

    base = path.parent
    name = str(data.get("fleet", path.stem))
    raw_root = data.get("root")
    workspace_root: Path | None = None
    if raw_root is not None:
        workspace_root = (base / str(raw_root)).resolve()
        if not workspace_root.is_dir():
            raise FleetManifestError(f"{path}: root '{raw_root}' is not a directory")

    raw_repos = data.get("repos", [])
    if not isinstance(raw_repos, list):
        raise FleetManifestError(f"{path}: 'repos' must be a list")
    repos: list[FleetRepo] = []
    seen: set[Path] = set()
    for i, entry in enumerate(raw_repos):
        if isinstance(entry, str):
            rel, rname = entry, ""
        elif isinstance(entry, dict) and "path" in entry:
            rel, rname = str(entry["path"]), str(entry.get("name", ""))
        else:
            raise FleetManifestError(
                f"{path}: repos[{i}] must be a path string or {{path:, name:}}"
            )
        repo_dir = (base / rel).resolve()
        if not repo_dir.is_dir():
            raise FleetManifestError(f"{path}: repos[{i}] '{rel}' is not a directory")
        if repo_dir in seen:
            raise FleetManifestError(f"{path}: duplicate repo path '{rel}'")
        seen.add(repo_dir)
        repos.append(FleetRepo(name=rname or repo_dir.name, path=repo_dir))
    if not repos and workspace_root is None:
        raise FleetManifestError(f"{path}: manifest declares neither 'root' nor 'repos'")
    return FleetManifest(
        path=path,
        name=name,
        repos=tuple(sorted(repos, key=lambda r: r.path.as_posix())),
        workspace_root=workspace_root,
    )


def build_fleet_model(manifest: FleetManifest) -> WorkspaceModel:
    """Build the merged estate graph from a manifest's repo list, or via
    workspace discovery when the manifest is a directory/`root:`.

    Repo ``path`` fields in the returned model are relative to the
    manifest directory when inside it, else absolute (deterministic
    either way).
    """
    if manifest.workspace_root is not None:
        root = manifest.workspace_root
        return merge_repos(root, discover_repos(root, ProjectContext(root=root)))

    repos: list[WorkspaceRepo] = []
    base = manifest.path.parent if manifest.path.is_file() else manifest.path
    for fr in manifest.repos:
        try:
            rel = fr.path.relative_to(base).as_posix()
        except ValueError:
            rel = fr.path.as_posix()
        repos.append(WorkspaceRepo(name=fr.name, path=rel, markers=(), languages=()))
    return merge_repos(base.resolve(), tuple(repos))
