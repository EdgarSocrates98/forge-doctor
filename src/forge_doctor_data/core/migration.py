"""Deterministic migration planning.

Enumerates named migration paths applicable to the project and emits a
``MigrationPlan`` per detected opportunity — purely advisory, never
executed. Facts come from knowledge packs; where a pack has no facts,
the field reports UNKNOWN rather than guessing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from forge_doctor_data.core.capabilities import capability_registry
from forge_doctor_data.core.knowledge import load_pack

if TYPE_CHECKING:
    from forge_doctor_data.core.context import ProjectContext


@dataclass(frozen=True)
class MigrationPlan:
    """One advisory migration plan — generated, never executed."""

    path_id: str
    source_environment: str
    target_environment: str
    affected_entities: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    required_changes: tuple[str, ...] = ()
    validation_steps: tuple[str, ...] = ()
    rollback: tuple[str, ...] = ()


def _glue_entities(ctx: ProjectContext) -> tuple[list[str], list[str]]:
    """(glue entity ids, observed glue versions) from TF + code."""
    from forge_doctor_data.analyzers.terraform_model import terraform_model

    ents: list[str] = []
    versions: list[str] = []
    for b in terraform_model(ctx).resources:
        if b.labels and b.labels[0] == "aws_glue_job":
            ents.append(f"compute_job:glue:{b.labels[-1]}")
            v = str(b.attrs.get("glue_version") or "")
            if v:
                versions.append(v)
    return sorted(set(ents)), sorted(set(versions))


def _glue_upgrade(ctx: ProjectContext) -> MigrationPlan | None:
    """Glue 4.0 -> 5.x upgrade path (glue/compatibility targets)."""
    ents, versions = _glue_entities(ctx)
    old = [v for v in versions if v.startswith(("3.", "4."))]
    if not old:
        return None
    targets = load_pack("glue", "compatibility").get("targets") or {}
    notes = targets.get("5.0") or {}
    blockers = [
        f"{c.get('change')}: {c.get('detail', '')}"
        for c in notes.get("changes") or []
        if str(c.get("severity")).upper() == "HIGH"
    ]
    warnings = [
        f"{c.get('change')}: {c.get('detail', '')}"
        for c in notes.get("changes") or []
        if str(c.get("severity")).upper() not in ("HIGH",)
    ]
    reg = capability_registry()
    for cap in reg.capabilities_for("glue"):
        b4 = reg.evaluate(cap, platform="glue", version="4.0")
        a5 = reg.evaluate(cap, platform="glue", version="5.0")
        if b4.status != a5.status:
            warnings.append(f"{cap}: {b4.status.value} -> {a5.status.value}")
    return MigrationPlan(
        path_id="glue-4-to-5",
        source_environment=f"glue {min(old)}",
        target_environment="glue 5.0",
        affected_entities=tuple(ents),
        blockers=tuple(sorted(blockers)),
        warnings=tuple(sorted(set(warnings))),
        required_changes=(
            'set glue_version = "5.0" on affected aws_glue_job resources',
            "rebuild native/python deps against Python 3.11",
            "rebuild Java connectors for Java 17",
            "re-verify Spark 3.3 -> 3.5 API usage",
        ),
        validation_steps=(
            "run a copy of each glue job on 5.0 with production-scale input",
            "compare output datasets + row counts",
            "check executor metrics for Spark 3.5 behavior changes",
        ),
        rollback=("keep the 4.0 job definition intact; revert glue_version and rerun",),
    )


def _iceberg_format_v2(ctx: ProjectContext) -> MigrationPlan | None:
    """Iceberg format-version 1 -> 2 for row-level ops."""
    from forge_doctor_data.analyzers.iceberg_model import iceberg_model

    m = iceberg_model(ctx)
    if not m.has_iceberg:
        return None
    fv_prop = m.properties.get("format-version")
    fv_value = fv_prop[0] if fv_prop else ""
    if str(fv_value) == "2":
        return None  # already migrated
    table_names = sorted(m.tables)
    vers = load_pack("iceberg", "versions").get("format_versions") or {}
    note = (vers.get("2") or {}).get("notes", "UNKNOWN")
    compat = load_pack("iceberg", "compatibility").get("runtimes") or {}
    warnings = []
    for engine, facts in compat.items():
        for ver, f in (facts or {}).items():
            if str(f.get("severity", "")).upper() in ("HIGH", "MEDIUM"):
                warnings.append(f"{engine} {ver}: {f.get('note', '')}")
    return MigrationPlan(
        path_id="iceberg-v1-to-v2",
        source_environment="iceberg format-version 1",
        target_environment="iceberg format-version 2",
        affected_entities=tuple(f"table:iceberg:{name}" for name in table_names)
        or (f"iceberg evidence in {len(m.evidence)} site(s)",),
        blockers=(),
        warnings=tuple(sorted(set(warnings))) or ("engine support varies; verify",),
        required_changes=(
            f"set 'format-version'='2' on {len(table_names)} table(s) — {note}",
            "verify readers/writers support v2 (glue 4.0+, athena engine v3, emr 6.x+)",
        ),
        validation_steps=(
            "run MERGE/DELETE on a copy of one migrated table",
            "read the v2 table from every consumer engine",
        ),
        rollback=(
            "format-version upgrades are one-way on row-level ops; snapshot + "
            "RESTORE or recreate the table to revert",
        ),
    )


def _databricks_upgrade(ctx: ProjectContext) -> MigrationPlan | None:
    """Databricks runtime upgrade to current LTS."""
    from forge_doctor_data.analyzers.databricks_model import databricks_model

    m = databricks_model(ctx)
    if not m.has_databricks:
        return None
    rts = {r["dbr"]: r for r in load_pack("databricks", "runtime").get("runtimes") or []}
    observed: set[str] = set()
    ents: list[str] = []
    for c in m.clusters:
        v = c.dbr_version or ""
        if v:
            observed.add(v)
        ents.append(f"compute_job:databricks:{c.name}")
    for j in m.jobs:
        ents.append(f"compute_job:databricks:{j.name}")
    eol = [v for v in observed if rts.get(v, {}).get("status") == "eol"]
    target = next(
        (dbr for dbr in reversed(list(rts)) if rts[dbr].get("status") == "lts"),
        "UNKNOWN",
    )
    warnings = [f"runtime {v} is end-of-life" for v in eol]
    if not observed:
        warnings.append("no DBR versions observed — runtime floor unknown")
    return MigrationPlan(
        path_id="databricks-runtime-upgrade",
        source_environment=f"databricks {sorted(observed) or ['UNKNOWN']}",
        target_environment=f"databricks {target}",
        affected_entities=tuple(sorted(set(ents))),
        blockers=(),
        warnings=tuple(sorted(warnings)),
        required_changes=(
            "pin clusters/jobs to the target DBR",
            "review Delta feature floors vs DBR (PLAT009 semantics)",
            "verify Unity Catalog bindings on new runtimes",
        ),
        validation_steps=(
            "run representative jobs on the target DBR in a staging workspace",
            "compare Delta read/write behavior across DBRs",
        ),
        rollback=("keep the old cluster policies; revert spark_version/dbr pins",),
    )


def _parquet_sites(ctx: ProjectContext) -> list[str]:
    """Parquet writer/reader evidence as entity-ish labels."""
    from forge_doctor_data.analyzers.parquet_model import parquet_model

    m = parquet_model(ctx)
    if not m.has_parquet:
        return []
    return sorted({f"{e.file.as_posix()}:{e.line}:{e.name}" for e in m.writers + m.by_kind("file")})


def _parquet_to_delta(ctx: ProjectContext) -> MigrationPlan | None:
    """Parquet datasets -> Delta tables."""
    sites = _parquet_sites(ctx)
    if not sites:
        return None
    return MigrationPlan(
        path_id="parquet-to-delta",
        source_environment="parquet datasets",
        target_environment="delta tables",
        affected_entities=tuple(sites),
        blockers=(),
        warnings=(
            "delta adds a _delta_log/ transaction log — external readers must use "
            "a delta-aware reader or a manifest",
            "idempotent writes need merge/overwritePartitions — plain appends can "
            "duplicate on retry",
        ),
        required_changes=(
            "rewrite parquet files to delta (CONVERT TO DELTA or a rewrite job)",
            "switch consumers from spark.read.parquet to delta readers",
            "set delta protocol floors per feature usage (deletion_vectors etc.)",
        ),
        validation_steps=(
            "row-count and checksum diff on migrated partitions",
            "verify downstream consumers read the delta tables",
        ),
        rollback=("keep the parquet files until consumers cut over",),
    )


def _parquet_to_iceberg(ctx: ProjectContext) -> MigrationPlan | None:
    """Parquet datasets -> Iceberg tables."""
    sites = _parquet_sites(ctx)
    if not sites:
        return None
    compat = load_pack("iceberg", "compatibility").get("runtimes") or {}
    engines = ", ".join(sorted(compat)) or "UNKNOWN"
    return MigrationPlan(
        path_id="parquet-to-iceberg",
        source_environment="parquet datasets",
        target_environment="iceberg tables",
        affected_entities=tuple(sites),
        blockers=(),
        warnings=(
            f"engine support differs ({engines}); verify per consumer",
            "format-version=2 required for row-level ops post-migration",
        ),
        required_changes=(
            "register an iceberg catalog (glue_catalog/hadoop) before migrating",
            "rewrite or add_files() the parquet datasets into iceberg tables",
            "set 'format-version'='2' where row-level ops are planned",
        ),
        validation_steps=(
            "snapshot the parquet layout before conversion",
            "read via every consumer engine listed in the catalog",
        ),
        rollback=("iceberg keeps data files; drop the table registration to revert",),
    )


def _streaming_modernize(ctx: ProjectContext) -> MigrationPlan | None:
    """Legacy streaming patterns -> modern streaming."""
    from forge_doctor_data.analyzers.streaming_model import streaming_model

    m = streaming_model(ctx)
    legacy = [
        q
        for q in m.queries
        if not q.checkpoint or q.trigger_kind in ("", "once") or q.checkpoint_dynamic
    ]
    if not legacy:
        return None
    return MigrationPlan(
        path_id="streaming-modernize",
        source_environment="legacy streaming (no checkpoint/once triggers)",
        target_environment="checkpointed micro-batch streaming",
        affected_entities=tuple(sorted(f"{q.file.as_posix()}:{q.line}:{q.name}" for q in legacy)),
        blockers=(),
        warnings=(
            "checkpointLocation changes reset committed offsets — plan a "
            "replay window or accept reprocessing",
            "foreachBatch sinks need batch_id dedup to be effectively-once",
        ),
        required_changes=(
            "add a stable checkpointLocation per query",
            "set trigger(processingTime=...) or availableNow explicitly",
            "add watermarks to stateful queries",
            "review delivery semantics via `streaming semantics`",
        ),
        validation_steps=(
            "run the modernized query against a replay window",
            "compare delivery semantics via streaming diagnose output",
        ),
        rollback=("keep the old checkpoint path until the new one proves out",),
    )


def _lambda_runtime(ctx: ProjectContext) -> MigrationPlan | None:
    """Lambda runtime upgrade."""
    from forge_doctor_data.analyzers.lambda_model import lambda_model

    m = lambda_model(ctx)
    fns = [f for f in m.functions if f.runtime]
    if not fns:
        return None
    pack = load_pack("lambda", "runtimes")
    eol_set = {str(r) for r in pack.get("eol") or []}
    eol = sorted({f.runtime for f in fns if f.runtime in eol_set})
    latest = "UNKNOWN"  # pack lists only eol runtimes, not a current target
    return MigrationPlan(
        path_id="lambda-runtime-upgrade",
        source_environment=f"lambda runtimes: {sorted({f.runtime for f in fns})}",
        target_environment=f"lambda {latest}",
        affected_entities=tuple(sorted(f"compute_job:lambda:{f.name}" for f in fns)),
        blockers=(),
        warnings=(
            *(f"runtime {r} is end-of-life" for r in eol),
            "pack lacks a 'current runtime' fact — target is UNKNOWN",
        ),
        required_changes=(
            "update runtime on each function (TF runtime attr / boto3 update)",
            "rebuild layers and dependencies for the new runtime ABI",
        ),
        validation_steps=(
            "invoke each function on the new runtime with production events",
            "check cold-start/duration regression in REPORT lines",
        ),
        rollback=("alias-based traffic shift back to the prior runtime",),
    )


MIGRATIONS: tuple[Callable[[ProjectContext], MigrationPlan | None], ...] = (
    _glue_upgrade,
    _iceberg_format_v2,
    _databricks_upgrade,
    _parquet_to_delta,
    _parquet_to_iceberg,
    _streaming_modernize,
    _lambda_runtime,
)


def plan_migrations(ctx: ProjectContext) -> list[MigrationPlan]:
    """All applicable migration plans, deterministic order."""
    plans = [p for p in (fn(ctx) for fn in MIGRATIONS) if p is not None]
    return sorted(plans, key=lambda p: p.path_id)
