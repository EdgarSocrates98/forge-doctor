"""EMR / Databricks / Delta deep checks - evidence-gated, capability-driven."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from forge_doctor.core.models import Confidence, EvidenceKind, Severity
from forge_doctor.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.models import CheckResult


def _emr(ctx: ProjectContext) -> Any:
    from forge_doctor.analyzers.emr_model import emr_model

    return emr_model(ctx)


def _dbx(ctx: ProjectContext) -> Any:
    from forge_doctor.analyzers.databricks_model import databricks_model

    return databricks_model(ctx)


def _delta(ctx: ProjectContext) -> Any:
    from forge_doctor.analyzers.delta_model import delta_model

    return delta_model(ctx)


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:2])


class _PlatformCheck(CheckBase):
    category = "platforms"
    evidence_kind = EvidenceKind.CONFIG


# --------------------------------------------------------------- EMR


class EmrUsage(_PlatformCheck):
    """EMR000: anchor - EC2 vs Serverless vs EKS footprint."""

    id = "EMR000"
    title = "EMR usage"
    why = "Anchor: sizes the EMR surface feeding the other EMR checks."
    when_ok = "Anchor check - always reports."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _emr(ctx)
        if not m.has_emr:
            return [self.result(Severity.PASS, "no EMR workloads detected")]
        return [
            self.result(
                Severity.INFO,
                f"{len(m.clusters)} EC2 cluster(s), {len(m.serverless_apps)} serverless app(s), "
                f"{len(m.eks_clusters)} EKS virtual cluster(s); releases: "
                f"{', '.join(m.releases) or 'unpinned'}",
            )
        ]


class EmrReleaseLegacy(_PlatformCheck):
    """EMR001: cluster release older than the supported floor (EMR 6.x)."""

    id = "EMR001"
    title = "EMR release below 6.x"
    why = (
        "EMR 5.x reaches end of standard support; its Spark/Hadoop stack is "
        "frozen and misses Delta/Iceberg/Lake Formation integrations that "
        "6.x+ gains."
    )
    when_ok = "All EMR releases are emr-6.x or newer."
    fix = "Upgrade to a supported emr-6.x/emr-7.x release label."
    confidence = Confidence.MEDIUM

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _emr(ctx)
        results = []
        for c in m.clusters + m.serverless_apps + m.eks_clusters:
            ver = _version_tuple(c.release)
            if ver and ver < (6,):
                results.append(
                    self.result(
                        Severity.WARNING,
                        f"{c.name} pinned to {c.release} (below emr-6.x)",
                        file=c.file,
                        line=c.line,
                    )
                )
        return results


class EmrNoScaling(_PlatformCheck):
    """EMR002: EC2 cluster without autoscaling and without dynamic allocation."""

    id = "EMR002"
    title = "Cluster without autoscaling or dynamic allocation"
    why = (
        "A fixed-size EMR EC2 cluster pays idle capacity between steps; "
        "managed scaling or spark.dynamicAllocation right-sizes executors."
    )
    when_ok = "Clusters enable managed scaling, autoscaling, or dynamic allocation."
    fix = "Add aws_emr_managed_scaling_policy or enable spark.dynamicAllocation.enabled."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _emr(ctx)
        return [
            self.result(
                Severity.INFO,
                f"{c.name}: no scaling policy, no autoscaling, no dynamic allocation "
                f"({c.core_count or '?'} core nodes)",
                file=c.file,
                line=c.line,
            )
            for c in m.clusters
            if not (c.autoscaling or c.dynamic_allocation or m.scaling_policies)
        ]


class EmrSpotCore(_PlatformCheck):
    """EMR003: all-fleet Spot - core nodes on Spot risk step termination."""

    id = "EMR003"
    title = "All-Spot instance fleets"
    why = (
        "Spot-only core/task fleets can interrupt mid-step and lose shuffle "
        "data; core nodes should be On-Demand (Spot for task fleets only)."
    )
    when_ok = "At least one On-Demand fleet/group exists, or no fleets at all."
    fix = "Keep master/core On-Demand; restrict Spot to task fleets."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.WARNING,
                f"{c.name}: {c.spot_fleets} spot fleet(s), 0 on-demand - step interruption risk",
                file=c.file,
                line=c.line,
            )
            for c in _emr(ctx).clusters
            if c.has_fleets and c.spot_fleets > 0 and c.on_demand_fleets == 0
        ]


class EmrNoLogging(_PlatformCheck):
    """EMR004: cluster without log_uri - no persisted logs."""

    id = "EMR004"
    title = "Cluster without log_uri"
    why = "Without log_uri, EMR logs vanish with the cluster - no postmortem."
    when_ok = "Every cluster has log_uri (or LogUri) set."
    fix = "Set log_uri to an s3:// log bucket."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.INFO,
                f"{c.name} has no log_uri",
                file=c.file,
                line=c.line,
            )
            for c in _emr(ctx).clusters
            if not c.log_uri
        ]


class EmrNoSecurityConfig(_PlatformCheck):
    """EMR005: no security configuration - encryption/IAM defaults off."""

    id = "EMR005"
    title = "Cluster without security configuration"
    why = (
        "A security configuration enables at-rest/in-transit encryption and "
        "Lake Formation integration; absent it, EMR runs with defaults."
    )
    when_ok = "Clusters reference an aws_emr_security_configuration."
    fix = "Attach a security_configuration (encryption + LF integration as needed)."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.INFO,
                f"{c.name} has no security_configuration",
                file=c.file,
                line=c.line,
            )
            for c in _emr(ctx).clusters
            if not c.security_configuration
        ]


class EmrStepNoFailAction(_PlatformCheck):
    """EMR006: step without action_on_failure."""

    id = "EMR006"
    title = "Step without action_on_failure"
    why = (
        "A failed step without action_on_failure defaults to continuing the "
        "cluster - silently skipping work is worse than stopping."
    )
    when_ok = "Steps set action_on_failure (CONTINUE/TERMINATE_CLUSTER/...)."
    fix = "Set action_on_failure per step (usually TERMINATE_CLUSTER or CANCEL_AND_WAIT)."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _emr(ctx)
        if not m.steps_no_fail_action:
            return []
        return [
            self.result(
                Severity.INFO,
                f"{m.steps_no_fail_action}/{m.steps_total} step(s) lack action_on_failure",
            )
        ]


class EmrServerlessNoCap(_PlatformCheck):
    """EMR007: serverless app without maximum capacity - unbounded spend."""

    id = "EMR007"
    title = "Serverless application without capacity cap"
    why = "EMR Serverless without maximum_capacity can scale to unbounded spend."
    when_ok = "Every serverless app sets maximum_capacity/maximum_cpu."
    fix = "Set maximum_capacity (or auto_stop_configuration at minimum)."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.INFO,
                f"{a.name} (release {a.release or 'unpinned'}) has no maximum capacity",
                file=a.file,
                line=a.line,
            )
            for a in _emr(ctx).serverless_apps
            if not a.max_cpu
        ]


# ---------------------------------------------------------- Databricks


class DatabricksUsage(_PlatformCheck):
    """DBX000: anchor - jobs/clusters/warehouses/UC footprint."""

    id = "DBX000"
    title = "Databricks usage"
    why = "Anchor: sizes the Databricks surface feeding the other DBX checks."
    when_ok = "Anchor check - always reports."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _dbx(ctx)
        if not m.has_databricks:
            return [self.result(Severity.PASS, "no Databricks workloads detected")]
        return [
            self.result(
                Severity.INFO,
                f"{len(m.jobs)} job(s), {len(m.clusters)} cluster(s), "
                f"{len(m.warehouses)} warehouse(s), {len(m.uc_objects)} UC object(s), "
                f"{len(m.pipelines)} pipeline(s), {len(m.bundles)} bundle(s)",
            )
        ]


class DbxJobOnExistingCluster(_PlatformCheck):
    """DBX001: job task pinned to an existing (all-purpose) cluster."""

    id = "DBX001"
    title = "Job task on existing cluster"
    why = (
        "Jobs should use job_clusters (ephemeral, per-run) - an "
        "existing_cluster_id pins the job to a shared interactive cluster, "
        "mixing workloads and paying for idle time."
    )
    when_ok = "Jobs use job_cluster/new_cluster, not existing_cluster_id."
    fix = "Move the task to a job_cluster/new_cluster block."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.WARNING,
                f"job '{j.name}' uses existing_cluster_id ({j.task_count} task(s))",
                file=j.file,
                line=j.line,
            )
            for j in _dbx(ctx).jobs
            if j.uses_existing_cluster
        ]


class DbxNoAutoscale(_PlatformCheck):
    """DBX002: cluster/new_cluster with fixed num_workers and no autoscale."""

    id = "DBX002"
    title = "Cluster without autoscaling"
    why = (
        "Fixed num_workers either starves the job or idles between steps - "
        "Databricks autoscale is the standard sizing primitive."
    )
    when_ok = "Clusters set autoscale { min_workers, max_workers }."
    fix = "Replace num_workers with autoscale bounds."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.INFO,
                f"cluster '{c.name}' ({c.dbr_version or 'unpinned DBR'}) "
                f"num_workers={c.num_workers}, no autoscale",
                file=c.file,
                line=c.line,
            )
            for c in _dbx(ctx).clusters
            if not c.autoscale and c.num_workers > 0
        ]


class DbxOldRuntime(_PlatformCheck):
    """DBX003: cluster on a pre-LTS DBR (< 13.3)."""

    id = "DBX003"
    title = "Databricks runtime below 13.3 LTS"
    why = "DBR < 13.3 is off the LTS line - no Photon/Delta-3 fixes."
    when_ok = "Clusters run DBR 13.3+ (or serverless)."
    fix = "Upgrade spark_version to a 13.3+ LTS or current DBR."
    confidence = Confidence.MEDIUM

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        results = []
        for c in _dbx(ctx).clusters:
            ver = _version_tuple(c.dbr_version)
            if ver and ver < (13, 3):
                results.append(
                    self.result(
                        Severity.WARNING,
                        f"cluster '{c.name}' on DBR {c.dbr_version} (< 13.3 LTS)",
                        file=c.file,
                        line=c.line,
                    )
                )
        return results


class DbxNoUnityCatalog(_PlatformCheck):
    """DBX004: Databricks objects but no Unity Catalog declared."""

    id = "DBX004"
    title = "No Unity Catalog objects declared"
    why = (
        "Without catalog/schema/external-location/storage-credential "
        "objects, tables resolve under hive_metastore - the legacy default."
    )
    when_ok = "At least one UC object exists when Databricks IaC does."
    fix = "Declare databricks_catalog/storage_credential/external_location for governed data."
    confidence = Confidence.MEDIUM

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _dbx(ctx)
        if not m.has_databricks or m.uc_objects:
            return []
        if not (m.jobs or m.clusters or m.warehouses):
            return []
        return [
            self.result(
                Severity.INFO,
                "Databricks IaC present but no Unity Catalog resources "
                "(hive_metastore fallback likely)",
            )
        ]


class DbxExternalLocationNoCred(_PlatformCheck):
    """DBX005: external_location without a storage_credential in project."""

    id = "DBX005"
    title = "External location without storage credential"
    why = (
        "An external location needs a storage credential - an undeclared one "
        "leaves the path unable to authenticate."
    )
    when_ok = "Every external_location coexists with a storage_credential."
    fix = "Add a databricks_storage_credential and reference it."
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _dbx(ctx)
        kinds = {o.kind for o in m.uc_objects}
        if "storage_credential" in kinds or "external_location" not in kinds:
            return []
        return [
            self.result(
                Severity.WARNING,
                f"{sum(1 for o in m.uc_objects if o.kind == 'external_location')} "
                "external_location(s) but no databricks_storage_credential",
            )
        ]


class DbxBundleMissing(_PlatformCheck):
    """DBX006: databricks IaC but no asset bundle / deploy config."""

    id = "DBX006"
    title = "No asset bundle"
    why = (
        "Databricks asset bundles (databricks.yml) are the deployment "
        "primitive - resources without a bundle are applied ad-hoc."
    )
    when_ok = "A databricks.yml exists when Databricks IaC does."
    fix = "Add a databricks.yml bundle (or document the deploy path)."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _dbx(ctx)
        if m.bundles or not (m.jobs or m.pipelines):
            return []
        return [
            self.result(
                Severity.INFO,
                f"{len(m.jobs)} job(s)/{len(m.pipelines)} pipeline(s) but no databricks.yml bundle",
            )
        ]


# --------------------------------------------------------------- Delta


class DeltaUsage(_PlatformCheck):
    """DELTA000: anchor - tables/ops/features/protocol surface."""

    id = "DELTA000"
    title = "Delta Lake usage"
    why = "Anchor: sizes the Delta surface feeding the other DELTA checks."
    when_ok = "Anchor check - always reports."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _delta(ctx)
        if not m.has_delta:
            return [self.result(Severity.PASS, "no Delta Lake usage detected")]
        counts = m.op_counts()
        detail = ", ".join(f"{k}={n}" for k, n in sorted(counts.items())) or "-"
        feats = ", ".join(sorted(m.features)) or "none"
        return [
            self.result(
                Severity.INFO,
                f"{len(m.tables)} table(s), {len(m.ops)} op(s) [{detail}]; "
                f"features: {feats}; protocol r{model_w(m)}",
            )
        ]


class DeltaNoOptimize(_PlatformCheck):
    """DELTA001: MERGE/DELETE churn without OPTIMIZE - small-file accumulation."""

    id = "DELTA001"
    title = "Merge/delete churn without OPTIMIZE"
    why = (
        "MERGE/UPDATE/DELETE rewrite files per commit - without periodic "
        "OPTIMIZE, Delta tables accumulate small files and readers degrade."
    )
    when_ok = "OPTIMIZE appears wherever merge/delete/update ops do."
    fix = "Add OPTIMIZE (optionally ZORDER BY) after merge-heavy writes."
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _delta(ctx)
        churn = sum(1 for o in m.ops if o.op in ("merge", "update", "delete"))
        optimizes = sum(1 for o in m.ops if o.op == "optimize")
        if churn and not optimizes:
            return [
                self.result(
                    Severity.WARNING,
                    f"{churn} merge/update/delete op(s) but no OPTIMIZE anywhere",
                )
            ]
        return []


class DeltaDeletionVectors(_PlatformCheck):
    """DELTA002: deletion vectors - capability-check the runtime floor."""

    id = "DELTA002"
    title = "Deletion vectors / feature compatibility"
    why = (
        "delta.deletionVectors requires reader/writer protocol 3/7 and "
        "runtime support (DBR 14.1+ / Delta 3.x) - older readers fail on the "
        "table entirely."
    )
    when_ok = "Deletion vectors only where readers are provably >= protocol 3."
    fix = "Verify reader runtimes before enabling deletionVectors."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _delta(ctx)
        if "deletion_vectors" not in m.features:
            return []
        return [
            self.result(
                Severity.WARNING,
                "deletion vectors enabled - requires protocol r3/w7 "
                "(DBR 14.1+ / Delta 3.x); older readers will fail on this table",
            )
        ]


class DeltaSchemaEvolutionRisk(_PlatformCheck):
    """DELTA003: auto-merge schema evolution flags - silent column drift."""

    id = "DELTA003"
    title = "Schema evolution flags"
    why = (
        "mergeSchema/autoMerge/allowSourceEvolution let incoming writes "
        "silently widen the table schema - downstream readers may see "
        "unexpected columns or nulls."
    )
    when_ok = "Schema changes are explicit, not auto-merged per write."
    fix = "Prefer explicit ALTER TABLE / evolution gates over mergeSchema."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _delta(ctx)
        if "schema_evolution" not in m.features:
            return []
        return [
            self.result(
                Severity.INFO,
                "schema-evolution flags detected (mergeSchema/autoMerge/"
                "allowSourceEvolution) - incoming writes widen the table silently",
            )
        ]


class DeltaCdfNoConsumer(_PlatformCheck):
    """DELTA004: change data feed enabled but never consumed."""

    id = "DELTA004"
    title = "CDF enabled without consumers"
    why = (
        "enableChangeDataFeed stores change data alongside the table - cost "
        "with no reader is waste."
    )
    when_ok = "CDF-enabled tables have a table_changes() / readChangeFeed consumer."
    fix = "Add a downstream CDF consumer or drop enableChangeDataFeed."
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _delta(ctx)
        if "cdf" in m.features and not m.cdf_sources:
            return [
                self.result(
                    Severity.INFO,
                    "change data feed enabled but no table_changes()/readChangeFeed "
                    "consumer found in project",
                )
            ]
        return []


def model_w(m: Any) -> str:
    """compact protocol display for DELTA000."""
    return (
        f"/w{m.protocol_writer}"
        if m.protocol_writer
        else (f"r{m.protocol_reader}/w?" if m.protocol_reader else "?")
    )


CHECKS: list[Check] = [
    EmrUsage(),
    EmrReleaseLegacy(),
    EmrNoScaling(),
    EmrSpotCore(),
    EmrNoLogging(),
    EmrNoSecurityConfig(),
    EmrStepNoFailAction(),
    EmrServerlessNoCap(),
    DatabricksUsage(),
    DbxJobOnExistingCluster(),
    DbxNoAutoscale(),
    DbxOldRuntime(),
    DbxNoUnityCatalog(),
    DbxExternalLocationNoCred(),
    DbxBundleMissing(),
    DeltaUsage(),
    DeltaNoOptimize(),
    DeltaDeletionVectors(),
    DeltaSchemaEvolutionRisk(),
    DeltaCdfNoConsumer(),
]
