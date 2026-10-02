"""EMR first-class model: EMR EC2, EMR Serverless, EMR on EKS.

Terraform ``aws_emr_*`` blocks (nested fleet/config/bootstrap/step blocks are
mined from the raw body), CFN ``AWS::EMR::*``, and boto3
``emr``/``emr-serverless``/``emr-containers`` call-sites. Offline only.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forge_doctor.analyzers.hcl_lite import _flat_attrs, project_iac
from forge_doctor.analyzers.index import project_index
from forge_doctor.analyzers.terraform_model import terraform_model
from forge_doctor.core.context import ProjectContext

_EMR_EC2 = {"aws_emr_cluster", "AWS::EMR::Cluster"}
_EMR_SERVERLESS = {"aws_emr_serverless_application", "AWS::EMRServerless::Application"}
_EMR_EKS = {"aws_emrcontainers_virtual_cluster", "AWS::EMRContainers::VirtualCluster"}
_EMR_STEP = {"aws_emr_step", "AWS::EMR::Step"}
_EMR_API = {
    "emr",
    "emr-serverless",
    "emr-containers",
    "elasticmapreduce",
}


@dataclass(frozen=True)
class EmrCluster:
    """One EMR-on-EC2 (classic) cluster."""

    name: str
    release: str
    file: Path
    line: int
    source: str = "terraform"
    master_type: str = ""
    core_type: str = ""
    core_count: int = 0
    has_fleets: bool = False
    spot_fleets: int = 0
    on_demand_fleets: int = 0
    autoscaling: bool = False
    dynamic_allocation: bool = False
    service_role: str = ""
    job_flow_role: str = ""
    log_uri: str = ""
    security_configuration: str = ""
    bootstrap_actions: int = 0
    kerberos: bool = False
    steps: tuple[str, ...] = ()
    steps_without_fail_action: int = 0


@dataclass(frozen=True)
class EmrServerlessApp:
    name: str
    release: str
    engine: str  # application type: spark|hive|...
    file: Path
    line: int
    source: str = "terraform"
    max_cpu: str = ""
    auto_stop: bool = False


@dataclass(frozen=True)
class EmrEksCluster:
    name: str
    eks_cluster: str
    file: Path
    line: int
    source: str = "terraform"
    release: str = ""  # virtual clusters pin no release; job runs do


@dataclass
class EmrProjectModel:
    clusters: list[EmrCluster] = field(default_factory=list)
    serverless_apps: list[EmrServerlessApp] = field(default_factory=list)
    eks_clusters: list[EmrEksCluster] = field(default_factory=list)
    releases: list[str] = field(default_factory=list)
    scaling_policies: int = 0
    steps_total: int = 0
    steps_no_fail_action: int = 0
    boto3_calls: list[str] = field(default_factory=list)  # emr api names seen
    has_emr: bool = False


def _sub_blocks(body: str) -> dict[str, list[dict[str, Any]]]:
    """``name { ... }`` sub-blocks -> flat attrs (one level deep)."""
    from forge_doctor.analyzers.hcl_lite import _brace_block

    out: dict[str, list[dict[str, Any]]] = {}
    for m in re.finditer(r"(\w+)\s*\{", body):
        head = body[max(0, m.start() - 1) : m.start()]
        if "=" in head:
            continue  # map literal, not a block
        sub, _end = _brace_block(body, m.end() - 1)
        out.setdefault(m.group(1), []).append(_flat_attrs(sub, 0))
    return out


def _role_short(role: Any) -> str:
    return str(role or "").rsplit("/", 1)[-1][:40]


def _int_attr(raw: Any) -> int:
    try:
        return int(str(raw or "0"))
    except ValueError:
        return 0


def _tf_cluster(b: Any) -> EmrCluster:
    attrs, body, labels = b.attrs, b.body, b.labels
    sub = _sub_blocks(body)
    fleets = sub.get("instance_fleet", [])
    groups = sub.get("instance_group", [])
    all_fleets = fleets + groups
    spot = sum(
        1
        for f in all_fleets
        if "spot" in str(f.get("instance_type_config") or "").lower()
        or str(f.get("market") or "").upper() == "SPOT"
    )
    steps = sub.get("step", [])
    no_fail = sum(1 for s in steps if not s.get("action_on_failure"))
    return EmrCluster(
        name=str(attrs.get("name") or (labels[-1] if labels else "")),
        release=str(attrs.get("release_label") or ""),
        file=Path(b.file),
        line=b.line,
        master_type=str(attrs.get("master_instance_type") or ""),
        core_type=str(attrs.get("core_instance_type") or ""),
        core_count=_int_attr(attrs.get("core_instance_count")),
        has_fleets=bool(all_fleets),
        spot_fleets=spot,
        on_demand_fleets=len(all_fleets) - spot,
        autoscaling=bool(re.search(r"autoscaling", body, re.IGNORECASE)),
        dynamic_allocation=bool(
            re.search(r"dynamicAllocation\.enabled[\"']?\s*[:=]\s*\"?true", body)
        ),
        service_role=_role_short(attrs.get("service_role")),
        job_flow_role=_role_short(attrs.get("job_flow_role") or attrs.get("job_flow_roles")),
        log_uri=str(attrs.get("log_uri") or ""),
        security_configuration=str(attrs.get("security_configuration") or ""),
        bootstrap_actions=len(sub.get("bootstrap_action", [])),
        kerberos=bool(sub.get("kerberos_attributes")),
        steps=tuple(str(s.get("name") or "?") for s in steps),
        steps_without_fail_action=no_fail,
    )


def _tf_serverless(b: Any) -> EmrServerlessApp:
    attrs = b.attrs
    return EmrServerlessApp(
        name=str(attrs.get("name") or (b.labels[-1] if b.labels else "")),
        release=str(attrs.get("release_label") or ""),
        engine=str(attrs.get("type") or "spark").lower(),
        file=Path(b.file),
        line=b.line,
        max_cpu=str(attrs.get("maximum_cpu") or ""),
        auto_stop=bool(attrs.get("auto_stop_configuration")) or "autoStop" in b.body,
    )


def _tf_eks(b: Any) -> EmrEksCluster:
    provider = (_sub_blocks(b.body).get("container_provider") or [{}])[0]
    return EmrEksCluster(
        name=str(b.attrs.get("name") or (b.labels[-1] if b.labels else "")),
        eks_cluster=str(
            provider.get("info") or provider.get("eks_cluster") or provider.get("id") or ""
        ),
        file=Path(b.file),
        line=b.line,
    )


def emr_model(ctx: ProjectContext) -> EmrProjectModel:
    """Fuse EMR evidence (TF/CFN/code) into one model."""
    model = EmrProjectModel()
    releases: set[str] = set()

    for b in terraform_model(ctx).resources:
        rtype = b.labels[0] if b.labels else ""
        if rtype == "aws_emr_cluster":
            cluster = _tf_cluster(b)
            model.clusters.append(cluster)
            if cluster.release:
                releases.add(cluster.release)
            model.steps_total += len(cluster.steps)
            model.steps_no_fail_action += cluster.steps_without_fail_action
        elif rtype in ("aws_emrserverless_application", "aws_emr_serverless_application"):
            app = _tf_serverless(b)
            model.serverless_apps.append(app)
            if app.release:
                releases.add(app.release)
        elif rtype == "aws_emrcontainers_virtual_cluster":
            model.eks_clusters.append(_tf_eks(b))
        elif rtype == "aws_emr_step":
            model.steps_total += 1
            if not b.attrs.get("action_on_failure"):
                model.steps_no_fail_action += 1
        elif rtype == "aws_emr_managed_scaling_policy":
            model.scaling_policies += 1

    # CloudFormation: AWS::EMR::* resources (flat Properties dicts).
    for res in project_iac(ctx.files, ctx.root):
        if res.source != "cloudformation":
            continue
        a = res.attrs
        file = Path(res.file)
        if res.type == "AWS::EMR::Cluster":
            release = str(a.get("ReleaseLabel") or "")
            model.clusters.append(
                EmrCluster(
                    name=str(a.get("Name") or res.name),
                    release=release,
                    file=file,
                    line=res.line,
                    source="cloudformation",
                    log_uri=str(a.get("LogUri") or ""),
                    security_configuration=str(a.get("SecurityConfiguration") or ""),
                    service_role=_role_short(a.get("ServiceRole")),
                    job_flow_role=_role_short(a.get("JobFlowRole")),
                )
            )
            if release:
                releases.add(release)
        elif res.type == "AWS::EMRServerless::Application":
            release = str(a.get("ReleaseLabel") or "")
            model.serverless_apps.append(
                EmrServerlessApp(
                    name=str(a.get("Name") or res.name),
                    release=release,
                    engine=str(a.get("Type") or "spark").lower(),
                    file=file,
                    line=res.line,
                    source="cloudformation",
                )
            )
            if release:
                releases.add(release)
        elif res.type == "AWS::EMRContainers::VirtualCluster":
            provider = a.get("ContainerProvider") or {}
            model.eks_clusters.append(
                EmrEksCluster(
                    name=str(a.get("Name") or res.name),
                    eks_cluster=str(provider.get("Id") or "") if isinstance(provider, dict) else "",
                    file=file,
                    line=res.line,
                    source="cloudformation",
                )
            )
        elif res.type == "AWS::EMR::Step":
            model.steps_total += 1
            if not a.get("ActionOnFailure"):
                model.steps_no_fail_action += 1

    # boto3: emr/emr-serverless/emr-containers client calls.
    index = project_index(ctx)
    calls: set[str] = set()
    for module in index.modules.values():
        bindings = _emr_bindings(module)
        if not bindings or module.tree is None:
            continue
        for node in ast.walk(module.tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id in bindings
            ):
                calls.add(f"{bindings[func.value.id]}.{func.attr}")

    model.releases = sorted(releases)
    model.boto3_calls = sorted(calls)
    model.has_emr = bool(
        model.clusters or model.serverless_apps or model.eks_clusters or model.boto3_calls
    )
    return model


def _emr_bindings(module: Any) -> dict[str, str]:
    """var -> 'emr'|'emr-serverless'|'emr-containers' for boto3 assigns."""
    out: dict[str, str] = {}
    if module.tree is None:
        return out
    for node in ast.walk(module.tree):
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
            continue
        call = node.value
        dotted = _attr_dotted(call.func)
        if not dotted.endswith(".client"):
            continue
        lit0 = (
            call.args[0].value
            if call.args
            and isinstance(call.args[0], ast.Constant)
            and isinstance(call.args[0].value, str)
            else None
        )
        if lit0 in _EMR_API:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = "emr" if lit0 == "elasticmapreduce" else lit0
    return out


def _attr_dotted(node: Any) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))
