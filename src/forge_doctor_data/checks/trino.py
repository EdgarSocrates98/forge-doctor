"""Trino checks (TRINO###) over the TrinoProjectModel (spec 218).

Evidence is config-plane (``.properties`` files) plus authored SQL for
the three-part-name cross-check. TRINO005 carries MEDIUM confidence —
a three-part name in a mixed Snowflake/Trino repo may legitimately name
a non-Trino catalog, so the finding describes the gap, not a verdict.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from forge_doctor_data.analyzers.trino_model import (
    TEST_CONNECTORS,
    ThreePartRef,
    trino_model,
)
from forge_doctor_data.core.models import Confidence, EvidenceKind, Severity
from forge_doctor_data.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor_data.core.context import ProjectContext
    from forge_doctor_data.core.models import CheckResult


class _TrinoCheck(CheckBase):
    category = "trino"


class TrinoSurface(_TrinoCheck):
    """TRINO000: anchor census of the Trino surface."""

    id = "TRINO000"
    title = "Trino surface"
    why = "Anchor: sizes the Trino estate feeding the TRINO checks."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the counts to size the surface."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = trino_model(ctx)
        if not model.has_evidence:
            return [self.result(Severity.PASS, "no trino evidence detected")]
        connectors = sorted({c.connector for c in model.catalogs})
        role = "coordinator" if model.is_coordinator else ("worker" if model.worker_props else "-")
        return [
            self.result(
                Severity.INFO,
                f"{len(model.catalogs)} catalogs ({', '.join(connectors)}), "
                f"node role: {role}, {len(model.refs)} three-part refs, "
                f"{len(model.observed)} observed rows",
            )
        ]


class HiveWithoutMetastore(_TrinoCheck):
    """TRINO001: hive connector catalog without metastore config."""

    id = "TRINO001"
    title = "Hive catalog without metastore"
    why = (
        "A hive connector with no metastore keys cannot resolve table "
        "metadata — the catalog is dead on arrival."
    )
    when_ok = "Every hive catalog sets a metastore (thrift/file/glue/iceberg)."
    fix = "Add hive.metastore.uri or hive.metastore=glue to the catalog."

    _METASTORE_HINT = re.compile(r"^hive\.metastore|iceberg\.catalog|delta\.metastore")

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        out: list[CheckResult] = []
        for c in trino_model(ctx).catalogs:
            if c.connector not in {"hive", "hive-hadoop2"}:
                continue
            keys = {k for k, _ in c.props}
            if not any(self._METASTORE_HINT.search(k) for k in keys):
                out.append(
                    self.result(
                        Severity.WARNING,
                        f"catalog '{c.name}' uses connector 'hive' but declares "
                        "no hive.metastore.* config",
                        file=c.file,
                        evidence="connector.name=hive with no metastore keys",
                        evidence_kind=EvidenceKind.CONFIG,
                    )
                )
        return out


class CoordinatorNoSpill(_TrinoCheck):
    """TRINO002: coordinator without spill-to-disk while ETL queries exist."""

    id = "TRINO002"
    title = "Coordinator without spill-to-disk"
    why = (
        "ETL-style queries (INSERT/CTAS/MERGE) can exceed memory on a "
        "coordinator that lacks spill config — the query dies instead "
        "of degrading to disk."
    )
    when_ok = "A coordinator that runs heavy writes configures spill-to-disk."
    fix = "Set spill-enabled + spiller-spill-path (and query.max-spill-per-node)."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = trino_model(ctx)
        if not model.is_coordinator or model.has_spill_config():
            return []
        # "running ETL-style queries" = authored SQL that writes
        from forge_doctor_data.analyzers.sql_ast import analyze_sql

        if not any(s.tables_written for s in analyze_sql(ctx).statements):
            return []
        return [
            self.result(
                Severity.WARNING,
                "coordinator=true with no spill-to-disk config, and authored "
                "SQL writes exist — ETL queries can OOM instead of spilling",
                file=model.config_file,
                evidence="config.properties: coordinator=true, no spill keys",
                evidence_kind=EvidenceKind.CONFIG,
            )
        ]


class TestConnectorInProd(_TrinoCheck):
    """TRINO003: tpch/jmx/system-style test connector in a deployment."""

    id = "TRINO003"
    title = "Test connector catalog in deployment"
    why = (
        "tpch/jmx/system/blackhole connectors exist for benchmarking and "
        "debug — checked into a deployment that also has real catalogs, "
        "they're either dead weight or a benchmark leaking into prod."
    )
    when_ok = "Test connectors appear only in test fixtures, not beside data catalogs."
    fix = "Remove the test-connector catalog or gate it to dev clusters."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = trino_model(ctx)
        data_catalogs = [c for c in model.catalogs if c.connector not in TEST_CONNECTORS]
        if not data_catalogs:
            return []
        return [
            self.result(
                Severity.WARNING,
                f"catalog '{c.name}' uses test-only connector '{c.connector}' "
                f"alongside {len(data_catalogs)} data catalog(s)",
                file=c.file,
                evidence=f"connector.name={c.connector}",
                evidence_kind=EvidenceKind.CONFIG,
            )
            for c in model.catalogs
            if c.connector in TEST_CONNECTORS
        ]


class NoResourceGroups(_TrinoCheck):
    """TRINO004: multi-catalog deployment without resource groups."""

    id = "TRINO004"
    title = "Multi-catalog deployment without resource groups"
    why = (
        "With several catalogs on one coordinator and no resource groups, "
        "a single workload can starve the others — no admission control."
    )
    when_ok = "Multi-catalog deployments declare resource groups."
    fix = "Add resource-groups.json + resource-groups.config-file."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = trino_model(ctx)
        if len(model.catalogs) < 2 or model.has_resource_groups():
            return []
        return [
            self.result(
                Severity.WARNING,
                f"{len(model.catalogs)} catalogs but no resource-groups "
                "config — no admission control between workloads",
                file=model.config_file or model.catalogs[0].file,
                evidence="no resource-groups.config-file / resource-groups.json",
                evidence_kind=EvidenceKind.CONFIG,
            )
        ]


class UnknownCatalogRef(_TrinoCheck):
    """TRINO005: three-part SQL name referencing an unknown catalog."""

    id = "TRINO005"
    title = "Three-part SQL ref to unknown catalog"
    why = (
        "catalog.schema.table in authored SQL must resolve to a declared "
        "catalog — a typo or missing catalog file fails at run time."
    )
    when_ok = "Every three-part name's catalog prefix is a declared catalog."
    fix = "Create the catalog properties file or fix the reference."
    confidence = Confidence.MEDIUM  # name-prefix heuristic; other vendors share syntax

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = trino_model(ctx)
        known = model.catalog_names()
        if not known:
            return []
        missing: dict[str, ThreePartRef] = {}
        for r in model.refs:
            if r.catalog.lower() not in {k.lower() for k in known}:
                missing.setdefault(r.catalog, r)
        return [
            self.result(
                Severity.WARNING,
                f"SQL references catalog '{r.catalog}' (e.g. {r.name}) which "
                "has no catalog properties file",
                file=r.file,
                line=r.line,
                evidence=f"{r.name} — no {r.catalog}.properties catalog",
            )
            for r in sorted(missing.values(), key=lambda r: (r.catalog, r.name))
        ]


CHECKS: tuple[Check, ...] = (
    TrinoSurface(),
    HiveWithoutMetastore(),
    CoordinatorNoSpill(),
    TestConnectorInProd(),
    NoResourceGroups(),
    UnknownCatalogRef(),
)
