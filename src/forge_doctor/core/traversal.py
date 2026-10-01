"""Pruned filesystem traversal shared by all checks."""

from __future__ import annotations

import os
from collections.abc import Iterator
from fnmatch import fnmatch
from pathlib import Path

DEFAULT_EXCLUDED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "dist",
        "build",
        "__pycache__",
        ".pytest_cache",
        ".pytest_tmp",
        ".forge-doctor",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".idea",
        ".vscode",
        "site-packages",
    }
)


def iter_files(
    root: Path,
    excluded_dirs: frozenset[str] = DEFAULT_EXCLUDED_DIRS,
    exclude_globs: tuple[str, ...] = (),
) -> Iterator[Path]:
    """Yield files under ``root`` as paths relative to ``root``.

    Prunes heavy/irrelevant directories by name and applies user-provided
    exclude globs (matched against the POSIX relative path).
    """
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in excluded_dirs and not d.endswith(".egg-info")
        )
        for filename in sorted(filenames):
            path = Path(dirpath) / filename
            relative = path.relative_to(root)
            posix = relative.as_posix()
            if any(fnmatch(posix, pattern) for pattern in exclude_globs):
                continue
            yield relative
