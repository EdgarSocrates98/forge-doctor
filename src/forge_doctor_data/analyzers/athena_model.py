"""Athena deep-intelligence model.

Fuses Terraform ``aws_athena_*`` resources, CloudFormation
``AWS::Athena::*`` resources, SQL evidence (CTAS / UNLOAD / prepared
statements / Iceberg DDL), and boto3 ``athena`` call-sites into one
deterministic, offline-only project model. No AWS calls.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forge_doctor_data.analyzers.hcl_lite import project_iac
from forge_doctor_data.analyzers.index import project_index
from forge_doctor_data.analyzers.terraform_model import terraform_model
from forge_doctor_data.core.context import ProjectContext


@dataclass(frozen=True)
class AthenaWorkgroup:
    name: str
    file: Path
    line: int
    source: str = "terraform"
    engine_version: str = ""  # e.g. "Athena engine version 3"
    enforce_config: bool = False
    result_location: str = ""
    bytes_scanned_cutoff: int = 0
    publish_metrics: bool = False
    encryption: str = ""  # SSE_S3|SSE_KMS|CSE_KMS


@dataclass(frozen=True)
class AthenaDataCatalog:
    name: str
    type: str  # GLUE|LAMBDA|HIVE|FEDERATED
    file: Path
    line: int


@dataclass(frozen=True)
class AthenaNamedQuery:
    name: str
    workgroup: str
    file: Path
    line: int
    prepared: bool = False  # aws_athena_prepared_statement
    query_text: str = ""


@dataclass(frozen=True)
class AthenaSqlOp:
    """One SQL statement shape relevant to Athena execution."""

    op: str  # ctas|unload|prepare|execute_prepare|select|insert
    file: Path
    line: int
    target: str = ""  # table written (CTAS/UNLOAD dest)


@dataclass
class AthenaProjectModel:
    """Everything the project evidences about Athena, in sorted order."""

    workgroups: list[AthenaWorkgroup] = field(default_factory=list)
    catalogs: list[AthenaDataCatalog] = field(default_factory=list)
    databases: list[str] = field(default_factory=list)  # aws_athena_database names
    named_queries: list[AthenaNamedQuery] = field(default_factory=list)
    sql_ops: list[AthenaSqlOp] = field(default_factory=list)
    iceberg_ddl: bool = False
    boto3_calls: list[str] = field(default_factory=list)  # athena api names seen
    has_athena: bool = False

    def ops_by_name(self, op: str) -> list[AthenaSqlOp]:
        return [o for o in self.sql_ops if o.op == op]

    @property
    def named_query_names(self) -> set[str]:
        return {q.name for q in self.named_queries}


_SQL_PATTERNS: list[tuple[str, re.Pattern[str], re.Pattern[str] | None]] = [
    (
        "ctas",
        re.compile(r"\bCREATE\s+TABLE\s+[\w.`]+\s+.*?AS\s+SELECT\b", re.IGNORECASE | re.DOTALL),
        re.compile(r"\bCREATE\s+TABLE\s+([\w.`]+)", re.IGNORECASE),
    ),
    (
        "unload",
        re.compile(r"\bUNLOAD\s*\(", re.IGNORECASE),
        re.compile(r"\bUNLOAD\s*\(.*?\)\s*TO\s*'([^']+)'", re.IGNORECASE | re.DOTALL),
    ),
    ("prepare", re.compile(r"\bPREPARE\s+\w+\s+", re.IGNORECASE), None),
    ("execute_prepare", re.compile(r"\bEXECUTE\s+\w+", re.IGNORECASE), None),
]


def _engine_version(attrs: dict[str, Any], body: str) -> str:
    """Engine version from Athena engine_version config block or text."""
    for k in ("engine_version", "EngineVersion", "selected_engine_version"):
        if attrs.get(k):
            return str(attrs[k])
    m = re.search(
        r"engine_version[\"']?\s*[:=]\s*[\"']?(Athena engine version \d|AUTO|\d)",
        body,
        re.IGNORECASE,
    )
    return m.group(1) if m else ""


def _tf_workgroup(b: Any) -> AthenaWorkgroup:
    attrs, body = b.attrs, b.body
    cfg = re.search(r"configuration\s*\{(.*?)\n\}", body, re.DOTALL) or re.search(
        r"configuration\s*\{(.*)", body, re.DOTALL
    )
    cfg_body = cfg.group(1) if cfg else body
    cut = re.search(r"bytes_scanned_cutoff_per_query\s*=\s*(\d+)", cfg_body)
    enc = re.search(r"encryption_option[\"']?\s*=\s*\"?([A-Z_]+)", cfg_body, re.IGNORECASE)
    loc = re.search(r"output_location[\"']?\s*=\s*\"([^\"]+)", cfg_body, re.IGNORECASE)
    return AthenaWorkgroup(
        name=str(attrs.get("name") or (b.labels[-1] if b.labels else "")),
        file=Path(b.file),
        line=b.line,
        engine_version=_engine_version(attrs, body),
        enforce_config=bool(
            re.search(r"enforce_workgroup_configuration\s*=\s*true", body, re.IGNORECASE)
        ),
        result_location=str(attrs.get("result_location") or (loc.group(1) if loc else "")),
        bytes_scanned_cutoff=int(cut.group(1)) if cut else 0,
        publish_metrics=bool(
            re.search(r"publish_cloudwatch_metrics_enabled\s*=\s*true", body, re.IGNORECASE)
        ),
        encryption=enc.group(1).upper() if enc else "",
    )


def _athena_sql(text: str, file: Path) -> tuple[list[AthenaSqlOp], bool]:
    """Athena-relevant SQL ops + iceberg DDL flag from one .sql/.sqlx file."""
    ops: list[AthenaSqlOp] = []
    iceberg = bool(
        re.search(
            r"table_type\s*=\s*'ICEBERG'|USING\s+ICEBERG\b|LOCATION\s+'s3://",
            text,
            re.IGNORECASE,
        )
    )
    for op, rex, tgt_rex in _SQL_PATTERNS:
        for m in rex.finditer(text):
            tgt = ""
            if tgt_rex is not None:
                tm = tgt_rex.search(text, m.start())
                if tm:
                    tgt = tm.group(1)
            ops.append(
                AthenaSqlOp(
                    op=op,
                    file=file,
                    line=text.count("\n", 0, m.start()) + 1,
                    target=tgt,
                )
            )
    return ops, iceberg


def athena_model(ctx: ProjectContext) -> AthenaProjectModel:
    """Fuse Athena evidence (TF/CFN/sql/boto3) into one model."""
    model = AthenaProjectModel()

    for b in terraform_model(ctx).resources:
        rtype = b.labels[0] if b.labels else ""
        file = Path(b.file)
        if rtype == "aws_athena_workgroup":
            model.workgroups.append(_tf_workgroup(b))
        elif rtype == "aws_athena_data_catalog":
            model.catalogs.append(
                AthenaDataCatalog(
                    name=str(b.attrs.get("name") or (b.labels[-1] if b.labels else "")),
                    type=str(b.attrs.get("type") or "GLUE").upper(),
                    file=file,
                    line=b.line,
                )
            )
        elif rtype == "aws_athena_database":
            model.databases.append(str(b.attrs.get("name") or (b.labels[-1] if b.labels else "")))
        elif rtype in ("aws_athena_named_query", "aws_athena_prepared_statement"):
            model.named_queries.append(
                AthenaNamedQuery(
                    name=str(b.attrs.get("name") or (b.labels[-1] if b.labels else "")),
                    workgroup=str(b.attrs.get("workgroup") or b.attrs.get("work_group") or ""),
                    file=file,
                    line=b.line,
                    prepared=rtype.endswith("prepared_statement"),
                    query_text=str(b.attrs.get("query") or b.attrs.get("query_statement") or "")[
                        :200
                    ],
                )
            )

    for res in project_iac(ctx.files, ctx.root):
        if res.source != "cloudformation":
            continue
        a, file = res.attrs, Path(res.file)
        if res.type == "AWS::Athena::WorkGroup":
            cfg = a.get("WorkGroupConfiguration") or {}
            if not isinstance(cfg, dict):
                cfg = {}
            rcfg = cfg.get("ResultConfiguration") or {}
            if not isinstance(rcfg, dict):
                rcfg = {}
            eng = cfg.get("EngineVersion") or {}
            model.workgroups.append(
                AthenaWorkgroup(
                    name=str(a.get("Name") or res.name),
                    file=file,
                    line=res.line,
                    source="cloudformation",
                    engine_version=str(eng.get("SelectedEngineVersion") or "")
                    if isinstance(eng, dict)
                    else "",
                    enforce_config=bool(cfg.get("EnforceWorkGroupConfiguration")),
                    result_location=str(rcfg.get("OutputLocation") or ""),
                    bytes_scanned_cutoff=int(cfg.get("BytesScannedCutoffPerQuery") or 0),
                    publish_metrics=bool(cfg.get("PublishCloudWatchMetricsEnabled")),
                )
            )
        elif res.type == "AWS::Athena::DataCatalog":
            model.catalogs.append(
                AthenaDataCatalog(
                    name=str(a.get("Name") or res.name),
                    type=str(a.get("Type") or "GLUE").upper(),
                    file=file,
                    line=res.line,
                )
            )
        elif res.type == "AWS::Athena::Database":
            model.databases.append(str(a.get("DatabaseName") or res.name))
        elif res.type in ("AWS::Athena::NamedQuery", "AWS::Athena::PreparedStatement"):
            model.named_queries.append(
                AthenaNamedQuery(
                    name=str(a.get("QueryName") or a.get("StatementName") or res.name),
                    workgroup=str(a.get("WorkGroup") or ""),
                    file=file,
                    line=res.line,
                    prepared=res.type.endswith("PreparedStatement"),
                    query_text=str(a.get("QueryStatement") or "")[:200],
                )
            )

    # SQL files: athena-flavored ops (gated on athena markers to avoid
    # counting every generic SELECT as athena evidence).
    for relative in sorted(ctx.files):
        if relative.suffix.lower() not in (".sql", ".hql"):
            continue
        text = ctx.read_text(relative) or ""
        ops, iceberg = _athena_sql(text, relative)
        model.sql_ops.extend(ops)
        model.iceberg_ddl = model.iceberg_ddl or iceberg

    # boto3 athena call-sites (arg-bound client vars, same convention as emr)
    index = project_index(ctx)
    calls: set[str] = set()
    for module in index.modules.values():
        bound = _athena_bindings(module)
        if module.tree is None:
            continue
        for node in ast.walk(module.tree):
            if isinstance(node, ast.Call):
                dotted = _attr_dotted(node.func)
                if dotted.split(".")[0] in bound:
                    calls.add(f"athena.{dotted.rsplit('.', 1)[-1]}")
    model.boto3_calls = sorted(calls)

    model.workgroups.sort(key=lambda w: (w.file.as_posix(), w.line, w.name))
    model.catalogs.sort(key=lambda c: (c.file.as_posix(), c.line, c.name))
    model.databases = sorted(set(model.databases))
    model.named_queries.sort(key=lambda q: (q.file.as_posix(), q.line, q.name))
    model.sql_ops.sort(key=lambda o: (o.file.as_posix(), o.line, o.op))
    model.has_athena = bool(
        model.workgroups
        or model.catalogs
        or model.databases
        or model.named_queries
        or model.sql_ops
        or model.boto3_calls
    )
    return model


def _attr_dotted(node: Any) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _athena_bindings(module: Any) -> set[str]:
    """var names bound to ``boto3.client('athena')``."""
    out: set[str] = set()
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
        if lit0 == "athena":
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out.add(target.id)
    return out
