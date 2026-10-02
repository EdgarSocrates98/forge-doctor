"""Public programmatic API - the stable SDK surface.

Everything in ``__all__`` is a public contract: signatures and return
types follow semver against the package version, and JSON output
follows ``SCHEMA_VERSION``. Everything else in the package is internal
and may change without notice.

Quick start::

    import forge_doctor.api as fd

    report = fd.scan("./my-project")
    for r in report.results:
        print(r.check_id, r.severity.value, r.message)

    graph = fd.platform_graph("./my-project")
    print([e.id for e in graph.entities()])
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from forge_doctor import __version__
from forge_doctor.core.context import ScanOptions
from forge_doctor.core.models import ScanReport
from forge_doctor.core.platform_graph import DataPlatformGraph
from forge_doctor.output.json_renderer import JSON_SCHEMA_VERSION as SCAN_SCHEMA_VERSION

__all__ = [
    "SCAN_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "DataPlatformGraph",
    "ScanOptions",
    "ScanReport",
    "capabilities_evaluate",
    "migrate_plans",
    "platform_graph",
    "scan",
    "version",
    "what_if",
]

# Machine-readable output contract for the artifact family introduced
# with the platform graph (lineage/graph/workspace/policy/misc meta
# payloads). Bump MINOR for additive fields, MAJOR for removed/renamed
# fields or changed semantics.
SCHEMA_VERSION = "1.0"

# The ``scan --format json`` report is a separate, older contract
# versioned independently - re-exported above as ``SCAN_SCHEMA_VERSION``


def version() -> str:
    """Installed package version (``pyproject.toml`` is source of truth)."""
    return __version__


def scan(
    path: str | Path = ".",
    *,
    profile: str = "default",
    ignore: tuple[str, ...] = (),
) -> ScanReport:
    """Run the full deterministic scan; returns the ``ScanReport``.

    Offline and side-effect-free: no network, no target-code execution.
    ``profile`` selects the severity profile (``default`` unless the
    project config overrides it via ``policy.extends``).
    """
    from forge_doctor.core.service import ScanRequest, ScanService

    outcome = ScanService().run(
        ScanRequest(
            path=Path(path).resolve(),
            ignore=ignore,
            profile=None if profile == "default" else profile,
        )
    )
    return outcome.report


def platform_graph(path: str | Path = ".") -> DataPlatformGraph:
    """Build the canonical ``DataPlatformGraph`` for a project."""
    from forge_doctor.analyzers.platform_graph_builder import build_platform_graph
    from forge_doctor.core.context import ProjectContext

    return build_platform_graph(ProjectContext(root=Path(path).resolve()))


def capabilities_evaluate(
    platform: str,
    capability: str,
    *,
    version: str | None = None,
    attributes: dict[str, str] | None = None,
) -> str:
    """Evaluate one capability fact against the knowledge packs.

    Returns ``supported`` | ``unsupported`` | ``conditional`` |
    ``unknown``. Read-only; packs are shared, process-cached data.
    """
    from forge_doctor.core.capabilities import CapabilityContext, capability_registry

    result = capability_registry().evaluate(
        capability,
        CapabilityContext(
            platform=platform,
            version=version,
            attributes=tuple(sorted((attributes or {}).items())),
        ),
    )
    return result.status.value


def what_if(path: str | Path, changes: dict[str, str]) -> Any:
    """Evaluate ``WhatIfChange``s against a project. Keys like
    ``glue-version``/``databricks-runtime``; see ``core/whatif.py``."""
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.whatif import evaluate_change, parse_change

    ctx = ProjectContext(root=Path(path).resolve())
    return [evaluate_change(ctx, parse_change(f"{k}={v}")) for k, v in changes.items()]


def migrate_plans(path: str | Path = ".") -> Any:
    """All applicable advisory migration plans (deterministic order).
    Plan-only; never mutates the project."""
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.migration import plan_migrations

    return plan_migrations(ProjectContext(root=Path(path).resolve()))
