"""DynamoDB semantic model - access-pattern-first, evidence-only.

Tables come from Terraform (``aws_dynamodb_table`` incl. nested
gsi/lsi/replica/ttl blocks), CloudFormation (``AWS::DynamoDB::*``), and
boto3 code bindings. Access operations are extracted from the module AST
so keyword arguments are visible regardless of value type
(``FilterExpression`` presence matters even when the value is dynamic).
Nothing connects to AWS; everything is static/config evidence.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from forge_doctor_data.analyzers.hcl_lite import (
    IaCResource,
    _brace_block,
    _flat_attrs,
    project_iac,
)

if TYPE_CHECKING:
    from forge_doctor_data.analyzers.index import PyModuleIndex
    from forge_doctor_data.analyzers.terraform_model import TfBlock
    from forge_doctor_data.core.context import ProjectContext

_CACHE_ATTR = "_forge_doctor_data_dynamodb_model"

_OPS = {
    "get_item",
    "put_item",
    "update_item",
    "delete_item",
    "query",
    "scan",
    "batch_get_item",
    "batch_write_item",
    "transact_get_items",
    "transact_write_items",
}
_WRITE_OPS = {"put_item", "update_item", "delete_item", "batch_write_item", "transact_write_items"}

_DDB_CLIENT_ARG = "dynamodb"
_TABLE_FACTORY_RE = re.compile(r"(?:^|\.)(dynamodb\.)?Table$")
_ENTITY_PREFIX_RE = re.compile(r"[fF]?['\"]([A-Z][A-Z0-9_]{1,30})#")
_NESTED_HEAD = re.compile(r"(\w+)\s*\{")


@dataclass(frozen=True)
class DynamoIndex:
    """A GSI/LSI declared on a table."""

    name: str
    kind: str  # gsi | lsi
    partition_key: str
    sort_key: str | None
    projection: str
    file: Path
    line: int


@dataclass
class DynamoTable:
    """One DynamoDB table from any evidence plane."""

    name: str
    source: str  # terraform | cloudformation | code
    file: Path
    line: int
    partition_key: str | None = None
    sort_key: str | None = None
    billing_mode: str = ""  # PAY_PER_REQUEST | PROVISIONED | ""
    encrypted: bool | None = None
    pitr: bool | None = None
    stream_view_type: str = ""  # non-empty when streams enabled
    ttl_attribute: str = ""
    indexes: list[DynamoIndex] = field(default_factory=list)
    replicas: list[str] = field(default_factory=list)
    global_mode: str = ""  # mrec | mrsc | unknown | "" (not global)

    @property
    def is_global(self) -> bool:
        return bool(self.global_mode)


@dataclass(frozen=True)
class DynamoAccess:
    """One observed DynamoDB operation call site."""

    op: str  # get_item | query | scan | transact_write_items | ...
    file: Path
    line: int
    symbol: str | None = None
    table: str | None = None  # resolved name when statically known
    index: str | None = None  # IndexName kwarg when literal
    has_key_condition: bool = False
    has_projection: bool = False
    has_filter: bool = False
    consistent: bool | None = None  # ConsistentRead literal, if given
    # Key dict contents: ("lit", "USER#1") constants / ("var", "uid") dynamic
    # / ("pattern", "USER#") f-string prefixes - for pk/sk heuristics.
    key_literals: tuple[tuple[str, str], ...] = ()
    raw: str = ""


@dataclass
class DynamoStreamModel:
    """Stream config on a table plus detected consumers."""

    table: str
    view_type: str
    file: Path
    line: int
    consumers: list[tuple[str, Path, int]] = field(default_factory=list)  # (kind, file, line)
    idempotency_signal: bool = False  # ReportBatchItemFailures / batchItemFailures


@dataclass
class DynamoGlobalTableModel:
    """Global-table evidence: mode + region set."""

    table: str
    mode: str  # mrec | mrsc | unknown
    regions: list[str] = field(default_factory=list)
    file: Path = Path(".")
    line: int = 0


@dataclass
class SingleTableEntityModel:
    """Entity types derived from ``PREFIX#{id}`` key conventions."""

    entities: dict[str, list[tuple[Path, int]]] = field(default_factory=dict)


@dataclass
class DynamoDBProjectModel:
    """All DynamoDB facts in a project; built once per scan."""

    tables: list[DynamoTable] = field(default_factory=list)
    accesses: list[DynamoAccess] = field(default_factory=list)
    streams: list[DynamoStreamModel] = field(default_factory=list)
    global_tables: list[DynamoGlobalTableModel] = field(default_factory=list)
    single_table: SingleTableEntityModel = field(default_factory=SingleTableEntityModel)

    @property
    def has_dynamodb(self) -> bool:
        return bool(self.tables or self.accesses or self.streams)

    @property
    def transactions_used(self) -> bool:
        return any(a.op.startswith("transact_") for a in self.accesses)


def _str_attr(attrs: dict[str, Any], *names: str) -> str:
    for n in names:
        v = attrs.get(n)
        if isinstance(v, str) and v:
            return v
    return ""


def _tf_dynamodb_table(
    block: TfBlock,
) -> tuple[DynamoTable, DynamoStreamModel | None]:
    """One ``aws_dynamodb_table`` block -> table (+stream if enabled)."""
    attrs = block.attrs
    table = DynamoTable(
        name=_str_attr(attrs, "name") or block.labels[-1],
        source="terraform",
        file=block.file,
        line=block.line,
        partition_key=_str_attr(attrs, "hash_key") or None,
        sort_key=_str_attr(attrs, "range_key") or None,
        billing_mode=_str_attr(attrs, "billing_mode"),
    )
    if attrs.get("stream_enabled") is True:
        table.stream_view_type = _str_attr(attrs, "stream_view_type") or "UNKNOWN"
    stream: DynamoStreamModel | None = None
    mrsc = False
    for m in _NESTED_HEAD.finditer(block.body):
        kind = m.group(1)
        body, _ = _brace_block(block.body, m.end() - 1)
        flat = _flat_attrs(body, block.line)
        bline = block.line + block.body[: m.start()].count("\n")
        if kind == "global_secondary_index":
            table.indexes.append(
                DynamoIndex(
                    name=_str_attr(flat, "name"),
                    kind="gsi",
                    partition_key=_str_attr(flat, "hash_key"),
                    sort_key=_str_attr(flat, "range_key") or None,
                    projection=_str_attr(flat, "projection_type"),
                    file=block.file,
                    line=bline,
                )
            )
        elif kind == "local_secondary_index":
            table.indexes.append(
                DynamoIndex(
                    name=_str_attr(flat, "name"),
                    kind="lsi",
                    partition_key=table.partition_key or "",
                    sort_key=_str_attr(flat, "range_key") or None,
                    projection=_str_attr(flat, "projection_type"),
                    file=block.file,
                    line=bline,
                )
            )
        elif kind in {"replica", "global_table_witness"}:
            region = _str_attr(flat, "region_name", "region") or "unknown"
            table.replicas.append(region)
            if kind == "global_table_witness":
                mrsc = True
        elif kind == "ttl":
            if flat.get("enabled") is not False:
                table.ttl_attribute = _str_attr(flat, "attribute_name") or "ttl"
        elif kind == "server_side_encryption":
            table.encrypted = flat.get("enabled") is not False
        elif kind == "point_in_time_recovery":
            table.pitr = flat.get("enabled") is True
    if table.replicas:
        table.global_mode = "mrsc" if mrsc else "mrec"  # MREC is the default
    if table.stream_view_type:
        stream = DynamoStreamModel(
            table=table.name,
            view_type=table.stream_view_type,
            file=block.file,
            line=block.line,
        )
    return table, stream


def _cfn_dynamodb(
    res: IaCResource,
) -> tuple[DynamoTable, DynamoStreamModel | None]:
    """``AWS::DynamoDB::Table``/``GlobalTable`` -> table (+stream)."""
    a = res.attrs
    table = DynamoTable(
        name=str(a.get("TableName") or res.name),
        source="cloudformation",
        file=Path(res.file),
        line=res.line,
        billing_mode=str(a.get("BillingMode") or ""),
    )
    for key in a.get("KeySchema") or []:
        if not isinstance(key, dict):
            continue
        if key.get("KeyType") == "HASH":
            table.partition_key = str(key.get("AttributeName") or "")
        elif key.get("KeyType") == "RANGE":
            table.sort_key = str(key.get("AttributeName") or "")
    for gsi in a.get("GlobalSecondaryIndexes") or []:
        if not isinstance(gsi, dict):
            continue
        pk, sk = "", None
        for key in gsi.get("KeySchema") or []:
            if not isinstance(key, dict):
                continue
            if key.get("KeyType") == "HASH":
                pk = str(key.get("AttributeName") or "")
            elif key.get("KeyType") == "RANGE":
                sk = str(key.get("AttributeName") or "")
        proj = gsi.get("Projection") or {}
        table.indexes.append(
            DynamoIndex(
                name=str(gsi.get("IndexName") or ""),
                kind="gsi",
                partition_key=pk,
                sort_key=sk,
                projection=str(proj.get("ProjectionType") or ""),
                file=Path(res.file),
                line=res.line,
            )
        )
    for lsi in a.get("LocalSecondaryIndexes") or []:
        if not isinstance(lsi, dict):
            continue
        sk = None
        for key in lsi.get("KeySchema") or []:
            if isinstance(key, dict) and key.get("KeyType") == "RANGE":
                sk = str(key.get("AttributeName") or "")
        table.indexes.append(
            DynamoIndex(
                name=str(lsi.get("IndexName") or ""),
                kind="lsi",
                partition_key=table.partition_key or "",
                sort_key=sk,
                projection="",
                file=Path(res.file),
                line=res.line,
            )
        )
    stream: DynamoStreamModel | None = None
    spec = a.get("StreamSpecification")
    if isinstance(spec, dict) and spec.get("StreamViewType"):
        table.stream_view_type = str(spec.get("StreamViewType"))
        stream = DynamoStreamModel(
            table=table.name,
            view_type=table.stream_view_type,
            file=Path(res.file),
            line=res.line,
        )
    ttl = a.get("TimeToLiveSpecification")
    if isinstance(ttl, dict) and str(ttl.get("Enabled", "")).lower() != "false":
        table.ttl_attribute = str(ttl.get("AttributeName") or "ttl")
    sse = a.get("SSESpecification")
    if isinstance(sse, dict):
        table.encrypted = str(sse.get("SSEEnabled", "")).lower() != "false"
    pitr = a.get("PointInTimeRecoverySpecification")
    if isinstance(pitr, dict):
        table.pitr = str(pitr.get("PointInTimeRecoveryEnabled", "")).lower() == "true"
    # AWS::DynamoDB::GlobalTable replicas + MRSC consistency signal.
    replicas = a.get("Replicas")
    if isinstance(replicas, list) and replicas:
        for r in replicas:
            table.replicas.append(str(r.get("Region")) if isinstance(r, dict) else str(r))
        strong = str(a.get("MultiRegionConsistency") or "")
        table.global_mode = "mrsc" if strong.upper() == "STRONG" else "mrec"
    return table, stream


def _ddb_bindings(module: PyModuleIndex) -> dict[str, str]:
    """var -> 'client'|'resource'|'table:<name>' for boto3/dynamodb assigns."""
    bindings: dict[str, str] = {}
    if module.tree is None:
        return bindings
    assigns = [
        n
        for n in ast.walk(module.tree)
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
    ]
    # Pass 1: clients/resources bound to the dynamodb service.
    for node in assigns:
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
        if lit0 != _DDB_CLIENT_ARG:
            continue
        if not dotted.endswith((".client", ".resource")):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                bindings[target.id] = "client" if dotted.endswith("client") else "resource"
    # Pass 2: Table factories on bound resources or explicit dynamodb paths.
    for node in assigns:
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
        if lit0 is None or not _TABLE_FACTORY_RE.search(dotted):
            continue
        recv = dotted.rsplit(".", 1)[0]
        if recv not in bindings and "dynamodb" not in dotted:
            continue  # ``x.Table`` on an unproven receiver stays silent
        for target in node.targets:
            if isinstance(target, ast.Name):
                bindings[target.id] = f"table:{lit0}"
    return bindings


def _attr_dotted(node: ast.expr) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _key_facts(node: ast.expr) -> list[tuple[str, str]]:
    """Literal/variable facts inside a Key-style dict or f-string."""
    facts: list[tuple[str, str]] = []
    if isinstance(node, ast.Dict):
        for key, value in zip(node.keys, node.values, strict=True):
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                facts.append(("key", key.value))
            facts.extend(_key_facts(value))
    elif isinstance(node, ast.Constant) and isinstance(node.value, str):
        facts.append(("lit", node.value))
    elif isinstance(node, ast.JoinedStr):
        prefix = "".join(
            v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)
        )
        if prefix:
            facts.append(("pattern", prefix))
        for v in node.values:
            if isinstance(v, ast.FormattedValue):
                name = _attr_dotted(v.value)
                if name:
                    facts.append(("var", name))
    elif isinstance(node, (ast.Name, ast.Attribute)):
        name = _attr_dotted(node)
        if name:
            facts.append(("var", name))
    return facts


def _enclosing_symbol(module: PyModuleIndex, line: int) -> str | None:
    """Smallest function containing ``line``, best effort."""
    best: tuple[int, str] | None = None
    for qualified, info in module.functions.items():
        if info.line <= line <= info.end_line:
            span = info.end_line - info.line
            if best is None or span < best[0]:
                best = (span, qualified)
    return best[1] if best else None


def _ddb_ops(module: PyModuleIndex, bindings: dict[str, str], relative: Path) -> list[DynamoAccess]:
    """Extract DynamoDB op call sites from the module AST."""
    if module.tree is None or not bindings:
        return []
    out: list[DynamoAccess] = []
    for node in ast.walk(module.tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        op = node.func.attr
        if op not in _OPS:
            continue
        recv = _attr_dotted(node.func.value)
        root = recv.split(".", 1)[0]
        bound = bindings.get(root)
        kw_names = {kw.arg for kw in node.keywords if kw.arg}
        table_name = next(
            (
                kw.value.value
                for kw in node.keywords
                if kw.arg == "TableName"
                and isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, str)
            ),
            None,
        )
        index_name = next(
            (
                kw.value.value
                for kw in node.keywords
                if kw.arg == "IndexName"
                and isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, str)
            ),
            None,
        )
        if bound is None and table_name is None:
            continue  # not provably dynamodb - stay silent
        table = table_name or (bound[6:] if bound and bound.startswith("table:") else None)
        consistent = next(
            (
                kw.value.value
                for kw in node.keywords
                if kw.arg == "ConsistentRead" and isinstance(kw.value, ast.Constant)
            ),
            None,
        )
        key_facts: list[tuple[str, str]] = []
        for kw in node.keywords:
            if kw.arg in {"Key", "KeyConditionExpression", "Item"}:
                key_facts.extend(_key_facts(kw.value))
        raw = ""
        try:
            raw = ast.unparse(node)[:160]
        except Exception:  # pragma: no cover - ast.unparse is total on py>=3.9
            raw = ""
        out.append(
            DynamoAccess(
                op=op,
                file=relative,
                line=node.lineno,
                symbol=_enclosing_symbol(module, node.lineno),
                table=table,
                index=index_name,
                has_key_condition="KeyConditionExpression" in kw_names or "Key" in kw_names,
                has_projection="ProjectionExpression" in kw_names,
                has_filter="FilterExpression" in kw_names,
                consistent=bool(consistent) if consistent is not None else None,
                key_literals=tuple(key_facts),
                raw=raw,
            )
        )
    return out


def _tf_stream_consumers(
    blocks: list[TfBlock],
) -> list[tuple[str, Path, int, bool]]:
    """Lambda event-source mappings referencing a dynamodb stream."""
    out = []
    for b in blocks:
        if not b.labels or b.labels[0] != "aws_lambda_event_source_mapping":
            continue
        arn = str(b.attrs.get("event_source_arn") or "")
        if "stream" not in arn and "dynamodb" not in arn:
            continue
        resp = b.attrs.get("function_response_types") or []
        idempotent = any("ReportBatchItemFailures" in str(r) for r in resp)
        out.append(("lambda", b.file, b.line, idempotent))
    return out


def dynamodb_model(ctx: ProjectContext) -> DynamoDBProjectModel:
    """Build (once, memoized on ctx) the project's DynamoDB model."""
    cached = getattr(ctx, _CACHE_ATTR, None)
    if cached is not None:
        return cast(DynamoDBProjectModel, cached)

    from forge_doctor_data.analyzers.index import project_index
    from forge_doctor_data.analyzers.terraform_model import terraform_model

    model = DynamoDBProjectModel()
    stream_by_table: dict[str, DynamoStreamModel] = {}

    # --- IaC plane: terraform blocks + cloudformation resources ----------
    tf = terraform_model(ctx)
    for block in tf.resources:
        if block.labels and block.labels[0] == "aws_dynamodb_table":
            table, stream = _tf_dynamodb_table(block)
            model.tables.append(table)
            if stream:
                stream_by_table[table.name] = stream
    for res in project_iac(ctx.files, ctx.root):
        if res.source != "cloudformation":
            continue
        if res.type in {"AWS::DynamoDB::Table", "AWS::DynamoDB::GlobalTable"}:
            table, stream = _cfn_dynamodb(res)
            model.tables.append(table)
            if stream:
                stream_by_table.setdefault(table.name, stream)
        elif res.type == "AWS::Lambda::EventSourceMapping":
            blob = json.dumps(res.attrs)
            if "stream" not in blob.lower() and "dynamodb" not in blob.lower():
                continue
            idem = "ReportBatchItemFailures" in blob
            for stream in stream_by_table.values():
                stream.consumers.append(("lambda", Path(res.file), res.line))
                stream.idempotency_signal = stream.idempotency_signal or idem
            if not stream_by_table:
                model.streams.append(
                    DynamoStreamModel(
                        table="",
                        view_type="",
                        file=Path(res.file),
                        line=res.line,
                        consumers=[("lambda", Path(res.file), res.line)],
                        idempotency_signal=idem,
                    )
                )

    # Stream consumers (terraform event-source mappings).
    for kind, file, line, idem in _tf_stream_consumers(tf.resources):
        for stream in stream_by_table.values():
            stream.consumers.append((kind, file, line))
            stream.idempotency_signal = stream.idempotency_signal or idem
        if not stream_by_table:
            # consumer without a declared stream - still evidence
            model.streams.append(
                DynamoStreamModel(
                    table="",
                    view_type="",
                    file=file,
                    line=line,
                    consumers=[(kind, file, line)],
                    idempotency_signal=idem,
                )
            )

    # --- code plane: boto3 bindings + op call sites ----------------------
    index = project_index(ctx)
    for relative, module in sorted(index.modules.items(), key=lambda kv: kv[0].as_posix()):
        bindings = _ddb_bindings(module)
        if bindings:
            for _target, bound in bindings.items():
                if bound.startswith("table:"):
                    name = bound[6:]
                    if not any(t.name == name for t in model.tables):
                        model.tables.append(
                            DynamoTable(name=name, source="code", file=relative, line=0)
                        )
            model.accesses.extend(_ddb_ops(module, bindings, relative))
        # idempotency signals in code (batchItemFailures responses)
        text = ctx.read_text(relative) or ""
        for m in _ENTITY_PREFIX_RE.finditer(text):
            model.single_table.entities.setdefault(m.group(1), []).append(
                (relative, text[: m.start()].count("\n") + 1)
            )
        if stream_by_table and re.search(
            r"batchItemFailures|batch_item_failures|report_batch_item_failures",
            text,
            re.I,
        ):
            for stream in (*stream_by_table.values(), *model.streams):
                stream.idempotency_signal = True

    model.streams.extend(s for s in stream_by_table.values() if s not in model.streams)
    for table in model.tables:
        if table.is_global:
            model.global_tables.append(
                DynamoGlobalTableModel(
                    table=table.name,
                    mode=table.global_mode,
                    regions=sorted(set(table.replicas)),
                    file=table.file,
                    line=table.line,
                )
            )
    setattr(ctx, _CACHE_ATTR, model)
    return model
