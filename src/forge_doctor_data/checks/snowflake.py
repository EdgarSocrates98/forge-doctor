"""Snowflake checks (SNOW###) over the vendor SnowflakeProjectModel.

Grounded in ``snowflake_model`` only — vendor-neutral findings belong to
WARE###. Evidence is declarative (DDL, Terraform) plus observed-metadata
exports; no live warehouse calls ever.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from forge_doctor_data.analyzers.snowflake_model import (
    SnowflakeProjectModel,
    snowflake_model,
)
from forge_doctor_data.core.models import Severity
from forge_doctor_data.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor_data.core.context import ProjectContext
    from forge_doctor_data.core.models import CheckResult


def _model(ctx: ProjectContext) -> SnowflakeProjectModel:
    return snowflake_model(ctx)


class _SnowflakeCheck(CheckBase):
    category = "snowflake"


def _falsy(value: str) -> bool:
    return value.lower() in {"", "0", "false", "off", "none", "never"}


class SnowflakeUsage(_SnowflakeCheck):
    """SNOW000: anchor census of the Snowflake surface."""

    id = "SNOW000"
    title = "Snowflake surface"
    why = "Anchor: sizes the Snowflake estate feeding the SNOW checks."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the counts to size the surface."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.has_evidence:
            return [self.result(Severity.PASS, "no Snowflake evidence detected")]
        return [
            self.result(
                Severity.INFO,
                f"{len(model.warehouses)} warehouses, "
                f"{len(model.namespaces)} namespaces, {len(model.tables)} tables, "
                f"{len(model.views)} views, {len(model.objects)} vendor objects, "
                f"{len(model.copies)} COPY INTO, {len(model.observed)} observed rows",
            )
        ]


class MissingAutoSuspend(_SnowflakeCheck):
    """SNOW001: warehouse without ``auto_suspend`` (credits burn idle)."""

    id = "SNOW001"
    title = "Warehouse without auto_suspend"
    why = (
        "A warehouse with no auto_suspend (or 0/never) bills credits while "
        "idle — the single most common Snowflake cost leak."
    )
    when_ok = "Every warehouse sets auto_suspend to a positive seconds value."
    fix = "Set AUTO_SUSPEND = <seconds> (e.g. 300) on the warehouse."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        results = []
        for w in _model(ctx).warehouses:
            if _falsy(w.attr("auto_suspend")):
                results.append(
                    self.result(
                        Severity.WARNING,
                        f"warehouse '{w.name}' has no positive auto_suspend (source: {w.source})",
                        file=w.file,
                        line=w.line,
                        evidence=self.evidence_at(ctx, w.file, w.line)
                        if w.file is not None
                        else None,
                    )
                )
        return results


class AutoResumeAsymmetry(_SnowflakeCheck):
    """SNOW002: auto_suspend set but auto_resume disabled/absent."""

    id = "SNOW002"
    title = "auto_suspend without auto_resume"
    why = (
        "Suspending without resume makes the warehouse cold-start manual — "
        "asymmetric lifecycle config is usually a copy-paste miss."
    )
    when_ok = "auto_resume accompanies every auto_suspend."
    fix = "Set AUTO_RESUME = TRUE alongside auto_suspend."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        results = []
        for w in _model(ctx).warehouses:
            if not _falsy(w.attr("auto_suspend")) and _falsy(w.attr("auto_resume")):
                results.append(
                    self.result(
                        Severity.INFO,
                        f"warehouse '{w.name}' suspends but never auto-resumes",
                        file=w.file,
                        line=w.line,
                        evidence=self.evidence_at(ctx, w.file, w.line)
                        if w.file is not None
                        else None,
                    )
                )
        return results


class UnclusteredLargeTable(_SnowflakeCheck):
    """SNOW003: observed large table with no clustering keys."""

    id = "SNOW003"
    title = "Large table without clustering"
    why = (
        "Large fact tables without clustering keys force full scans; "
        "pruning depends on declared clustering for big data."
    )
    when_ok = "Large observed tables declare CLUSTER BY keys."
    fix = "Add CLUSTER BY (...) on the dominant filter/join columns."

    _LARGE_BYTES = 1_000_000_000  # 1 GB proxy — static, documented

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        declared = {t.name.rpartition(".")[2].lower() for t in model.tables}
        declared |= {t.name.lower() for t in model.tables}
        results = []
        for row in model.observed_tables():
            raw_bytes = row.get("bytes", "row_count", "rowcount")
            try:
                size = float(raw_bytes) if raw_bytes else 0.0
            except ValueError:
                size = 0.0
            clustered = bool(row.get("clustering_key", "cluster_by", "clustered"))
            name = row.get("table_name", "name")
            if size >= self._LARGE_BYTES and not clustered and name.lower() in declared:
                results.append(
                    self.result(
                        Severity.WARNING,
                        f"table '{name}' is {size:,.0f} units on observed "
                        "metadata with no clustering keys",
                        file=row.file,
                        evidence=f"observed metadata row in {row.file.as_posix()}",
                    )
                )
        return results


_PUBLIC_STAGE = re.compile(r"@~|@%|public", re.IGNORECASE)


class InsecureCopyStage(_SnowflakeCheck):
    """SNOW004: ``COPY INTO`` from a public or credential-less stage."""

    id = "SNOW004"
    title = "COPY INTO insecure stage"
    why = (
        "COPY from a public/user stage or a stage with no storage integration "
        "or credentials moves data over an unauthenticated channel."
    )
    when_ok = "COPY sources are named stages backed by a storage integration."
    fix = "Point COPY INTO at an internal stage with STORAGE_INTEGRATION set."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        # Stages the project declares, keyed by lowercase name.
        declared = {o.name.lower(): o for o in model.by_kind("stage")}
        results = []
        for copy in model.copies:
            ref = copy.stage_ref.lstrip("@%").split(".")[0].split("/")[0].lower()
            stage = declared.get(ref)
            insecure = (
                # literal URI or user/table stage - no storage wiring at all
                not copy.stage_ref
                or bool(_PUBLIC_STAGE.search(copy.stage_ref))
                # declared stage without integration/credentials
                or (
                    stage is not None
                    and not stage.attr("storage_integration")
                    and not stage.attr("credentials")
                )
            )
            if insecure:
                results.append(
                    self.result(
                        Severity.WARNING,
                        f"COPY INTO {copy.target or '?'} reads "
                        f"{copy.stage_ref or 'a literal URI'} - stage not "
                        "declared with a storage integration",
                        file=copy.file,
                        line=copy.line,
                        evidence=self.evidence_at(ctx, copy.file, copy.line)
                        if copy.file is not None
                        else None,
                    )
                )
        return results


class WildcardInPersistedDdl(_SnowflakeCheck):
    """SNOW005: ``SELECT *`` inside persisted view/procedure DDL."""

    id = "SNOW005"
    title = "SELECT * in persisted DDL"
    why = (
        "Views/materialized views on SELECT * break silently when the base "
        "table gains or reorders columns — schema drift you cannot see."
    )
    when_ok = "Persisted DDL names its column list explicitly."
    fix = "Replace SELECT * with an explicit column list in the DDL."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        from forge_doctor_data.analyzers.sql_ast import SQLGLOT_AVAILABLE, analyze_sql

        if not SQLGLOT_AVAILABLE:
            return []
        model = _model(ctx)
        results = []
        for stmt in analyze_sql(ctx).statements:
            if stmt.file not in model.sql_files or not stmt.wildcard:
                continue
            head = " ".join(stmt.text.split()[:6]).lower()
            if stmt.kind == "create" and ("view" in head or "table" in head or "procedure" in head):
                results.append(
                    self.result(
                        Severity.WARNING,
                        "persisted DDL selects '*' - column list is implicit",
                        file=stmt.file,
                        line=stmt.line,
                        evidence=self.evidence_at(ctx, stmt.file, stmt.line),
                    )
                )
        return results


CHECKS: tuple[Check, ...] = (
    SnowflakeUsage(),
    MissingAutoSuspend(),
    AutoResumeAsymmetry(),
    UnclusteredLargeTable(),
    InsecureCopyStage(),
    WildcardInPersistedDdl(),
)
