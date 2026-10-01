"""Incremental analysis cache - per-file sha256 keyed fact store.

The expensive part of a rescan is re-parsing every Python file. The semantic
index condenses each file into serializable facts (imports, calls, assigns,
function return shapes, analyzer buckets); unchanged files reuse them without
an ``ast.parse``.

Trust model: the cache is derived data, so it must never live inside the
analyzed project - a hostile repo could ship ``.forge-doctor/cache/`` entries
with correct SHAs but forged facts. Storage is the platform user cache keyed
by repo identity + tool version + analyzer schema version:

- Windows: ``%LOCALAPPDATA%/forge-doctor/cache/``
- macOS:   ``~/Library/Caches/forge-doctor/``
- Linux:   ``$XDG_CACHE_HOME/forge-doctor`` or ``~/.cache/forge-doctor/``
- override: ``FORGE_DOCTOR_CACHE_DIR``

Cross-file invalidation: bucket facts (spark/glue) embed propagated
cross-module names, so each entry also records ``dep_sigs`` - per imported
module, the resolved project file plus its *export signature* (a hash of the
dep's derived semantic surface: exported functions' producer state, return
shapes, and call sites). A dep whose signature moved invalidates the
dependent's cached facts - including transitive drift where a dep's file
bytes are unchanged but its semantics moved through its own deps.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext

CACHE_FORMAT = 2
# Bump when the serialized facts shape changes - cached entries from older
# schemas are ignored wholesale.
ANALYZER_SCHEMA_VERSION = 3

CACHE_ATTR = "_fd_scan_cache"
LEGACY_CACHE_DIR = ".forge-doctor"
LEGACY_CACHE_FILE = "scan-cache.json"


def cache_root() -> Path:
    """User-level cache directory for forge-doctor (never inside the target)."""
    override = os.environ.get("FORGE_DOCTOR_CACHE_DIR")
    if override:
        return Path(override)
    # platform.system() so mypy doesn't narrow dead branches per-OS.
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "forge-doctor" / "cache"
        return Path.home() / ".cache" / "forge-doctor"
    if system == "Darwin":
        return Path.home() / "Library" / "Caches" / "forge-doctor"
    base = os.environ.get("XDG_CACHE_HOME")
    return (Path(base) if base else Path.home() / ".cache") / "forge-doctor"


def in_ci() -> bool:
    """Heuristic CI detection - cache defaults to off in untrusted contexts."""
    return any(
        os.environ.get(name)
        for name in ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE", "TF_BUILD")
    )


class ScanCache:
    """JSON file cache: ``files[relpath] = {sha256, dep_sigs, facts}``."""

    def __init__(self, root: Path, enabled: bool = True) -> None:
        self.root = root
        self.enabled = enabled
        self.hits = 0
        self.misses = 0
        self._files: dict[str, dict[str, Any]] = {}
        if enabled:
            self._load()

    @property
    def path(self) -> Path:
        from forge_doctor import __version__

        repo_key = hashlib.sha256(self.root.as_posix().encode()).hexdigest()[:16]
        name = f"scan-{repo_key}-v{__version__}-s{ANALYZER_SCHEMA_VERSION}.json"
        return cache_root() / name

    @property
    def legacy_path(self) -> Path:
        """Old in-project location - never trusted, only cleaned up."""
        return self.root / LEGACY_CACHE_DIR / "cache" / LEGACY_CACHE_FILE

    def sha256(self, relative: Path) -> str | None:
        try:
            data = (self.root / relative).read_bytes()
        except OSError:
            return None
        return hashlib.sha256(data).hexdigest()

    def get(self, relative: Path, sha: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        entry = self._files.get(relative.as_posix())
        if entry is None or entry.get("sha") != sha:
            self.misses += 1
            return None
        self.hits += 1
        return entry.get("facts")

    def get_entry(self, relative: Path, sha: str) -> dict[str, Any] | None:
        """Full cache entry (facts + dep_sigs) for dep-aware invalidation."""
        if not self.enabled:
            return None
        entry = self._files.get(relative.as_posix())
        if entry is None or entry.get("sha") != sha:
            return None
        return entry

    def put(
        self,
        relative: Path,
        sha: str | None,
        facts: dict[str, Any],
        dep_sigs: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        if not self.enabled or sha is None:
            return
        self._files[relative.as_posix()] = {
            "sha": sha,
            "dep_sigs": dep_sigs or {},
            "facts": facts,
        }

    def update_facts(self, relative: Path, patch: dict[str, Any]) -> None:
        """Merge analyzer buckets into a stored entry after first analysis."""
        if not self.enabled:
            return
        entry = self._files.get(relative.as_posix())
        if entry is not None:
            entry["facts"].update(patch)

    def set_dep_sigs(self, relative: Path, dep_sigs: dict[str, dict[str, Any]]) -> None:
        if not self.enabled:
            return
        entry = self._files.get(relative.as_posix())
        if entry is not None:
            entry["dep_sigs"] = dep_sigs

    def prune(self, live: set[str]) -> None:
        """Drop cache entries for files that no longer exist."""
        self._files = {k: v for k, v in self._files.items() if k in live}

    def save(self) -> None:
        if not self.enabled:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format": CACHE_FORMAT,
            "repo": self.root.name,
            "files": self._files,
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(payload, dict) or payload.get("format") != CACHE_FORMAT:
            return
        files = payload.get("files")
        if isinstance(files, dict):
            self._files = files

    def clear(self) -> bool:
        """Delete the cache file (and any legacy in-project cache)."""
        self._files = {}
        removed = False
        for candidate in (self.path, self.legacy_path):
            try:
                candidate.unlink()
                removed = True
            except OSError:
                continue
        return removed


def scan_cache(ctx: ProjectContext) -> ScanCache:
    """Memoized per-scan cache on the context (respects --no-cache and CI)."""
    cached = getattr(ctx, CACHE_ATTR, None)
    if isinstance(cached, ScanCache):
        return cached
    use_cache = ctx.options.use_cache
    # None = user didn't pass --cache/--no-cache: off by default in CI where
    # the target repo is less trusted. Explicit --cache always wins.
    enabled = (not in_ci()) if use_cache is None else use_cache
    cache = ScanCache(ctx.root, enabled=enabled)
    setattr(ctx, CACHE_ATTR, cache)
    return cache
