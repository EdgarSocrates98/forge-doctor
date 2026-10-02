"""Project context: memoized facts about the scanned project.

Built once per scan. Checks read from the context only - files are listed
once, ``pyproject.toml`` is parsed once, git facts are collected once.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

from forge_doctor.core.config import ForgeDoctorConfig
from forge_doctor.core.traversal import iter_files

_ENV_KEYS = (
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "AWS_PROFILE",
    "AWS_DEFAULT_PROFILE",
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
)


@dataclass(frozen=True)
class ScanOptions:
    """User-facing options resolved by the CLI."""

    categories: tuple[str, ...] = ()
    ignore: tuple[str, ...] = ()
    fail_on: str = "error"
    verbose: bool = False
    # Tri-state: None = unset (auto: on locally, off in CI); True/False
    # when the caller explicitly passed --cache/--no-cache.
    use_cache: bool | None = None
    # Unsaved-buffer overlay (LSP): project-relative posix path -> content.
    # Overlay files read from memory, never from disk, and are excluded
    # from the incremental cache (facts must not key on stale disk shas).
    overlay: Mapping[str, str] | None = None


@dataclass
class GitInfo:
    """Git facts; ``tracked_files`` is ``None`` when unavailable."""

    is_repo: bool
    tracked_files: frozenset[str] | None = None


@dataclass
class ProjectContext:
    """Everything checks may need; lazy, cached, never executes target code."""

    root: Path
    options: ScanOptions = field(default_factory=ScanOptions)

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", self.root.resolve())

    @cached_property
    def config(self) -> ForgeDoctorConfig:
        return ForgeDoctorConfig.from_pyproject(self.pyproject or {})

    @cached_property
    def files(self) -> frozenset[Path]:
        found = set(iter_files(self.root, exclude_globs=self.config.exclude))
        if self.options.overlay:
            found.update(Path(rel) for rel in self.options.overlay)
        return frozenset(found)

    @cached_property
    def dirs(self) -> frozenset[Path]:
        return frozenset(p for f in self.files for p in f.parents)

    @cached_property
    def pyproject_path(self) -> Path | None:
        candidate = self.root / "pyproject.toml"
        return candidate if candidate.is_file() else None

    @cached_property
    def pyproject(self) -> dict[str, Any] | None:
        if self.pyproject_path is None:
            return None
        try:
            with self.pyproject_path.open("rb") as handle:
                return tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            return None

    @cached_property
    def capabilities(self) -> Any:
        """Shared capability registry - the single path for platform facts."""
        from forge_doctor.core.capabilities import capability_registry

        return capability_registry()

    @cached_property
    def env(self) -> dict[str, str]:
        """Relevant environment variables only - values stay in-process."""
        return {key: os.environ[key] for key in _ENV_KEYS if key in os.environ}

    @cached_property
    def git(self) -> GitInfo:
        git = shutil.which("git")
        if git is None:
            return GitInfo(is_repo=False)
        inside = _run([git, "-C", str(self.root), "rev-parse", "--is-inside-work-tree"])
        if inside is None or inside.strip() != "true":
            return GitInfo(is_repo=False)
        # ls-files reports paths relative to the REPOSITORY root; when the
        # scanned root is a subdirectory, strip the prefix so results stay
        # project-relative and out-of-scope files are ignored.
        prefix = (_run([git, "-C", str(self.root), "rev-parse", "--show-prefix"]) or "").strip()
        listed = _run([git, "-C", str(self.root), "ls-files"])
        tracked: frozenset[str] | None = None
        if listed is not None:
            tracked = frozenset(
                line.strip()[len(prefix) :]
                for line in listed.splitlines()
                if line.strip() and line.strip().startswith(prefix)
            )
        return GitInfo(is_repo=True, tracked_files=tracked)

    @cached_property
    def python_version(self) -> tuple[int, int, int] | None:
        """Version of the ``python`` on PATH (the project-facing interpreter)."""
        for name in ("python", "python3"):
            binary = shutil.which(name)
            if binary is None:
                continue
            out = _run([binary, "--version"])
            if out:
                parts = out.removeprefix("Python ").strip().split(".")
                try:
                    return int(parts[0]), int(parts[1]), int(parts[2])
                except (ValueError, IndexError):
                    continue
        return sys.version_info[:3]  # pragma: no cover - PATH always has python in tests

    def read_text(self, relative: Path, limit: int = 1_000_000) -> str | None:
        """Read a project file as text; ``None`` on failure. Never follows outside root."""
        if self.options.overlay is not None:
            overlaid = self.options.overlay.get(relative.as_posix())
            if overlaid is not None:
                return overlaid[:limit]
        try:
            path = (self.root / relative).resolve()
            if self.root not in path.parents and path != self.root:
                return None
            if path.stat().st_size > limit:
                return None
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def has_file(self, name: str) -> bool:
        return (self.root / name).is_file()

    def has_dir(self, name: str) -> bool:
        return (self.root / name).is_dir()


def _run(command: list[str], timeout: float = 5.0) -> str | None:
    """Run a read-only system command; ``None`` on any failure."""
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout
