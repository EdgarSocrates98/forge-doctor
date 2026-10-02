"""Neptune semantic model - product-aware, evidence-only.

Separates Amazon Neptune Database (property graph via Gremlin/
openCypher, RDF via SPARQL) from Neptune Analytics (a distinct service
with built-in algorithms). Detection sources: Terraform
``aws_neptune_*``/``aws_neptune_graph`` resources, CloudFormation
``AWS::Neptune*`` types, boto3 ``neptune``/``neptunedata``/
``neptune-graph`` client bindings, endpoint strings (port 8182,
``*.neptune.amazonaws.com``), bulk-loader call sites, and the query
shapes collected by ``neptune_queries``. Nothing connects to AWS.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from forge_doctor.analyzers.hcl_lite import IaCResource, project_iac

if TYPE_CHECKING:
    from forge_doctor.analyzers.index import PyModuleIndex
    from forge_doctor.analyzers.terraform_model import TfBlock
    from forge_doctor.core.context import ProjectContext

_CACHE_ATTR = "_forge_doctor_neptune_model"

NEPTUNE_DATABASE = "database"
NEPTUNE_ANALYTICS = "analytics"
NEPTUNE_UNKNOWN = "unknown"

_NESTED_HEAD = re.compile(r"(\w+)\s*\{")
_NEPTUNE_HOST_RE = re.compile(
    r"([\w.-]+\.(?:neptune|neptune-analytics)\.[a-z0-9-]+\.amazonaws\.com)"
)
_PORT_RE = re.compile(r":(\d{2,5})\b")
_GremlinConn_RE = re.compile(r"DriverRemoteConnection\s*\(\s*[\"']([^\"']+)")
_LOADER_PATH_RE = re.compile(r"[\"'][^\"']*/loader\b")
_ALGO_NAME_RE = re.compile(
    r"\b(bfs|dfs|shortest_?paths?|page_?rank|degree_?centrality|closeness_?centrality|"
    r"connected_?components|community_?detection|label_?propagation|"
    r"node_?embedding|link_?prediction|common_?neighbors|similarity)\b",
    re.I,
)
_IAM_HINT_RE = re.compile(r"SigV4Auth|AWS4Auth|service\s*=\s*[\"']neptune-db|aws4auth", re.I)

_LOADER_KWARGS = {
    "source",
    "format",
    "iamRoleArn",
    "region",
    "failOnError",
    "parallelism",
    "updateSingleCardinalityProperties",
    "mode",
    "dependencies",
    "queueRequest",
    "parserConfiguration",
    "bulkLoadId",
}
_LANG_APIS = {
    "execute_gremlin_query": "gremlin",
    "execute_open_cypher_query": "opencypher",
    "execute_open_cypher_explain_query": "opencypher",
    "execute_sparql": "sparql",
}


@dataclass(frozen=True)
class NeptuneInstance:
    """One ``aws_neptune_cluster_instance``/``AWS::Neptune::DBInstance``."""

    name: str
    source: str
    file: Path
    line: int
    cluster_ref: str = ""
    instance_class: str = ""
    publicly_accessible: bool | None = None
    promotion_tier: int | None = None


@dataclass
class NeptuneCluster:
    """One Neptune Database cluster from any evidence plane."""

    name: str
    source: str  # terraform | cloudformation | code
    file: Path
    line: int
    engine_version: str = ""
    iam_auth: bool | None = None
    backup_retention: int | None = None
    preferred_backup_window: str = ""
    skip_final_snapshot: bool | None = None
    deletion_protection: bool | None = None
    storage_encrypted: bool | None = None
    subnet_group: str = ""
    security_groups: list[str] = field(default_factory=list)
    serverless: bool = False
    global_cluster: str = ""
    region: str = ""
    instances: list[NeptuneInstance] = field(default_factory=list)

    @property
    def replica_count(self) -> int:
        return max(0, len(self.instances) - 1)


@dataclass(frozen=True)
class NeptuneGlobalCluster:
    """``aws_neptune_global_cluster`` - one write region + read replicas."""

    name: str
    file: Path
    line: int
    source_cluster: str = ""


@dataclass(frozen=True)
class NeptuneBulkLoad:
    """One bulk-loader invocation or bulk-format artifact."""

    file: Path
    line: int
    origin: str  # api | http | file
    source_s3: str = ""
    format: str = ""
    iam_role: str = ""
    region: str = ""
    fail_on_error: bool | None = None
    parallelism: str = ""
    update_single_cardinality: bool | None = None
    dependencies: tuple[str, ...] = ()
    raw: str = ""


@dataclass(frozen=True)
class NeptuneEndpoint:
    """A Neptune endpoint observed in code (host/connection string)."""

    value: str
    file: Path
    line: int
    port: int = 0
    ssl: bool | None = None
    iam: bool = False
    kind: str = ""  # gremlin | sparql | loader | data | analytics


@dataclass(frozen=True)
class NeptuneAnalyticsCall:
    """Neptune Analytics API/algorithm usage observed in code."""

    file: Path
    line: int
    kind: str  # client | algorithm | resource
    detail: str = ""


@dataclass(frozen=True)
class ManualAlgorithm:
    """Hand-rolled graph algorithm in code (BFS/pagerank/...)."""

    name: str
    file: Path
    line: int


@dataclass
class NeptuneProjectModel:
    """All Neptune facts in a project; built once per scan."""

    product: str = NEPTUNE_UNKNOWN
    products: set[str] = field(default_factory=set)
    clusters: list[NeptuneCluster] = field(default_factory=list)
    subnet_groups: list[tuple[str, Path, int]] = field(default_factory=list)
    param_groups: list[tuple[str, str, Path, int]] = field(default_factory=list)
    global_clusters: list[NeptuneGlobalCluster] = field(default_factory=list)
    endpoints: list[NeptuneEndpoint] = field(default_factory=list)
    bulk_loads: list[NeptuneBulkLoad] = field(default_factory=list)
    analytics_calls: list[NeptuneAnalyticsCall] = field(default_factory=list)
    manual_algorithms: list[ManualAlgorithm] = field(default_factory=list)
    query_languages: set[str] = field(default_factory=set)
    iam_in_code: bool = False
    read_traversals: int = 0
    write_traversals: int = 0

    @property
    def has_neptune(self) -> bool:
        return bool(
            self.clusters
            or self.endpoints
            or self.bulk_loads
            or self.analytics_calls
            or self.query_languages
        )

    @property
    def instances(self) -> list[NeptuneInstance]:
        return [i for c in self.clusters for i in c.instances]


def _str_attr(attrs: dict[str, Any], *names: str) -> str:
    for n in names:
        v = attrs.get(n)
        if isinstance(v, str) and v:
            return v
    return ""


def _list_attr(attrs: dict[str, Any], name: str) -> list[str]:
    v = attrs.get(name)
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str) and v:
        return [v]
    return []


def _int_attr(attrs: dict[str, Any], name: str) -> int | None:
    v = attrs.get(name)
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.isdigit():
        return int(v)
    return None


def _tf_cluster(block: TfBlock) -> NeptuneCluster:
    attrs = block.attrs
    cluster = NeptuneCluster(
        name=_str_attr(attrs, "cluster_identifier") or block.labels[-1],
        source="terraform",
        file=block.file,
        line=block.line,
        engine_version=_str_attr(attrs, "engine_version"),
        iam_auth=(
            bool(attrs["iam_database_authentication_enabled"])
            if "iam_database_authentication_enabled" in attrs
            else None
        ),
        backup_retention=_int_attr(attrs, "backup_retention_period"),
        preferred_backup_window=_str_attr(attrs, "preferred_backup_window"),
        skip_final_snapshot=(
            bool(attrs["skip_final_snapshot"]) if "skip_final_snapshot" in attrs else None
        ),
        deletion_protection=(
            bool(attrs["deletion_protection"]) if "deletion_protection" in attrs else None
        ),
        storage_encrypted=(
            bool(attrs["storage_encrypted"]) if "storage_encrypted" in attrs else None
        ),
        subnet_group=_str_attr(attrs, "neptune_subnet_group_name"),
        security_groups=_list_attr(attrs, "vpc_security_group_ids"),
        global_cluster=_str_attr(attrs, "global_cluster_identifier"),
        region=_str_attr(attrs, "region", "availability_zones") and _str_attr(attrs, "region"),
    )
    for m in _NESTED_HEAD.finditer(block.body):
        if m.group(1) == "serverlessv2_scaling_configuration":
            cluster.serverless = True
    return cluster


def _tf_instance(block: TfBlock) -> NeptuneInstance:
    attrs = block.attrs
    return NeptuneInstance(
        name=_str_attr(attrs, "identifier") or block.labels[-1],
        source="terraform",
        file=block.file,
        line=block.line,
        cluster_ref=_str_attr(attrs, "cluster_identifier"),
        instance_class=_str_attr(attrs, "instance_class"),
        publicly_accessible=(
            bool(attrs["publicly_accessible"]) if "publicly_accessible" in attrs else None
        ),
        promotion_tier=_int_attr(attrs, "promotion_tier"),
    )


def _cfn_cluster(res: IaCResource) -> NeptuneCluster:
    a = res.attrs
    return NeptuneCluster(
        name=str(a.get("DBClusterIdentifier") or res.name),
        source="cloudformation",
        file=Path(res.file),
        line=res.line,
        engine_version=str(a.get("EngineVersion") or ""),
        iam_auth=(
            str(a.get("IamAuthEnabled", "")).lower() == "true" if "IamAuthEnabled" in a else None
        ),
        backup_retention=(
            int(a["BackupRetentionPeriod"])
            if str(a.get("BackupRetentionPeriod") or "").isdigit()
            else None
        ),
        preferred_backup_window=str(a.get("PreferredBackupWindow") or ""),
        storage_encrypted=(
            str(a.get("StorageEncrypted", "")).lower() == "true"
            if "StorageEncrypted" in a
            else None
        ),
        deletion_protection=(
            str(a.get("DeletionProtection", "")).lower() == "true"
            if "DeletionProtection" in a
            else None
        ),
        security_groups=[str(x) for x in a.get("VpcSecurityGroupIds") or []],
        global_cluster=str(a.get("GlobalClusterIdentifier") or ""),
    )


def _cfn_instance(res: IaCResource) -> NeptuneInstance:
    a = res.attrs
    return NeptuneInstance(
        name=str(a.get("DBInstanceIdentifier") or res.name),
        source="cloudformation",
        file=Path(res.file),
        line=res.line,
        cluster_ref=str(a.get("DBClusterIdentifier") or ""),
        instance_class=str(a.get("DBInstanceClass") or ""),
        publicly_accessible=(
            str(a.get("PubliclyAccessible", "")).lower() == "true"
            if "PubliclyAccessible" in a
            else None
        ),
    )


def _nep_bindings(module: PyModuleIndex) -> dict[str, str]:
    """var -> 'control'|'data'|'analytics' for boto3 neptune assigns."""
    bindings: dict[str, str] = {}
    if module.tree is None:
        return bindings
    for node in ast.walk(module.tree):
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
            continue
        call = node.value
        assert isinstance(call, ast.Call)
        dotted = _attr_dotted(call.func)
        lit0 = (
            call.args[0].value
            if call.args
            and isinstance(call.args[0], ast.Constant)
            and isinstance(call.args[0].value, str)
            else None
        )
        if not dotted.endswith((".client", ".resource")):
            continue
        kind = {"neptune": "control", "neptunedata": "data", "neptune-graph": "analytics"}.get(
            lit0 or ""
        )
        if kind is None:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                bindings[target.id] = kind
    return bindings


def _attr_dotted(node: ast.expr) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _kw_literal(node: ast.Call, name: str) -> str:
    for kw in node.keywords:
        if (
            kw.arg == name
            and isinstance(kw.value, ast.Constant)
            and isinstance(kw.value.value, str)
        ):
            return kw.value.value
        if kw.arg == name and isinstance(kw.value, ast.JoinedStr):
            prefix = "".join(
                v.value
                for v in kw.value.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
            )
            if prefix:
                return f"{prefix}..."
    return ""


def _kw_bool(node: ast.Call, name: str) -> bool | None:
    for kw in node.keywords:
        if (
            kw.arg == name
            and isinstance(kw.value, ast.Constant)
            and isinstance(kw.value.value, bool)
        ):
            return kw.value.value
    return None


def _kw_present(node: ast.Call, name: str) -> bool:
    return any(kw.arg == name for kw in node.keywords)


def _loader_calls(
    module: PyModuleIndex, bindings: dict[str, str], relative: Path, text: str
) -> tuple[list[NeptuneBulkLoad], set[str]]:
    """``start_loader_job``/``execute_*`` calls on bound data clients."""
    loads: list[NeptuneBulkLoad] = []
    langs: set[str] = set()
    if module.tree is None:
        return loads, langs
    for node in ast.walk(module.tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        recv = _attr_dotted(node.func.value)
        root = recv.split(".", 1)[0]
        kind = bindings.get(root)
        name = node.func.attr
        if name in _LANG_APIS and kind == "data":
            langs.add(_LANG_APIS[name])
        if name != "start_loader_job" or kind != "data":
            continue
        deps = _kw_literal(node, "dependencies") or ""
        raw = ""
        try:
            raw = ast.unparse(node)[:160]
        except Exception:  # pragma: no cover
            raw = ""
        loads.append(
            NeptuneBulkLoad(
                file=relative,
                line=node.lineno,
                origin="api",
                source_s3=_kw_literal(node, "source"),
                format=_kw_literal(node, "format"),
                iam_role=_kw_literal(node, "iamRoleArn"),
                region=_kw_literal(node, "region"),
                fail_on_error=_kw_bool(node, "failOnError"),
                parallelism=_kw_literal(node, "parallelism"),
                update_single_cardinality=_kw_bool(node, "updateSingleCardinalityProperties"),
                dependencies=tuple(d for d in deps.split(",") if d) if deps else (),
                raw=raw,
            )
        )
    # HTTP loader invocations (requests/curl against /loader).
    for m in _LOADER_PATH_RE.finditer(text):
        line = text[: m.start()].count("\n") + 1
        loads.append(NeptuneBulkLoad(file=relative, line=line, origin="http", raw=m.group(0)[:160]))
    return loads, langs


def _endpoints(relative: Path, text: str) -> list[NeptuneEndpoint]:
    out: list[NeptuneEndpoint] = []
    for m in _NEPTUNE_HOST_RE.finditer(text):
        line = text[: m.start()].count("\n") + 1
        tail = text[m.end() : m.end() + 40]
        port_m = _PORT_RE.search(tail)
        kind = "analytics" if "neptune-analytics" in m.group(1) else "data"
        if "/gremlin" in tail:
            kind = "gremlin"
        elif "/sparql" in tail:
            kind = "sparql"
        elif "/loader" in tail:
            kind = "loader"
        ssl = (
            "wss://" in text[max(0, m.start() - 12) : m.start()]
            or "https://" in text[max(0, m.start() - 12) : m.start()]
        )
        out.append(
            NeptuneEndpoint(
                value=m.group(1),
                file=relative,
                line=line,
                port=int(port_m.group(1)) if port_m else 0,
                ssl=ssl,
                kind=kind,
            )
        )
    for m in _GremlinConn_RE.finditer(text):
        line = text[: m.start()].count("\n") + 1
        url = m.group(1)
        port_m = _PORT_RE.search(url)
        out.append(
            NeptuneEndpoint(
                value=url,
                file=relative,
                line=line,
                port=int(port_m.group(1)) if port_m else 0,
                ssl=url.startswith("wss://") or url.startswith("https://"),
                kind="gremlin",
            )
        )
    return out


def neptune_model(ctx: ProjectContext) -> NeptuneProjectModel:
    """Build (once, memoized on ctx) the project's Neptune model."""
    cached = getattr(ctx, _CACHE_ATTR, None)
    if cached is not None:
        return cast(NeptuneProjectModel, cached)

    from forge_doctor.analyzers.index import project_index
    from forge_doctor.analyzers.neptune_queries import neptune_queries
    from forge_doctor.analyzers.terraform_model import terraform_model

    model = NeptuneProjectModel()
    tf = terraform_model(ctx)
    unattached: list[NeptuneInstance] = []

    # --- IaC plane -------------------------------------------------------
    for block in tf.resources:
        label = block.labels[0] if block.labels else ""
        if label == "aws_neptune_cluster":
            model.clusters.append(_tf_cluster(block))
        elif label == "aws_neptune_cluster_instance":
            unattached.append(_tf_instance(block))
        elif label == "aws_neptune_subnet_group":
            model.subnet_groups.append(
                (_str_attr(block.attrs, "name") or block.labels[-1], block.file, block.line)
            )
        elif label in {"aws_neptune_parameter_group", "aws_neptune_cluster_parameter_group"}:
            model.param_groups.append(
                (
                    _str_attr(block.attrs, "name") or block.labels[-1],
                    _str_attr(block.attrs, "family"),
                    block.file,
                    block.line,
                )
            )
        elif label == "aws_neptune_global_cluster":
            model.global_clusters.append(
                NeptuneGlobalCluster(
                    name=_str_attr(block.attrs, "global_cluster_identifier") or block.labels[-1],
                    file=block.file,
                    line=block.line,
                    source_cluster=_str_attr(block.attrs, "source_cluster_identifier"),
                )
            )
        elif label == "aws_neptune_graph":
            model.products.add(NEPTUNE_ANALYTICS)
            model.analytics_calls.append(
                NeptuneAnalyticsCall(
                    file=block.file,
                    line=block.line,
                    kind="resource",
                    detail=_str_attr(block.attrs, "graph_name") or block.labels[-1],
                )
            )
    for res in project_iac(ctx.files, ctx.root):
        if res.source != "cloudformation":
            continue
        if res.type == "AWS::Neptune::DBCluster":
            model.clusters.append(_cfn_cluster(res))
        elif res.type == "AWS::Neptune::DBInstance":
            unattached.append(_cfn_instance(res))
        elif res.type == "AWS::Neptune::DBSubnetGroup":
            model.subnet_groups.append((res.name, Path(res.file), res.line))
        elif res.type == "AWS::NeptuneGraph::Graph":
            model.products.add(NEPTUNE_ANALYTICS)
            model.analytics_calls.append(
                NeptuneAnalyticsCall(
                    file=Path(res.file), line=res.line, kind="resource", detail=res.name
                )
            )

    # Attach instances to clusters by ref/label; leftovers keep the fact.
    # Terraform refs look like ``aws_neptune_cluster.<label>.id``; CFN
    # refs carry the logical id or a ``Ref``/``GetAtt`` shape.
    cluster_keys: dict[str, NeptuneCluster] = {}
    for c in model.clusters:
        cluster_keys.setdefault(c.name, c)
    for block in tf.resources:
        if block.labels and block.labels[0] == "aws_neptune_cluster":
            cl = next(
                (x for x in model.clusters if x.file == block.file and x.line == block.line),
                None,
            )
            if cl is not None:
                cluster_keys[block.labels[-1]] = cl
    for inst in unattached:
        ref = inst.cluster_ref
        parts = ref.split(".")
        label = parts[1] if len(parts) >= 2 and parts[0] == "aws_neptune_cluster" else parts[-1]
        target = cluster_keys.get(label) or cluster_keys.get(ref)
        if target is None and len(model.clusters) == 1:
            target = model.clusters[0]
        if target is not None:
            target.instances.append(inst)
        else:
            orphan = NeptuneCluster(
                name=inst.cluster_ref or inst.name,
                source=inst.source,
                file=inst.file,
                line=inst.line,
            )
            orphan.instances.append(inst)
            model.clusters.append(orphan)
    if model.clusters:
        model.products.add(NEPTUNE_DATABASE)

    # --- code plane ------------------------------------------------------
    index = project_index(ctx)
    for relative, module in sorted(index.modules.items(), key=lambda kv: kv[0].as_posix()):
        text = ctx.read_text(relative) or ""
        bindings = _nep_bindings(module)
        if "data" in bindings.values():
            model.products.add(NEPTUNE_DATABASE)
        if "analytics" in bindings.values():
            model.products.add(NEPTUNE_ANALYTICS)
            model.analytics_calls.append(
                NeptuneAnalyticsCall(file=relative, line=1, kind="client", detail="neptune-graph")
            )
        loads, langs = _loader_calls(module, bindings, relative, text)
        model.bulk_loads.extend(loads)
        model.query_languages |= langs
        if loads or bindings:
            model.products.add(
                NEPTUNE_ANALYTICS if "analytics" in bindings.values() else NEPTUNE_DATABASE
            )
        for ep in _endpoints(relative, text):
            model.endpoints.append(ep)
            if ep.kind == "gremlin":
                model.query_languages.add("gremlin")
            elif ep.kind == "sparql":
                model.query_languages.add("sparql")
            model.products.add(NEPTUNE_ANALYTICS if ep.kind == "analytics" else NEPTUNE_DATABASE)
        if _IAM_HINT_RE.search(text):
            model.iam_in_code = True
        for m in _ALGO_NAME_RE.finditer(text):
            name = m.group(1).lower()
            # definitions only - calls into a named helper aren't "manual"
            line = text[: m.start()].count("\n") + 1
            seg = text[max(0, m.start() - 12) : m.start()]
            if re.search(r"def\s+$", seg):
                model.manual_algorithms.append(ManualAlgorithm(name, relative, line))

    # --- query plane (shared extraction) ----------------------------------
    report = neptune_queries(ctx)
    model.query_languages |= report.languages
    if report.queries:
        model.products.add(NEPTUNE_DATABASE)
    for q in report.queries:
        if not q.parsed:
            continue
        if q.writes:
            model.write_traversals += 1
        else:
            model.read_traversals += 1

    # Bulk-format CSVs detected by the graph model are loader artifacts.
    from forge_doctor.analyzers.graph_model import graph_model

    for w in graph_model(ctx).workloads:
        if w.kind == "bulk_load":
            model.bulk_loads.append(
                NeptuneBulkLoad(file=w.file, line=w.line, origin="file", format="csv")
            )

    if not model.products:
        model.product = NEPTUNE_UNKNOWN
    elif model.products == {NEPTUNE_ANALYTICS}:
        model.product = NEPTUNE_ANALYTICS
    else:
        model.product = NEPTUNE_DATABASE
    setattr(ctx, _CACHE_ATTR, model)
    return model
