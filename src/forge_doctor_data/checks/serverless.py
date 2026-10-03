"""Athena / Lambda / Step-Functions-deepening checks - evidence-gated."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from forge_doctor_data.core.models import Confidence, EvidenceKind, Severity
from forge_doctor_data.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor_data.core.context import ProjectContext
    from forge_doctor_data.core.models import CheckResult


def _athena(ctx: ProjectContext) -> Any:
    from forge_doctor_data.analyzers.athena_model import athena_model

    return athena_model(ctx)


def _lambda(ctx: ProjectContext) -> Any:
    from forge_doctor_data.analyzers.lambda_model import lambda_model

    return lambda_model(ctx)


def _sfn(ctx: ProjectContext) -> Any:
    from forge_doctor_data.analyzers.stepfunctions_model import stepfunctions_model

    return stepfunctions_model(ctx)


class _ServerlessCheck(CheckBase):
    category = "serverless"
    evidence_kind = EvidenceKind.CONFIG


# -------------------------------------------------------------- Athena


class AthenaUsage(_ServerlessCheck):
    """ATH000: anchor - workgroups/catalogs/queries footprint."""

    id = "ATH000"
    title = "Athena usage"
    why = "Anchors the serverless-analytics domain for downstream checks."
    when_ok = "Always passes; informational census."
    fix = "n/a"
    confidence = Confidence.MEDIUM

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _athena(ctx)
        if not m.has_athena:
            return [self.result(Severity.PASS, "no Athena workgroups/queries/config detected")]
        engines = sorted({w.engine_version for w in m.workgroups if w.engine_version})
        ops = sorted({o.op for o in m.sql_ops})
        return [
            self.result(
                Severity.INFO,
                f"{len(m.workgroups)} workgroup(s), {len(m.catalogs)} catalog(s), "
                f"{len(m.named_queries)} named/prepared quer(y|ies), "
                f"{len(m.sql_ops)} athena-sql op(s) [{', '.join(ops) or 'none'}]"
                + (f"; engines: {', '.join(engines)}" if engines else ""),
            )
        ]


class AthenaLegacyEngine(_ServerlessCheck):
    """ATH001: Iceberg DDL but workgroup engine below Athena engine 3."""

    id = "ATH001"
    title = "Athena engine below v3 with Iceberg evidence"
    why = (
        "Iceberg on Athena requires engine version 3 - an engine-1/2 "
        "workgroup fails on Iceberg DDL/DML entirely."
    )
    when_ok = "Iceberg evidence coexists only with engine-3 workgroups."
    fix = "Set engine_version to 'Athena engine version 3'."
    confidence = Confidence.MEDIUM

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _athena(ctx)
        if not m.iceberg_ddl:
            return []
        results = []
        for w in m.workgroups:
            ver = re.search(r"(\d+)", w.engine_version)
            if ver and int(ver.group(1)) < 3:
                results.append(
                    self.result(
                        Severity.WARNING,
                        f"workgroup '{w.name}' on '{w.engine_version}' but the "
                        "project has Iceberg DDL (needs engine 3)",
                        file=w.file,
                        line=w.line,
                    )
                )
        return results


class AthenaNoResultLocation(_ServerlessCheck):
    """ATH002: workgroup without an enforced result location."""

    id = "ATH002"
    title = "Workgroup without enforced result configuration"
    why = (
        "Without enforce_workgroup_configuration + output_location, query "
        "results land wherever each client chooses - no cost/governance "
        "control."
    )
    when_ok = "Workgroups enforce a governed output location."
    fix = "Set result_configuration.output_location + enforce_workgroup_configuration."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.INFO,
                f"workgroup '{w.name}' has no enforced result location",
                file=w.file,
                line=w.line,
            )
            for w in _athena(ctx).workgroups
            if not (w.enforce_config and w.result_location)
        ]


class AthenaNoScanCutoff(_ServerlessCheck):
    """ATH003: workgroup without bytes_scanned_cutoff - no cost guard."""

    id = "ATH003"
    title = "Workgroup without bytes-scanned cutoff"
    why = (
        "bytes_scanned_cutoff_per_query aborts runaway scans - without it "
        "one bad query can scan the whole lake."
    )
    when_ok = "Workgroups set bytes_scanned_cutoff_per_query."
    fix = "Set bytes_scanned_cutoff_per_query on the workgroup configuration."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.INFO,
                f"workgroup '{w.name}' has no bytes_scanned_cutoff_per_query",
                file=w.file,
                line=w.line,
            )
            for w in _athena(ctx).workgroups
            if not w.bytes_scanned_cutoff
        ]


class AthenaSqlNoWorkgroup(_ServerlessCheck):
    """ATH004: CTAS/UNLOAD/prepared-statement SQL but no workgroup declared."""

    id = "ATH004"
    title = "Athena-shaped SQL without a declared workgroup"
    why = (
        "CTAS/UNLOAD/PREPARE statements exist but the project declares no "
        "aws_athena_workgroup - the execution context is unmanaged or lives "
        "outside this repo."
    )
    when_ok = "Athena-shaped SQL coexists with a declared workgroup."
    fix = "Declare the executing workgroup in IaC (or document it externally)."
    evidence_kind = EvidenceKind.DERIVED
    confidence = Confidence.MEDIUM

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _athena(ctx)
        heavy = [o for o in m.sql_ops if o.op in ("ctas", "unload", "prepare")]
        if heavy and not m.workgroups:
            return [
                self.result(
                    Severity.INFO,
                    f"{len(heavy)} CTAS/UNLOAD/PREPARE statement(s) but no "
                    "aws_athena_workgroup declared in this project",
                )
            ]
        return []


class AthenaClientSidePolling(_ServerlessCheck):
    """ATH005: boto3 start_query_execution + get_query_execution polling."""

    id = "ATH005"
    title = "Client-side Athena polling pattern"
    why = (
        "start_query_execution followed by get_query_execution loops is "
        "manual polling - Step Functions `.sync` integrations or "
        "result-reuse remove the wait code entirely."
    )
    when_ok = "No hand-rolled poll loop around start_query_execution."
    fix = "Use states:::aws-sdk:athena:startQueryExecution.sync or prepared statements."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        calls = set(_athena(ctx).boto3_calls)
        if {"athena.start_query_execution", "athena.get_query_execution"} <= calls:
            return [
                self.result(
                    Severity.INFO,
                    "boto3 athena start_query_execution + get_query_execution "
                    "seen together - a hand-rolled polling loop",
                )
            ]
        return []


# -------------------------------------------------------------- Lambda


class LambdaUsage(_ServerlessCheck):
    """LAM000: anchor - functions/event-sources/layers footprint."""

    id = "LAM000"
    title = "Lambda usage"
    why = "Anchors the serverless-compute domain for downstream checks."
    when_ok = "Always passes; informational census."
    fix = "n/a"
    confidence = Confidence.MEDIUM

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _lambda(ctx)
        if not m.has_lambda:
            return [self.result(Severity.PASS, "no Lambda functions/config detected")]
        runtimes = sorted({f.runtime for f in m.functions if f.runtime})
        src_kinds = sorted({s.kind for s in m.event_sources})
        return [
            self.result(
                Severity.INFO,
                f"{len(m.functions)} function(s), {len(m.event_sources)} event "
                f"source(s) [{', '.join(src_kinds) or 'none'}], "
                f"{len(m.layer_versions)} layer version(s)"
                + (f"; runtimes: {', '.join(runtimes)}" if runtimes else ""),
            )
        ]


class LambdaEolRuntime(_ServerlessCheck):
    """LAM001: function on an end-of-life runtime."""

    id = "LAM001"
    title = "End-of-life Lambda runtime"
    why = (
        "EOL runtimes stop receiving security patches and are blocked from "
        "create/update; the pack of EOL runtimes is versioned, not guessed."
    )
    when_ok = "All functions run a supported runtime."
    fix = "Upgrade to a current runtime (e.g. python3.12+, nodejs20.x+)."
    confidence = Confidence.MEDIUM

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        from forge_doctor_data.analyzers.lambda_model import runtime_eol

        return [
            self.result(
                Severity.WARNING,
                f"{f.name} pinned to EOL runtime {f.runtime}",
                file=f.file,
                line=f.line,
            )
            for f in _lambda(ctx).functions
            if f.runtime and runtime_eol(f.runtime)
        ]


class LambdaAsyncNoDlq(_ServerlessCheck):
    """LAM002: async/event-triggered function without DLQ or destination."""

    id = "LAM002"
    title = "Event-triggered function without failure destination"
    why = (
        "Async invocations (S3/SNS/event-driven) that fail without a DLQ or "
        "on-failure destination drop the event silently."
    )
    when_ok = "Event-driven functions set dead_letter_config or an on-failure destination."
    fix = "Add dead_letter_config or aws_lambda_function_event_invoke_config."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _lambda(ctx)
        triggered = {s.function for s in m.event_sources if s.kind != "schedule"}
        with_dest = {d.function for d in m.destinations}
        results = []
        for f in m.functions:
            attached = any(f.matches(t) for t in triggered)
            has_dest = any(f.matches(d) for d in with_dest)
            if attached and not f.dlq and not has_dest:
                results.append(
                    self.result(
                        Severity.INFO,
                        f"{f.name} is event-triggered with no DLQ/destination",
                        file=f.file,
                        line=f.line,
                    )
                )
        return results


class LambdaNoConcurrencyBound(_ServerlessCheck):
    """LAM003: stream-triggered function without reserved concurrency."""

    id = "LAM003"
    title = "Stream-triggered function without concurrency bound"
    why = (
        "Kinesis/DynamoDB/SQS-triggered functions without "
        "reserved_concurrent_executions can scale to the account limit and "
        "starve neighbors (or hammer a downstream)."
    )
    when_ok = "Stream-triggered functions declare a concurrency bound."
    fix = "Set reserved_concurrent_executions on stream-fed functions."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _lambda(ctx)
        stream_fed = {
            s.function
            for s in m.event_sources
            if s.kind in ("kinesis", "dynamodb", "sqs", "kafka", "msk", "stream")
        }
        return [
            self.result(
                Severity.INFO,
                f"{f.name} is stream-triggered with no concurrency bound",
                file=f.file,
                line=f.line,
            )
            for f in m.functions
            if any(f.matches(s) for s in stream_fed)
            and f.reserved_concurrency < 0
            and not f.provisioned_concurrency
        ]


class LambdaMaxTimeout(_ServerlessCheck):
    """LAM004: timeout unset or pinned at the 15-minute ceiling."""

    id = "LAM004"
    title = "Function timeout unset or at ceiling"
    why = (
        "A missing timeout defaults to 3s (surprising truncation); 900s "
        "masks hangs. Most workloads want an explicit, smaller bound."
    )
    when_ok = "Functions set a deliberate timeout."
    fix = "Set timeout to a measured bound."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        results = []
        for f in _lambda(ctx).functions:
            if f.timeout_s == 0:
                results.append(
                    self.result(
                        Severity.INFO,
                        f"{f.name} has no timeout (defaults to 3s)",
                        file=f.file,
                        line=f.line,
                    )
                )
            elif f.timeout_s >= 900:
                results.append(
                    self.result(
                        Severity.INFO,
                        f"{f.name} pinned at timeout={f.timeout_s}s (15m ceiling)",
                        file=f.file,
                        line=f.line,
                    )
                )
        return results


class LambdaVpcNoMemory(_ServerlessCheck):
    """LAM005: VPC-attached function on default memory."""

    id = "LAM005"
    title = "VPC-attached function on default memory"
    why = (
        "VPC cold starts are already slow; 128MB default memory makes them "
        "worse. Bump memory unless deliberately minimal."
    )
    when_ok = "VPC functions set memory_size explicitly."
    fix = "Set memory_size above 128 for VPC-attached functions."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.INFO,
                f"{f.name} is VPC-attached with default memory (128MB)",
                file=f.file,
                line=f.line,
            )
            for f in _lambda(ctx).functions
            if f.vpc and f.memory_mb and f.memory_mb <= 128
        ]


# --------------------------------------- Step Functions deepening


class SfnJsonataMixed(_ServerlessCheck):
    """SFN030: JSONata-only keys used without QueryLanguage=JSONata."""

    id = "SFN030"
    title = "JSONata syntax under a JSONPath machine"
    why = (
        "Arguments/Output/Assign are JSONata-only keys - under the default "
        "JSONPath query language they are silently ignored or fail "
        "validation at deploy time."
    )
    when_ok = "JSONata keys only appear where QueryLanguage is JSONata."
    fix = "Set QueryLanguage to JSONata or rewrite the state with Parameters/ResultSelector."
    evidence_kind = EvidenceKind.STATIC

    _JSONATA_KEYS = frozenset({"Arguments", "Output", "Assign"})

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _sfn(ctx)
        results = []
        for machine in m.machines:
            for st in machine.states:
                if (
                    set(st.payload_keys) & self._JSONATA_KEYS
                    and st.query_language != "JSONata"
                    and machine.query_language != "JSONata"
                ):
                    results.append(
                        self.result(
                            Severity.WARNING,
                            f"{machine.name}.{st.name} uses JSONata keys "
                            f"({', '.join(sorted(set(st.payload_keys) & self._JSONATA_KEYS))}) "
                            "but the machine runs JSONPath",
                            file=machine.file,
                        )
                    )
        return results


class SfnDistributedMapUnguarded(_ServerlessCheck):
    """SFN031: Distributed Map with no retry/catch and 0 tolerated failure."""

    id = "SFN031"
    title = "Distributed Map without failure tolerance"
    why = (
        "A DISTRIBUTED Map with ToleratedFailurePercentage=0 and no "
        "Retry/Catch fails the whole execution on the first bad item."
    )
    when_ok = "Distributed Maps tolerate failures or declare Retry/Catch."
    fix = "Set ToleratedFailurePercentage or add Catch/Retry on the Map state."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        results = []
        for machine in _sfn(ctx).machines:
            for st in machine.states:
                if (
                    st.map_mode == "DISTRIBUTED"
                    and st.tolerated_failure <= 0
                    and st.retry_count == 0
                    and st.catch_count == 0
                ):
                    results.append(
                        self.result(
                            Severity.INFO,
                            f"{machine.name}.{st.name}: DISTRIBUTED Map with no "
                            "retry, catch, or tolerated-failure threshold",
                            file=machine.file,
                        )
                    )
        return results


class SfnPollingLoop(_ServerlessCheck):
    """SFN032: Wait->Choice->Task poll loop around a sync-capable call."""

    id = "SFN032"
    title = "Manual polling loop for a sync-capable integration"
    why = (
        "A Wait+Choice loop re-checking an AWS SDK call (e.g. athena "
        "getQueryExecution) duplicates what `.sync` does for free - billed "
        "state transitions for pure waiting."
    )
    when_ok = "Long-running service calls use `resource: ...sync`."
    fix = "Switch the Task resource to the `.sync` variant."
    evidence_kind = EvidenceKind.STATIC

    _POLLABLE = re.compile(
        r"(startQueryExecution|getQueryExecution|startJobRun|getJobRun"
        r"|startExecution|describeExecution|runTask|describeTasks)",
        re.IGNORECASE,
    )

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        results = []
        for machine in _sfn(ctx).machines:
            has_wait = any(s.type == "Wait" for s in machine.states)
            if not has_wait:
                continue
            starters = [
                s
                for s in machine.states
                if s.type == "Task"
                and re.search(r"start|run|submit", s.resource, re.IGNORECASE)
                and not s.integration.endswith("sync")
                and self._POLLABLE.search(s.resource)
            ]
            pollers = [
                s
                for s in machine.states
                if s.type == "Task"
                and re.search(r"get|describe", s.resource, re.IGNORECASE)
                and self._POLLABLE.search(s.resource)
            ]
            if starters and pollers:
                results.append(
                    self.result(
                        Severity.WARNING,
                        f"{machine.name}: Wait+poll loop around "
                        f"{starters[0].resource.rsplit(':', 2)[-1]} - "
                        "`.sync` replaces the loop",
                        file=machine.file,
                    )
                )
        return results


CHECKS: list[Check] = [
    AthenaUsage(),
    AthenaLegacyEngine(),
    AthenaNoResultLocation(),
    AthenaNoScanCutoff(),
    AthenaSqlNoWorkgroup(),
    AthenaClientSidePolling(),
    LambdaUsage(),
    LambdaEolRuntime(),
    LambdaAsyncNoDlq(),
    LambdaNoConcurrencyBound(),
    LambdaMaxTimeout(),
    LambdaVpcNoMemory(),
    SfnJsonataMixed(),
    SfnDistributedMapUnguarded(),
    SfnPollingLoop(),
]
