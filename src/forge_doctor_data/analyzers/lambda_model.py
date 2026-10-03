"""Lambda deep-intelligence model.

Fuses Terraform ``aws_lambda_*`` resources, CloudFormation
``AWS::Lambda::*`` resources, and boto3 ``lambda`` call-sites into one
deterministic, offline-only project model. No AWS calls.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from dataclasses import replace as dc_replace
from pathlib import Path
from typing import Any

from forge_doctor_data.analyzers.hcl_lite import project_iac
from forge_doctor_data.analyzers.index import project_index
from forge_doctor_data.analyzers.terraform_model import terraform_model
from forge_doctor_data.core.context import ProjectContext

_EOL_RUNTIMES = {
    "python2.7",
    "python3.6",
    "python3.7",
    "python3.8",
    "nodejs10.x",
    "nodejs12.x",
    "nodejs14.x",
    "nodejs16.x",
    "dotnetcore3.1",
    "dotnet6",
    "ruby2.5",
    "ruby2.7",
    "java8",
    "go1.x",
    "provided",
}


@dataclass(frozen=True)
class LambdaFunction:
    name: str
    file: Path
    line: int
    source: str = "terraform"
    runtime: str = ""
    architectures: tuple[str, ...] = ()
    memory_mb: int = 0
    timeout_s: int = 0
    ephemeral_mb: int = 0
    handler: str = ""
    reserved_concurrency: int = -1  # -1 = unset
    provisioned_concurrency: bool = False
    layers: int = 0
    vpc: bool = False
    dlq: bool = False
    tracing: str = ""
    package_type: str = "Zip"
    tf_label: str = ""  # terraform resource label (for ref matching)

    def matches(self, name_or_ref: str) -> bool:
        """``function_name`` attr, TF label, or CFN logical id."""
        return bool(name_or_ref) and name_or_ref in {self.name, self.tf_label}


@dataclass(frozen=True)
class LambdaEventSource:
    """One trigger feeding a function."""

    kind: str  # stream|sqs|sns|s3|schedule|apigw|ddb|kinesis|kafka|other
    function: str
    file: Path
    line: int
    source: str = "terraform"
    detail: str = ""  # arn/topic/schedule tail


@dataclass(frozen=True)
class LambdaDestination:
    function: str
    on_success: str
    on_failure: str
    file: Path
    line: int


@dataclass
class LambdaProjectModel:
    """Everything the project evidences about Lambda, in sorted order."""

    functions: list[LambdaFunction] = field(default_factory=list)
    event_sources: list[LambdaEventSource] = field(default_factory=list)
    destinations: list[LambdaDestination] = field(default_factory=list)
    layer_versions: list[str] = field(default_factory=list)
    boto3_calls: list[str] = field(default_factory=list)  # lambda api names seen
    idempotency_evidence: list[tuple[str, int]] = field(default_factory=list)
    has_lambda: bool = False

    @property
    def function_names(self) -> set[str]:
        return {f.name for f in self.functions}

    def functions_with(self, pred: Any) -> list[LambdaFunction]:
        return [f for f in self.functions if pred(f)]


_AWS_SRC_KINDS = {
    "aws_lambda_event_source_mapping": "stream",  # kinesis/ddb/sqs/msk/kafka
    "aws_s3_bucket_notification": "s3",
    "aws_sns_topic_subscription": "sns",
    "aws_cloudwatch_event_target": "schedule",
    "aws_scheduler_schedule": "schedule",
    "aws_lambda_permission": "other",
}


def _int(raw: Any, default: int = 0) -> int:
    if raw is None or raw == "":
        return default
    try:
        return int(str(raw))
    except ValueError:
        return default


def _tf_ref(raw: str) -> str:
    """`aws_lambda_function.fn.attr` -> `fn`; literals pass through."""
    parts = str(raw or "").split(".")
    if len(parts) >= 3 and parts[0].startswith(("aws_", "databricks_", "module.")):
        return parts[1]
    return str(raw or "")


def _tf_function(b: Any) -> LambdaFunction:
    attrs, body = b.attrs, b.body
    archs = attrs.get("architectures")
    if isinstance(archs, str):
        archs = (archs.strip("[]").strip(),)
    elif isinstance(archs, list):
        archs = tuple(str(a) for a in archs)
    else:
        archs = ()
    eph = re.search(r"ephemeral_storage\s*\{[^}]*size\s*=\s*(\d+)", body, re.DOTALL)
    return LambdaFunction(
        name=str(
            attrs.get("function_name") or attrs.get("name") or (b.labels[-1] if b.labels else "")
        ),
        file=Path(b.file),
        line=b.line,
        runtime=str(attrs.get("runtime") or ""),
        architectures=tuple(a for a in archs if a),
        memory_mb=_int(attrs.get("memory_size")),
        timeout_s=_int(attrs.get("timeout")),
        ephemeral_mb=int(eph.group(1)) if eph else 0,
        handler=str(attrs.get("handler") or ""),
        reserved_concurrency=_int(attrs.get("reserved_concurrent_executions"), -1),
        layers=len(str(attrs.get("layers") or "").split(",")) if attrs.get("layers") else 0,
        vpc=bool(re.search(r"vpc_config\s*\{", body)),
        dlq=bool(re.search(r"dead_letter_config\s*\{|dead_letter_arn", body)),
        tracing=str(attrs.get("tracing_mode") or ("Active" if "tracing_config" in body else "")),
        package_type=str(attrs.get("package_type") or "Zip"),
        tf_label=b.labels[-1] if b.labels else "",
    )


_EVENT_SRC_HINT = re.compile(
    r"(kinesis|dynamodb|sqs|kafka|msk|amazonmq|documentdb|self_managed)", re.IGNORECASE
)


def _event_kind(res_type: str, attrs: dict[str, Any]) -> str:
    if res_type == "aws_lambda_event_source_mapping":
        src = str(attrs.get("event_source_arn") or "")
        m = _EVENT_SRC_HINT.search(src)
        return m.group(1).lower() if m else "stream"
    return _AWS_SRC_KINDS.get(res_type, "other")


def lambda_model(ctx: ProjectContext) -> LambdaProjectModel:
    """Fuse Lambda evidence (TF/CFN/code) into one model."""
    model = LambdaProjectModel()
    tf = terraform_model(ctx)

    prov_conc: set[str] = set()
    for b in tf.resources:
        rtype = b.labels[0] if b.labels else ""
        file = Path(b.file)
        if rtype == "aws_lambda_function":
            fn = _tf_function(b)
            model.functions.append(fn)
        elif rtype in _AWS_SRC_KINDS:
            fn_ref = _tf_ref(
                str(
                    b.attrs.get("function_name")
                    or b.attrs.get("function")
                    or b.attrs.get("target_id")
                    or ""
                )
            )
            model.event_sources.append(
                LambdaEventSource(
                    kind=_event_kind(rtype, b.attrs),
                    function=fn_ref,
                    file=file,
                    line=b.line,
                    detail=str(
                        b.attrs.get("event_source_arn")
                        or b.attrs.get("schedule_expression")
                        or b.attrs.get("topic_arn")
                        or ""
                    )[:120],
                )
            )
        elif rtype == "aws_lambda_function_event_invoke_config":
            dest = b.body
            succ = re.search(r"on_success\s*\{[^}]*destination\s*=\s*\"([^\"]+)", dest)
            fail = re.search(r"on_failure\s*\{[^}]*destination\s*=\s*\"([^\"]+)", dest)
            model.destinations.append(
                LambdaDestination(
                    function=_tf_ref(str(b.attrs.get("function_name") or ""))
                    or (b.labels[-1] if b.labels else ""),
                    on_success=succ.group(1) if succ else "",
                    on_failure=fail.group(1) if fail else "",
                    file=file,
                    line=b.line,
                )
            )
        elif rtype == "aws_lambda_provisioned_concurrency_config":
            prov_conc.add(_tf_ref(str(b.attrs.get("function_name") or "")))
        elif rtype == "aws_lambda_layer_version":
            model.layer_versions.append(
                str(b.attrs.get("layer_name") or (b.labels[-1] if b.labels else ""))
            )

    if prov_conc:
        model.functions = [
            (
                dc_replace(fn, provisioned_concurrency=True)
                if fn.name in prov_conc or fn.name.split(".")[-1] in prov_conc
                else fn
            )
            for fn in model.functions
        ]

    for res in project_iac(ctx.files, ctx.root):
        if res.source != "cloudformation":
            continue
        a, file = res.attrs, Path(res.file)
        if res.type == "AWS::Lambda::Function":
            vpc = a.get("VpcConfig")
            dlq = a.get("DeadLetterConfig")
            model.functions.append(
                LambdaFunction(
                    name=str(a.get("FunctionName") or res.name),
                    file=file,
                    line=res.line,
                    source="cloudformation",
                    runtime=str(a.get("Runtime") or ""),
                    memory_mb=_int(a.get("MemorySize")),
                    timeout_s=_int(a.get("Timeout")),
                    ephemeral_mb=_int(
                        a.get("EphemeralStorage", {}).get("Size")
                        if isinstance(a.get("EphemeralStorage"), dict)
                        else 0
                    ),
                    handler=str(a.get("Handler") or ""),
                    reserved_concurrency=_int(a.get("ReservedConcurrentExecutions"), -1),
                    layers=len(a.get("Layers") or []) if isinstance(a.get("Layers"), list) else 0,
                    vpc=bool(vpc),
                    dlq=bool(dlq),
                    package_type=str(a.get("PackageType") or "Zip"),
                    tf_label=res.name,  # CFN logical id for !Ref matching
                )
            )
        elif res.type == "AWS::Lambda::EventSourceMapping":
            model.event_sources.append(
                LambdaEventSource(
                    kind=_event_kind("aws_lambda_event_source_mapping", a),
                    function=str(a.get("FunctionName") or ""),
                    file=file,
                    line=res.line,
                    source="cloudformation",
                    detail=str(a.get("EventSourceArn") or "")[:120],
                )
            )
        elif res.type == "AWS::Lambda::LayerVersion":
            model.layer_versions.append(str(a.get("LayerName") or res.name))
        elif res.type == "AWS::Lambda::EventInvokeConfig":
            dests = a.get("DestinationConfig") or {}
            succ_arn = fail_arn = ""
            if isinstance(dests, dict):
                on_s = dests.get("OnSuccess") or {}
                on_f = dests.get("OnFailure") or {}
                if isinstance(on_s, dict):
                    succ_arn = str(
                        (on_s.get("Destination") or {}).get("Arn") or on_s.get("Destination") or ""
                    )
                if isinstance(on_f, dict):
                    fail_arn = str(
                        (on_f.get("Destination") or {}).get("Arn") or on_f.get("Destination") or ""
                    )
            model.destinations.append(
                LambdaDestination(
                    function=str(a.get("FunctionName") or res.name),
                    on_success=succ_arn,
                    on_failure=fail_arn,
                    file=file,
                    line=res.line,
                )
            )

    # boto3 lambda call-sites + idempotency evidence in handler code.
    index = project_index(ctx)
    calls: set[str] = set()
    for module in index.modules.values():
        bound = _lambda_bindings(module)
        if module.tree is not None:
            for node in ast.walk(module.tree):
                if isinstance(node, ast.Call):
                    dotted = _attr_dotted(node.func)
                    if dotted.split(".")[0] in bound:
                        calls.add(f"lambda.{dotted.rsplit('.', 1)[-1]}")
        # idempotency evidence: powertools idempotency decorators/imports,
        # explicit dedup keys in handler code.
        for imp in module.imports:
            if "idempotency" in imp.module.lower() or "powertools" in imp.module.lower():
                model.idempotency_evidence.append((module.file.as_posix(), imp.line))

    model.boto3_calls = sorted(calls)
    model.functions.sort(key=lambda f: (f.file.as_posix(), f.line, f.name))
    model.event_sources.sort(key=lambda e: (e.file.as_posix(), e.line, e.kind))
    model.layer_versions.sort()
    model.has_lambda = bool(
        model.functions or model.event_sources or model.boto3_calls or model.layer_versions
    )
    return model


def runtime_family(runtime: str) -> str:
    """'python3.12' -> 'python', 'nodejs20.x' -> 'nodejs', '' -> ''."""
    m = re.match(r"([a-z]+)", runtime or "")
    return m.group(1) if m else ""


def runtime_eol(runtime: str) -> bool:
    return (runtime or "").lower() in _EOL_RUNTIMES


def _attr_dotted(node: Any) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _lambda_bindings(module: Any) -> set[str]:
    """var names bound to ``boto3.client('lambda')``."""
    out: set[str] = set()
    if module.tree is None:
        return out
    for node in ast.walk(module.tree):
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
            continue
        call = node.value
        if not _attr_dotted(call.func).endswith(".client"):
            continue
        lit0 = (
            call.args[0].value
            if call.args
            and isinstance(call.args[0], ast.Constant)
            and isinstance(call.args[0].value, str)
            else None
        )
        if lit0 == "lambda":
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out.add(target.id)
    return out
