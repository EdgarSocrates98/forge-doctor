"""Forge handoff bundle: one portable JSON artifact for downstream tools.

A bundle packages a scan's durable outputs — findings, the platform
graph, capability headline statuses, and remediation plans — with stable
keys and deterministic ordering. It carries no timestamps: identical
project state produces byte-identical bundles.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from forge_doctor_data import __version__
from forge_doctor_data.api import SCHEMA_VERSION

if TYPE_CHECKING:
    from forge_doctor_data.core.context import ProjectContext
    from forge_doctor_data.core.models import ScanReport

CONTRACT_NAME = "handoff-bundle"
CONTRACT_VERSION = 1


def build_handoff_bundle(report: ScanReport, ctx: ProjectContext) -> dict[str, Any]:
    """Assemble the interop bundle from a completed scan."""
    from forge_doctor_data.analyzers.platform_graph_builder import build_platform_graph
    from forge_doctor_data.core.capabilities import capability_registry
    from forge_doctor_data.core.diagnosis import cluster_findings
    from forge_doctor_data.core.remediation import plan_remediation, plan_to_dict
    from forge_doctor_data.output.json_renderer import result_to_dict

    graph = build_platform_graph(ctx).to_dict()
    cap_reg = capability_registry()
    capabilities = {
        platform: {
            cap: cap_reg.explain(platform, cap).status.value
            for cap in sorted(cap_reg.capabilities_for(platform))
        }
        for platform in sorted(cap_reg.platforms())
    }
    clusters = cluster_findings(report.results, [])
    plans = [plan_to_dict(p) for p in plan_remediation(report.results, clusters)]
    return {
        "contract": CONTRACT_NAME,
        "contract_version": CONTRACT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": "forge-doctor-data", "version": __version__},
        "project": {"name": ctx.root.name, "root": str(ctx.root)},
        "summary": {
            "passed": report.summary.passed,
            "info": report.summary.info,
            "warnings": report.summary.warnings,
            "errors": report.summary.errors,
        },
        "results": [
            result_to_dict(r)
            for r in sorted(report.results, key=lambda x: (x.check_id, x.fingerprint or ""))
        ],
        "graph": graph,
        "capabilities": capabilities,
        "plans": sorted(plans, key=lambda p: str(p["id"])),
    }
