"""Forge Doctor - deterministic diagnostics for data engineering projects."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version

try:
    # Single source of truth: the [project] version in pyproject.toml.
    __version__ = _dist_version("forge-doctor")
except PackageNotFoundError:  # src tree without install (editable checkouts)
    __version__ = "0.7.0"
