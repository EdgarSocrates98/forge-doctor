"""Cross-domain platform rules (PLAT###) - multi-model derived findings.

Each ``CrossDomainRule`` composes facts from the semantic models, the
canonical ``DataPlatformGraph`` and the ``CapabilityRegistry``. A rule
fires only when its declared prerequisites are observably present; a
missing leg means no finding - never a negative claim from absence of
evidence.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from forge_doctor.analyzers.graph_queries import GraphTraversal
from forge_doctor.analyzers.platform_graph_builder import _AIRFLOW_OPERATOR_TARGETS
from forge_doctor.core.capabilities import CapabilityStatus
from forge_doctor.core.crossdomain import (
    ContributingFact,
    CrossDomainHit,
    CrossDomainRule,
    RuleContext,
    rule_context,
)
from forge_doctor.core.models import CheckResult, Severity
from forge_doctor.core.platform_graph import EntityKind as K
from forge_doctor.core.platform_graph import RelKind as R
from forge_doctor.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext

_ORCHESTRATORS = {"airflow", "controlm", "stepfunctions"}
_APPEND_OPS = {"insert"}
_IDEMPOTENT_OPS = {
    "merge",
    "update",
    "delete",
    "overwrite",
    "overwritepartitions",
    "createorreplace",
}
_ROW_OPS = {"merge": "ICEBERG_MERGE_WRITE", "update": "ICEBERG_UPDATE", "delete": "ICEBERG_DELETE"}
_NON_TXN_SINK_DOMAINS = {"dynamodb", "neptune"}
# Dedup evidence in a sink file: a conditional write, or the microbatch
# id actually used inside a key/item literal (a bare ``batch_id`` param
# name never used in the write is not dedup).
_DEDUP_TOKENS = re.compile(
    r"condition_?expression|\b(?:Item|Key)\s*=\s*\{[^}]*batch_?id", re.IGNORECASE | re.DOTALL
)


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:2])


def _fact(
    plane: str, label: str, detail: str, file: Path | None = None, line: int | None = None
) -> ContributingFact:
    return ContributingFact(plane=plane, label=label, detail=detail, file=file, line=line)


def _retrying_tasks(rc: RuleContext) -> list[tuple[str, str, Path, int]]:
    """(task_id, retries, file, line) for airflow tasks with retries>0."""
    from forge_doctor.analyzers.airflow_model import airflow_model

    model = airflow_model(rc.ctx)
    defaults = {d.dag_id or d.var: d.default_retries for d in model.dags}
    out: list[tuple[str, str, Path, int]] = []
    for task in model.tasks:
        raw = task.retries or defaults.get(task.dag, "")
        try:
            retries = int(str(raw).strip())
        except ValueError:
            continue
        if retries > 0:
            out.append((task.task_id, str(retries), task.file, task.line))
    return out


def _task_targets(rc: RuleContext) -> dict[str, str]:
    """Airflow task_id -> canonical compute/workflow entity id."""
    from forge_doctor.analyzers.airflow_model import airflow_model

    out: dict[str, str] = {}
    for task in airflow_model(rc.ctx).tasks:
        kd = _AIRFLOW_OPERATOR_TARGETS.get(task.operator)
        if task.target and kd:
            kind, domain = kd
            ident = task.target.rsplit(":", 1)[-1].rsplit("/", 1)[-1] or task.target
            out[task.task_id] = f"{kind.value}:{domain}:{ident}"
    return out


def _append_sinks(rc: RuleContext) -> tuple[list[tuple[str, Path, int]], bool]:
    """Append-style Iceberg writes + whether idempotent ops exist.

    Append-style = a SQL ``INSERT`` (non-overwrite) or a Python ``writeTo``
    v2 API chain whose file carries an Iceberg format marker and does not
    terminate in an overwrite/createOrReplace op.
    """
    from forge_doctor.analyzers.iceberg_model import iceberg_model

    model = iceberg_model(rc.ctx)
    overwrite_chain = re.compile(r"createorreplace|overwrite", re.IGNORECASE)
    v2 = [ev for ev in model.by_kind("write_api") if ev.name == "v2"]
    append_files = {ev.file for ev in v2 if not overwrite_chain.search(ev.value)}
    overwrite_files = {ev.file for ev in v2 if overwrite_chain.search(ev.value)}
    appends = {
        (ev.value, ev.file, ev.line)
        for ev in model.operations
        if ev.name in _APPEND_OPS
        or (ev.name == "write" and ev.file in append_files and ev.file not in overwrite_files)
    }
    idempotent = bool(model.operation_names & _IDEMPOTENT_OPS) or bool(overwrite_files)
    return sorted(appends, key=lambda t: (t[0], t[1].as_posix(), t[2])), idempotent


def _eval_plat001(rc: RuleContext) -> list[CrossDomainHit]:
    tasks = _retrying_tasks(rc)
    if not tasks:
        return []
    appends, idempotent = _append_sinks(rc)
    if not appends:
        return []
    targets = _task_targets(rc)
    hits: list[CrossDomainHit] = []
    for task_id, retries, file, line in tasks:
        facts = [_fact("orchestration", "airflow task", f"{task_id} retries={retries}", file, line)]
        job = targets.get(task_id)
        if job:
            facts.append(_fact("compute", "invokes", job))
        for table, tfile, tline in appends:
            facts.append(_fact("sink", "append write", f"iceberg table {table}", tfile, tline))
        facts.append(
            _fact(
                "sink",
                "idempotency",
                "merge/update/overwrite evidence"
                if idempotent
                else "no idempotent write evidence observed",
            )
        )
        if idempotent:
            continue  # idempotent path observed - no finding
        hits.append(
            CrossDomainHit(
                message=(
                    f"retried task {task_id!r} + append-only Iceberg writes "
                    "- duplicate-write risk on retry"
                ),
                facts=tuple(facts),
                file=file,
                line=line,
            )
        )
    return hits


def _glue_versions(rc: RuleContext) -> dict[str, tuple[str, Path, int]]:
    """glue job name -> (declared version, file, line) from Terraform."""
    from forge_doctor.analyzers.terraform_model import terraform_model

    out: dict[str, tuple[str, Path, int]] = {}
    for res in terraform_model(rc.ctx).resources:
        if not res.labels or res.labels[0] != "aws_glue_job":
            continue
        version = str(res.attrs.get("glue_version") or "")
        name = str(res.attrs.get("name") or res.labels[-1])
        if version:
            out[name] = (version, res.file, res.line)
    return out


def _iceberg_format_version(rc: RuleContext) -> str:
    """Observed table format-version property ("" when undeclared)."""
    from forge_doctor.analyzers.iceberg_model import iceberg_model

    props = iceberg_model(rc.ctx).properties.get("format-version")
    if props is None:
        props = iceberg_model(rc.ctx).properties.get("format_version")
    return props[0] if props else ""


def _eval_plat002(rc: RuleContext) -> list[CrossDomainHit]:
    """Glue version x row-level Iceberg op evaluated UNSUPPORTED."""
    from forge_doctor.analyzers.iceberg_model import iceberg_model

    versions = _glue_versions(rc)
    ops = iceberg_model(rc.ctx).operations
    row_ops = sorted({ev.name for ev in ops if ev.name in _ROW_OPS})
    if not (versions and row_ops):
        return []
    fmt = _iceberg_format_version(rc)
    hits: list[CrossDomainHit] = []
    for job, (version, jfile, jline) in sorted(versions.items()):
        for op in row_ops:
            res = rc.capability(
                _ROW_OPS[op],
                platform="glue",
                version=version,
                format_version=fmt,
            )
            if res.status is not CapabilityStatus.UNSUPPORTED:
                continue
            hits.append(
                CrossDomainHit(
                    message=(
                        f"glue job {job!r} (version {version}) cannot perform "
                        f"iceberg {op} writes used in code - {res.status.value}"
                    ),
                    facts=(
                        _fact("config", "glue_version", f"{job}={version}", jfile, jline),
                        _fact("capability", _ROW_OPS[op], res.reason or res.status.value),
                        _fact("source", "pack", res.pack or "-"),
                    ),
                    file=jfile,
                    line=jline,
                )
            )
    return hits


def _eval_plat003(rc: RuleContext) -> list[CrossDomainHit]:
    """Continuous stream writer to table + no maintenance evidence."""
    from forge_doctor.analyzers.iceberg_model import iceberg_model
    from forge_doctor.analyzers.streaming_model import streaming_model

    maintenance = iceberg_model(rc.ctx).maintenance_names
    hits: list[CrossDomainHit] = []
    for q in streaming_model(rc.ctx).queries:
        if q.sink not in {"iceberg", "delta"}:
            continue
        if q.trigger_kind not in {"processingTime", "continuous"}:
            continue
        if maintenance or _maintains_storage(rc):
            continue
        hits.append(
            CrossDomainHit(
                message=(
                    f"stream {q.name!r} writes {q.sink} every "
                    f"{q.trigger_arg or q.trigger_kind} with no compaction/"
                    "snapshot maintenance observed - small-file/snapshot growth"
                ),
                facts=(
                    _fact(
                        "streaming",
                        "writer",
                        f"{q.name} trigger={q.trigger_kind}",
                        q.file,
                        q.line,
                    ),
                    _fact("sink", "format", q.sink),
                    _fact("storage", "maintenance", "no maintenance ops observed"),
                ),
                file=q.file,
                line=q.line,
                severity=Severity.INFO,
            )
        )
    return hits


def _maintains_storage(rc: RuleContext) -> bool:
    """Vacuum/optimize/compaction call sites anywhere in project code."""
    from forge_doctor.analyzers.index import project_index

    for module in project_index(rc.ctx).modules.values():
        for site in module.calls:
            name = (site.name or site.dotted).lower()
            if any(t in name for t in ("optimize", "vacuum", "compact", "expire_snapshots")):
                return True
    return False


def _eval_plat004(rc: RuleContext) -> list[CrossDomainHit]:
    """Same entity invoked by two different orchestrator domains."""
    invokers: dict[str, set[str]] = {}
    for rel in rc.graph.relationships(R.INVOKES):
        src = rc.graph.entity(rel.src)
        if src is not None and src.domain in _ORCHESTRATORS:
            invokers.setdefault(rel.dst, set()).add(src.domain)
    hits: list[CrossDomainHit] = []
    for dst, domains in sorted(invokers.items()):
        if len(domains) < 2:
            continue
        target = rc.graph.entity(dst)
        hits.append(
            CrossDomainHit(
                message=(
                    f"{dst} invoked from {len(domains)} orchestrators "
                    f"({', '.join(sorted(domains))}) - duplicate ownership risk"
                ),
                facts=tuple(
                    _fact("orchestration", "invoker", f"{d} -> {dst}") for d in sorted(domains)
                ),
                file=target.file if target else None,
                line=target.line if target else None,
            )
        )
    return hits


def _eval_plat005(rc: RuleContext) -> list[CrossDomainHit]:
    """IaC-declared runtime vs source-code version assumptions."""
    from forge_doctor.analyzers.glue_ast import GlueAnalyzer
    from forge_doctor.analyzers.index import project_index
    from forge_doctor.analyzers.pyproject import requires_python
    from forge_doctor.analyzers.terraform_model import terraform_model

    hits: list[CrossDomainHit] = []
    pyproject = rc.ctx.pyproject
    floor = requires_python(pyproject) if isinstance(pyproject, dict) else None
    if floor:
        m = re.search(r"(\d+)\.(\d+)", floor)
        if m:
            floor_tuple = (int(m.group(1)), int(m.group(2)))
            for res in terraform_model(rc.ctx).resources:
                if not res.labels or res.labels[0] != "aws_lambda_function":
                    continue
                runtime = str(res.attrs.get("runtime") or "")
                rm = re.match(r"python(\d+)\.(\d+)", runtime)
                if rm and (int(rm.group(1)), int(rm.group(2))) < floor_tuple:
                    hits.append(
                        CrossDomainHit(
                            message=(
                                f"lambda {res.labels[-1]!r} runtime {runtime} below "
                                f"project requires-python {floor!r}"
                            ),
                            facts=(
                                _fact("config", "terraform runtime", runtime, res.file, res.line),
                                _fact("source", "requires-python", floor),
                            ),
                            file=res.file,
                            line=res.line,
                        )
                    )
    # Glue: version pinned in Terraform vs version pinned in job code.
    tf_versions = _glue_versions(rc)
    if tf_versions:
        analyzer = GlueAnalyzer()
        pins: list[tuple[str, Path, int]] = []
        for module in project_index(rc.ctx).modules.values():
            text = rc.ctx.read_text(module.file) or ""
            for f in analyzer.analyze_source(text, module.file):
                if f.pattern.startswith("glue_version:"):
                    pins.append((f.pattern.split(":", 1)[1], module.file, f.line))
        for job, (version, jfile, jline) in sorted(tf_versions.items()):
            for pinned, pfile, pline in pins:
                if pinned and pinned != version:
                    hits.append(
                        CrossDomainHit(
                            message=(
                                f"glue version drift: terraform declares {version} "
                                f"for {job!r} but code pins {pinned}"
                            ),
                            facts=(
                                _fact("config", "terraform glue_version", version, jfile, jline),
                                _fact("source", "code glue_version", pinned, pfile, pline),
                            ),
                            file=jfile,
                            line=jline,
                            severity=Severity.INFO,
                        )
                    )
    return hits


def _eval_plat006(rc: RuleContext) -> list[CrossDomainHit]:
    """Iceberg format-version=2 table + consumer lacking v2 support."""
    from forge_doctor.analyzers.iceberg_model import iceberg_model

    model = iceberg_model(rc.ctx)
    fmt = _iceberg_format_version(rc)
    if not fmt or fmt not in {"2", "2.0"}:
        return []
    readers = sorted(
        {
            rel.src
            for table in model.tables
            for rel in rc.graph.inbound(f"table:iceberg:{table}", R.READS)
        }
        | {
            rel.src
            for table in model.tables
            for rel in rc.graph.inbound(f"table:iceberg:{table}", R.WRITES)
        }
    )
    if not readers:
        return []
    hits: list[CrossDomainHit] = []
    runtimes = model.runtimes
    for reader in readers:
        res = rc.capability(
            "ICEBERG_FORMAT_V2",
            platform="spark",
            version=next((ev.value for ev in runtimes.values() if ev.value), ""),
            format_version=fmt,
        )
        severity = Severity.WARNING if res.status is CapabilityStatus.UNSUPPORTED else Severity.INFO
        hits.append(
            CrossDomainHit(
                severity=severity,
                message=(
                    f"format-version=2 iceberg table has consumer {reader} - "
                    f"row-level file support: {res.status.value}"
                ),
                facts=(
                    _fact("storage", "format-version", fmt),
                    _fact("consumer", "reader", reader),
                    _fact("capability", "ICEBERG_FORMAT_V2", res.status.value),
                ),
            )
        )
    return hits


def _eval_plat007(rc: RuleContext) -> list[CrossDomainHit]:
    """Microbatch sink to non-transactional store without dedup evidence."""
    from forge_doctor.analyzers.dynamodb_model import dynamodb_model
    from forge_doctor.analyzers.streaming_model import streaming_model

    ddb_files = {
        a.file
        for a in dynamodb_model(rc.ctx).accesses
        if a.op.startswith(("put", "update", "batch_write", "transact_write"))
    }
    nep_files = {q.file for q in neptune_queries_safe(rc) if q.writes}
    side_effect_files = ddb_files | nep_files
    hits: list[CrossDomainHit] = []
    for q in streaming_model(rc.ctx).queries:
        side_effect = q.sink in _NON_TXN_SINK_DOMAINS
        foreach = q.foreach_batch and q.file in side_effect_files
        if not (side_effect or foreach):
            continue
        text = rc.ctx.read_text(q.file) or ""
        dedup = bool(_DEDUP_TOKENS.search(text)) or bool(q.checkpoint)
        if dedup:
            continue
        hits.append(
            CrossDomainHit(
                message=(
                    f"microbatch sink {q.sink or 'foreachBatch'} in {q.name!r} "
                    "has no checkpoint/dedup evidence - retried batches can "
                    "double-apply side effects"
                ),
                facts=(
                    _fact(
                        "streaming",
                        "sink",
                        f"{q.name} -> {q.sink or 'foreachBatch'}",
                        q.file,
                        q.line,
                    ),
                    _fact("sink", "dedup", "no batch_id/condition/checkpoint evidence"),
                ),
                file=q.file,
                line=q.line,
            )
        )
    return hits


def _eval_plat008(rc: RuleContext) -> list[CrossDomainHit]:
    """EMR release + Iceberg write ops + Lake Formation auth present."""
    from forge_doctor.analyzers.emr_model import emr_model
    from forge_doctor.analyzers.iceberg_model import iceberg_model
    from forge_doctor.analyzers.lakeformation_model import lakeformation_model

    emr = emr_model(rc.ctx)
    if not emr.has_emr:
        return []
    ice = iceberg_model(rc.ctx)
    write_ops = [ev for ev in ice.operations if ev.name.lower() in _IDEMPOTENT_OPS]
    if not write_ops:
        return []
    lf = lakeformation_model(rc.ctx)
    # risk: iceberg writes on EMR while LF governs the catalog - the writer
    # must run LF-integrated (security configuration) or bypasses grants.
    hits: list[CrossDomainHit] = []
    for c in emr.clusters:
        if not lf.has_lakeformation:
            break
        if not c.security_configuration:
            hits.append(
                CrossDomainHit(
                    message=(
                        f"EMR cluster {c.name} ({c.release}) writes Iceberg tables "
                        "under Lake Formation but has no security_configuration - "
                        "LF grants are not evaluated for this path"
                    ),
                    file=c.file,
                    line=c.line,
                    severity=Severity.WARNING,
                    facts=(
                        _fact(
                            "config", "emr", f"cluster {c.name} release={c.release}", c.file, c.line
                        ),
                        _fact("code", "iceberg", f"{len(write_ops)} row-level write op(s)"),
                        _fact("config", "lakeformation", "LF grants/resources present"),
                    ),
                )
            )
    return hits


def _eval_plat009(rc: RuleContext) -> list[CrossDomainHit]:
    """Databricks runtime < Delta feature protocol floor."""
    from forge_doctor.analyzers.databricks_model import databricks_model
    from forge_doctor.analyzers.delta_model import delta_model

    dbx = databricks_model(rc.ctx)
    delta = delta_model(rc.ctx)
    if not (dbx.has_databricks and delta.has_delta):
        return []
    hits: list[CrossDomainHit] = []
    for c in dbx.clusters:
        ver = _version_tuple(c.dbr_version)
        if not ver:
            continue
        for cap, floor, feat in (
            ("DELTA_DELETION_VECTORS", (14, 1), "deletion_vectors"),
            ("DELTA_LIQUID_CLUSTERING", (15, 1), "liquid_clustering"),
            ("DELTA_CDF", (10, 4), "cdf"),
            ("DELTA_COLUMN_MAPPING", (10, 4), "column_mapping"),
        ):
            if feat not in delta.features:
                continue
            res = rc.caps.evaluate(cap, platform="databricks", version=c.dbr_version)
            if res.status == CapabilityStatus.UNSUPPORTED or (
                ver < floor and res.status != CapabilityStatus.SUPPORTED
            ):
                hits.append(
                    CrossDomainHit(
                        message=(
                            f"cluster '{c.name}' DBR {c.dbr_version} + Delta feature "
                            f"'{feat}' - {res.reason[:80]}"
                        ),
                        file=c.file,
                        line=c.line,
                        severity=Severity.WARNING,
                        facts=(
                            _fact(
                                "config",
                                "databricks",
                                f"{c.name} dbr={c.dbr_version}",
                                c.file,
                                c.line,
                            ),
                            _fact("code", "delta", f"feature {feat} exercised"),
                        ),
                    )
                )
    return hits


def neptune_queries_safe(rc: RuleContext) -> list[GraphTraversal]:
    from forge_doctor.analyzers.neptune_queries import neptune_queries

    return list(neptune_queries(rc.ctx).queries)


class _PlatCheck(CheckBase):
    """Adapter: one Check per CrossDomainRule."""

    category = "platform"
    rule: CrossDomainRule

    def __init__(self, rule: CrossDomainRule) -> None:
        self.rule = rule
        self.id = rule.id
        self.title = rule.title
        self.why = rule.why
        self.when_ok = rule.when_ok
        self.fix = rule.fix
        self.severity = rule.severity
        self.confidence = rule.confidence
        self.evidence_kind = rule.evidence_kind

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        rc = rule_context(ctx)
        results: list[CheckResult] = []
        for hit in self.rule.run(rc):
            details = "\n".join(f"  {f.render()}" for f in hit.facts)
            results.append(
                self.result(
                    hit.severity or self.severity,
                    f"{hit.message}\n{details}" if details else hit.message,
                    file=hit.file,
                    line=hit.line,
                )
            )
        return results


RULES: tuple[CrossDomainRule, ...] = (
    CrossDomainRule(
        id="PLAT001",
        title="Orchestration retry + non-idempotent sink",
        description=(
            "A retrying orchestration task drives writes into an append-only "
            "sink with no idempotent write evidence - retries can duplicate."
        ),
        why="Retried pipelines into append-only sinks duplicate rows on failure.",
        when_ok="Retries are 0, sinks support upsert/overwrite, or idempotency evidence exists.",
        fix="Add MERGE/upsert semantics, dedupe keys, or disable blind retries.",
        required_entity_kinds=frozenset({K.TASK}),
        required_relationships=frozenset({R.INVOKES}),
        evaluate=_eval_plat001,
    ),
    CrossDomainRule(
        id="PLAT002",
        title="Runtime/config feature incompatibility",
        description=(
            "Declared runtime version cannot perform a capability the source "
            "code exercises (capability engine verdict: UNSUPPORTED)."
        ),
        why="Code and declared runtime disagree on a versioned capability.",
        when_ok="Declared versions cover every exercised capability.",
        fix="Bump the runtime version or remove the unsupported operation.",
        required_entity_kinds=frozenset({K.COMPUTE_JOB}),
        required_capabilities=("ICEBERG_MERGE_WRITE",),
        evaluate=_eval_plat002,
    ),
    CrossDomainRule(
        id="PLAT003",
        title="Continuous writer + storage maintenance gap",
        description=(
            "A stream writes small batches into a lakehouse table with no "
            "compaction/snapshot-expiry evidence."
        ),
        why="Continuous micro-batch writes amplify small files and metadata.",
        when_ok="Streaming sinks are batch tables with scheduled maintenance jobs.",
        fix="Schedule OPTIMIZE/expire_snapshots or raise the trigger interval.",
        severity=Severity.INFO,
        required_entity_kinds=frozenset({K.STREAM}),
        required_relationships=frozenset({R.PRODUCES}),
        evaluate=_eval_plat003,
    ),
    CrossDomainRule(
        id="PLAT004",
        title="Duplicate orchestration ownership",
        description="Two orchestrator domains invoke the same compute/workflow entity.",
        why="The same job owned by two schedulers double-runs and fights retries.",
        when_ok="Each compute job is invoked by exactly one orchestrator.",
        fix="Pick one orchestrator per job; remove the duplicate trigger.",
        required_entity_kinds=frozenset({K.TASK}),
        required_relationships=frozenset({R.INVOKES}),
        evaluate=_eval_plat004,
    ),
    CrossDomainRule(
        id="PLAT005",
        title="IaC runtime config vs source assumptions",
        description=(
            "A runtime declared in IaC contradicts the version/dependency "
            "the source assumes (lambda runtime vs requires-python, glue "
            "version drift)."
        ),
        why="IaC and code disagreeing on runtime breaks at deploy time.",
        when_ok="Declared runtime satisfies the source's version floor.",
        fix="Align the IaC runtime with the code's requirements.",
        evaluate=_eval_plat005,
    ),
    CrossDomainRule(
        id="PLAT006",
        title="Table format + consumer compatibility mismatch",
        description=(
            "A row-level-format table (Iceberg format-version=2) has "
            "consumers whose engine support is not evidenced."
        ),
        why="v2 row-level files need capable readers; weaker engines fail or misread.",
        when_ok="Every observed consumer runtime covers the table format version.",
        fix="Upgrade reader engines or keep the table at format-version=1.",
        severity=Severity.INFO,
        required_entity_kinds=frozenset({K.TABLE}),
        evaluate=_eval_plat006,
    ),
    CrossDomainRule(
        id="PLAT007",
        title="Stream sink retry + side-effect idempotency risk",
        description=(
            "A microbatch writer sinks into a non-transactional store "
            "(dynamodb/neptune) without checkpoint or dedup evidence."
        ),
        why="Retried microbatches re-run sink side effects unless dedup is explicit.",
        when_ok="foreachBatch handlers use batch_id/conditional writes or checkpoints exist.",
        fix="Key the sink writes on epoch/batch id or enable checkpointing.",
        required_entity_kinds=frozenset({K.STREAM}),
        evaluate=_eval_plat007,
    ),
    CrossDomainRule(
        id="PLAT008",
        title="EMR + Iceberg writes under Lake Formation without LF integration",
        description=(
            "An EMR cluster writes Iceberg tables while Lake Formation "
            "governs the catalog, but the cluster has no security "
            "configuration - the LF grants are bypassed for that path."
        ),
        why="Iceberg writes on EMR without LF integration ignore catalog grants.",
        when_ok="EMR clusters that touch governed tables run an LF-integrated security config.",
        fix="Attach an aws_emr_security_configuration with the Lake Formation integration.",
        required_entity_kinds=frozenset({K.COMPUTE_JOB}),
        required_capabilities=("EMR_LAKE_FORMATION", "EMR_ICEBERG"),
        evaluate=_eval_plat008,
    ),
    CrossDomainRule(
        id="PLAT009",
        title="Databricks runtime below Delta feature floor",
        description=(
            "A Delta feature (deletion vectors, liquid clustering, CDF, "
            "column mapping) is exercised on a Databricks cluster whose "
            "runtime predates the feature's protocol floor."
        ),
        why="Feature-enabled tables fail or downgrade on runtimes below the protocol floor.",
        when_ok="Cluster DBR versions satisfy every detected Delta feature's protocol floor.",
        fix="Upgrade the cluster's spark_version or drop the feature.",
        required_capabilities=("DELTA_DELETION_VECTORS",),
        evaluate=_eval_plat009,
    ),
)

CHECKS: list[Check] = [_PlatCheck(rule) for rule in RULES]
