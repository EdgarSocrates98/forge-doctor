"""Lake Formation first-class model - governance, grants, cross-account.

Reads Terraform/CFN (``project_iac`` + nested-block mining), boto3
``lakeformation``/``glue`` call-sites, and IAM policy documents that name
``lakeformation:`` actions. Everything is offline; nothing is fetched.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from forge_doctor.analyzers.hcl_lite import (
    _RESOURCE_HEAD,
    _brace_block,
    _flat_attrs,
    project_iac,
)
from forge_doctor.analyzers.index import project_index
from forge_doctor.core.context import ProjectContext

# ---------------------------------------------------------------- model


@dataclass(frozen=True)
class LFGrant:
    """One principal+permissions grant (TF permissions, CFN, or boto3)."""

    principal: str
    permissions: tuple[str, ...]
    # database|table|columns|lf_tag|lf_tag_expression|data_location|data_cells_filter|catalog
    resource_kind: str
    resource_name: str
    file: Path
    line: int
    source: str  # terraform|cloudformation|boto3
    cross_account: bool = False
    grant_option: bool = False


@dataclass(frozen=True)
class LFDataLocation:
    """A registered S3 data location (``aws_lakeformation_resource``)."""

    arn: str
    role_arn: str
    file: Path
    line: int
    source: str
    hybrid: bool = False


@dataclass(frozen=True)
class LFResourceLink:
    """A Glue catalog database that links to a remote (producer) database."""

    name: str
    target_database: str
    target_catalog: str
    file: Path
    line: int
    source: str


@dataclass(frozen=True)
class LFTag:
    """An ``aws_lakeformation_lf_tag`` definition."""

    key: str
    values: tuple[str, ...]
    file: Path
    line: int
    source: str


@dataclass(frozen=True)
class LFCellsFilter:
    """A data-cells (row/column) filter."""

    name: str
    database: str
    table: str
    columns: tuple[str, ...]
    row_filter: str
    file: Path
    line: int
    source: str


@dataclass(frozen=True)
class LFRamShare:
    """A RAM resource share + the principals it shares with."""

    name: str
    resources: tuple[str, ...]
    principals: tuple[str, ...]
    file: Path
    line: int


@dataclass(frozen=True)
class LFAdmin:
    arn: str
    file: Path
    line: int


@dataclass
class LakeFormationProjectModel:
    resources: list[Any] = field(default_factory=list)
    principals: set[str] = field(default_factory=set)
    admins: list[LFAdmin] = field(default_factory=list)
    databases: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    data_locations: list[LFDataLocation] = field(default_factory=list)
    resource_links: list[LFResourceLink] = field(default_factory=list)
    grants: list[LFGrant] = field(default_factory=list)
    lf_tags: list[LFTag] = field(default_factory=list)
    filters: list[LFCellsFilter] = field(default_factory=list)
    ram_shares: list[LFRamShare] = field(default_factory=list)
    ram_principals: set[str] = field(default_factory=set)
    external_accounts: set[str] = field(default_factory=set)
    iam_lf_actions: list[str] = field(default_factory=list)
    fgac: bool = False  # column-level grants or data-cells filters observed
    fta: bool = False  # LF-TBAC: grants on lf_tag / lf_tag_expression observed
    hybrid_access: bool = False  # IAMAllowedPrincipals defaults / hybrid flags
    has_lakeformation: bool = False

    def grants_for(self, principal: str) -> list[LFGrant]:
        return [g for g in self.grants if g.principal == principal]


# ------------------------------------------------------------ tf helpers

_LF_TYPES = {
    "aws_lakeformation_permissions",
    "aws_lakeformation_data_lake_settings",
    "aws_lakeformation_resource",
    "aws_lakeformation_lf_tag",
    "aws_lakeformation_resource_lf_tags",
    "aws_lakeformation_data_cells_filter",
    "aws_glue_catalog_database",
    "aws_glue_catalog_table",
    "aws_ram_resource_share",
    "aws_ram_resource_association",
    "aws_ram_principal_association",
    "aws_iam_policy",
    "aws_iam_role_policy",
}
_CFN_LF_TYPES = (
    "AWS::LakeFormation::",
    "AWS::Glue::Database",
    "AWS::Glue::Table",
    "AWS::RAM::",
    "AWS::IAM::Policy",
    "AWS::IAM::ManagedPolicy",
)
_ACCOUNT_RE = re.compile(r"^\d{12}$")
_ARN_ACCOUNT_RE = re.compile(r"arn:aws[a-z-]*:iam::(\d{12}):")
_IAM_PRINCIPALS_RE = re.compile(r"iam_?allowed_?principals", re.IGNORECASE)

_LF_APIS = {
    "grant_permissions",
    "batch_grant_permissions",
    "register_resource",
    "put_data_lake_settings",
    "create_lf_tag",
    "add_lf_tags_to_resource",
    "list_permissions",
    "create_data_cells_filter",
    "deregister_resource",
    "revoke_permissions",
}


def _blocks(body: str) -> dict[str, list[tuple[dict[str, Any], str]]]:
    """``name { ... }`` sub-blocks at body depth 0 -> (flat attrs, sub-body)."""
    out: dict[str, list[tuple[dict[str, Any], str]]] = {}
    depth = 0
    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if ch == '"':
            i += 1
            while i < n and body[i] != '"':
                i += 2 if body[i] == "\\" else 1
            i += 1
            continue
        if ch == "{":
            depth += 1
            i += 1
            continue
        if ch == "}":
            depth = max(0, depth - 1)
            i += 1
            continue
        m = re.match(r"\s*([A-Za-z_][\w]*)\s*\{", body[i:])
        if m and depth == 0:
            name = m.group(1)
            open_at = i + m.end() - 1
            sub, end = _brace_block(body, open_at)
            out.setdefault(name, []).append((_flat_attrs(sub, 0), sub))
            i = end + 1
            continue
        i += 1
    return out


def _str_list(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, list):
        return tuple(str(v) for v in raw)
    if isinstance(raw, str):
        return (raw,)
    return ()


def _account_of(principal: str) -> str | None:
    if _ACCOUNT_RE.match(principal):
        return principal
    m = _ARN_ACCOUNT_RE.match(principal)
    return m.group(1) if m else None


def _resource_block_map(res_attrs: dict[str, Any]) -> dict[str, Any]:
    """CFN ``Resource`` property dict -> kind -> inner dict."""
    out: dict[str, Any] = {}
    for key, val in res_attrs.items():
        if isinstance(val, dict):
            out[key] = val
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    out[key] = item
    return out


# -------------------------------------------------------------- parsing


def _tf_blocks_per_resource(text: str) -> list[tuple[str, str, str, int]]:
    """``(type, name, body, line)`` for every TF resource block."""
    out = []
    for m in _RESOURCE_HEAD.finditer(text):
        body, _end = _brace_block(text, m.end() - 1)
        out.append((m.group(1), m.group(2), body, text.count("\n", 0, m.start()) + 1))
    return out


def _grant_resource(
    blocks: dict[str, list[tuple[dict[str, Any], str]]],
) -> tuple[str, str]:
    """TF ``resource {}`` children -> (kind, name)."""
    for name in (
        "database",
        "table",
        "table_with_columns",
        "lf_tag",
        "data_location",
        "data_cells_filter",
        "catalog",
    ):
        entries = blocks.get(name)
        if not entries:
            continue
        attrs, _sub = entries[0]
        if name == "database":
            return "database", str(attrs.get("name") or "")
        if name in ("table", "table_with_columns"):
            db = str(attrs.get("database_name") or "")
            tbl = str(attrs.get("name") or ("*" if attrs.get("wildcard") else ""))
            kind = "columns" if name == "table_with_columns" else "table"
            return kind, f"{db}.{tbl}".strip(".")
        if name == "lf_tag":
            return "lf_tag", str(attrs.get("key") or "")
        if name == "data_location":
            return "data_location", str(attrs.get("arn") or "")
        if name == "data_cells_filter":
            return "data_cells_filter", str(attrs.get("name") or "")
        return "catalog", "account"
    if blocks.get("lf_tag_expression"):
        return "lf_tag_expression", "lf-tag-expression"
    return "catalog", "account"


def _cf_grant_resource(props: dict[str, Any]) -> tuple[str, str]:
    """CFN ``Resource`` object -> (kind, name)."""
    res = props.get("Resource")
    if not isinstance(res, dict):
        return "catalog", "account"
    m = _resource_block_map(res)
    if "DatabaseResource" in m:
        return "database", str(m["DatabaseResource"].get("Name") or "")
    for key in ("TableResource", "TableWithColumnsResource"):
        if key in m:
            inner = m[key]
            db = str(inner.get("DatabaseName") or "")
            tbl = str(inner.get("Name") or ("*" if inner.get("TableWildcard") else ""))
            return ("columns" if key.endswith("ColumnsResource") else "table"), f"{db}.{tbl}".strip(
                "."
            )
    if "LFTagResource" in m or "LFTag" in m:
        inner = m.get("LFTagResource") or m.get("LFTag") or {}
        return "lf_tag", str(inner.get("TagKey") or inner.get("Key") or "")
    if "LFTagExpressionResource" in m:
        return "lf_tag_expression", "lf-tag-expression"
    if "DataLocationResource" in m:
        return "data_location", str(m["DataLocationResource"].get("S3Resource") or "")
    if "DataCellsFilterResource" in m:
        return "data_cells_filter", str(m["DataCellsFilterResource"].get("Name") or "")
    return "catalog", "account"


def _principal_from(raw: Any) -> str:
    """Normalize the many principal spellings into one string."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        for key in (
            "DataLakePrincipalIdentifier",
            "data_lake_principal_identifier",
            "PrincipalIdentifier",
        ):
            if key in raw:
                return str(raw[key])
    if isinstance(raw, list):
        return ",".join(sorted(_principal_from(x) for x in raw))
    return str(raw or "")


def _is_iam_allowed(principal: str) -> bool:
    return bool(_IAM_PRINCIPALS_RE.search(principal))


def _settings_hybrid(attrs: dict[str, Any]) -> bool:
    """Does a DataLakeSettings attrs dict keep IAMAllowedPrincipals defaults?"""
    for key in ("create_database_default_permissions", "create_table_default_permissions"):
        raw = attrs.get(key)
        text = json.dumps(raw) if isinstance(raw, (dict, list)) else str(raw or "")
        if "IAMAllowedPrincipals" in text:
            return True
    params = attrs.get("Parameters") or attrs.get("parameters")
    return isinstance(params, dict) and bool(_IAM_PRINCIPALS_RE.search(json.dumps(params)))


def lakeformation_model(ctx: ProjectContext) -> LakeFormationProjectModel:
    """Fuse all Lake Formation evidence into one model."""
    model = LakeFormationProjectModel()
    resources = project_iac(ctx.files, ctx.root)

    for res in resources:
        rtype = res.type
        is_lf = rtype in _LF_TYPES or any(rtype.startswith(p) for p in _CFN_LF_TYPES)
        if not is_lf:
            continue
        model.resources.append(res)
        attrs = res.attrs
        file = Path(res.file)

        # ------------------------------ data lake settings (admins/hybrid)
        if rtype in (
            "aws_lakeformation_data_lake_settings",
            "AWS::LakeFormation::DataLakeSettings",
        ):
            admins = attrs.get("admins") or attrs.get("Admins") or []
            for admin in admins if isinstance(admins, list) else [admins]:
                arn = _principal_from(admin)
                if arn:
                    model.admins.append(LFAdmin(arn=arn, file=file, line=res.line))
                    model.principals.add(arn)
            if res.source == "terraform":
                sub = _blocks(_block_body(ctx, res))
                for key in (
                    "create_database_default_permissions",
                    "create_table_default_permissions",
                ):
                    for raw, _b in sub.get(key, []):
                        principals = _str_list(raw.get("principal") or raw.get("principals"))
                        perms = _str_list(raw.get("permissions"))
                        for p in principals:
                            model.grants.append(
                                LFGrant(
                                    principal=_principal_from(p),
                                    permissions=perms,
                                    resource_kind="catalog",
                                    resource_name="default",
                                    file=file,
                                    line=res.line,
                                    source=res.source,
                                )
                            )
                            if _is_iam_allowed(p):
                                model.hybrid_access = True
            else:
                for key in ("CreateDatabaseDefaultPermissions", "CreateTableDefaultPermissions"):
                    raw_c = attrs.get(key)
                    items: list[Any] = (
                        list(raw_c)
                        if isinstance(raw_c, list)
                        else ([raw_c] if isinstance(raw_c, dict) else [])
                    )
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        principals_raw = item.get("Principals") or []
                        perms_raw = item.get("Permissions") or []
                        for p in (
                            principals_raw if isinstance(principals_raw, list) else [principals_raw]
                        ):
                            model.grants.append(
                                LFGrant(
                                    principal=_principal_from(p),
                                    permissions=_str_list(perms_raw),
                                    resource_kind="catalog",
                                    resource_name="default",
                                    file=file,
                                    line=res.line,
                                    source=res.source,
                                )
                            )
                            if _is_iam_allowed(_principal_from(p)):
                                model.hybrid_access = True
            model.hybrid_access = model.hybrid_access or _settings_hybrid(attrs)

        # -------------------------------------------------- LF permissions
        elif rtype in (
            "aws_lakeformation_permissions",
            "AWS::LakeFormation::Permissions",
            "AWS::LakeFormation::PrincipalPermissions",
        ):
            if res.source == "terraform":
                body = _block_body(ctx, res)
                sub = _blocks(body)
                resource_blocks = _blocks(sub["resource"][0][1]) if sub.get("resource") else sub
                kind, rname = _grant_resource(resource_blocks)
                principal = _principal_from(attrs.get("principal"))
                perms = _str_list(attrs.get("permissions"))
                acct = _account_of(principal)
                grant = LFGrant(
                    principal=principal,
                    permissions=perms,
                    resource_kind=kind,
                    resource_name=rname,
                    file=file,
                    line=res.line,
                    source=res.source,
                    cross_account=bool(acct),
                    grant_option=bool(attrs.get("permissions_with_grant_option")),
                )
                model.grants.append(grant)
                if principal:
                    model.principals.add(principal)
                if acct:
                    model.external_accounts.add(acct)
                if kind == "columns" or "column_names" in json.dumps(resource_blocks):
                    model.fgac = True
                if kind in ("lf_tag", "lf_tag_expression"):
                    model.fta = True
                if _is_iam_allowed(principal):
                    model.hybrid_access = True
            else:
                principal = _principal_from(
                    attrs.get("Principal")
                    or attrs.get("PrincipalIdentifier")
                    or attrs.get("DataLakePrincipal")
                )
                perms = _str_list(attrs.get("Permissions"))
                kind, rname = _cf_grant_resource(attrs)
                acct = _account_of(principal)
                model.grants.append(
                    LFGrant(
                        principal=principal,
                        permissions=perms,
                        resource_kind=kind,
                        resource_name=rname,
                        file=file,
                        line=res.line,
                        source=res.source,
                        cross_account=bool(acct),
                    )
                )
                if principal:
                    model.principals.add(principal)
                if acct:
                    model.external_accounts.add(acct)
                if kind == "columns":
                    model.fgac = True
                if kind in ("lf_tag", "lf_tag_expression"):
                    model.fta = True
                if _is_iam_allowed(principal):
                    model.hybrid_access = True

        # -------------------------------------------------- data locations
        elif rtype in ("aws_lakeformation_resource", "AWS::LakeFormation::Resource"):
            arn = str(attrs.get("arn") or attrs.get("ResourceArn") or "")
            role = str(attrs.get("role_arn") or attrs.get("RoleArn") or "")
            hybrid = (
                str(
                    attrs.get("hybrid_access_enabled") or attrs.get("HybridAccessEnabled") or ""
                ).lower()
                == "true"
            )
            model.data_locations.append(
                LFDataLocation(
                    arn=arn,
                    role_arn=role,
                    file=file,
                    line=res.line,
                    source=res.source,
                    hybrid=hybrid,
                )
            )
            if hybrid:
                model.hybrid_access = True

        # -------------------------------------------------------- LF tags
        elif rtype in ("aws_lakeformation_lf_tag", "AWS::LakeFormation::Tag"):
            key = str(attrs.get("key") or attrs.get("TagKey") or "")
            values = _str_list(attrs.get("values") or attrs.get("TagValues"))
            model.lf_tags.append(
                LFTag(key=key, values=values, file=file, line=res.line, source=res.source)
            )

        elif rtype in ("aws_lakeformation_resource_lf_tags", "AWS::LakeFormation::TagAssociation"):
            model.fta = True

        # ------------------------------------------------- data cells filters
        elif rtype in (
            "aws_lakeformation_data_cells_filter",
            "AWS::LakeFormation::DataCellsFilter",
        ):
            if res.source == "terraform":
                sub = _blocks(_block_body(ctx, res))
                tda, td_body = (sub.get("table_data") or [({}, "")])[0]
                row_attrs, _rb = (_blocks(td_body).get("row_filter") or [({}, "")])[0]
                model.filters.append(
                    LFCellsFilter(
                        name=str(tda.get("name") or ""),
                        database=str(tda.get("database_name") or ""),
                        table=str(tda.get("table_name") or ""),
                        columns=_str_list(tda.get("column_names")),
                        row_filter=str(row_attrs.get("filter_expression") or ""),
                        file=file,
                        line=res.line,
                        source=res.source,
                    )
                )
            else:
                model.filters.append(
                    LFCellsFilter(
                        name=str(attrs.get("Name") or ""),
                        database=str(attrs.get("DatabaseName") or ""),
                        table=str(attrs.get("TableName") or ""),
                        columns=_str_list(attrs.get("ColumnNames")),
                        row_filter=str(
                            (attrs.get("RowFilter") or {}).get("FilterExpression", "")
                            if isinstance(attrs.get("RowFilter"), dict)
                            else attrs.get("RowFilter") or ""
                        ),
                        file=file,
                        line=res.line,
                        source=res.source,
                    )
                )
            model.fgac = True

        # -------------------------------------------------- glue catalog
        elif rtype in ("aws_glue_catalog_database", "AWS::Glue::Database"):
            name = str(attrs.get("name") or attrs.get("Name") or res.name)
            model.databases.append(name)
            target = attrs.get("target_database") or attrs.get("TargetDatabase")
            inner = target if isinstance(target, dict) else {}
            db_input = attrs.get("DatabaseInput")
            if isinstance(db_input, dict):
                t2 = db_input.get("TargetDatabase")
                if isinstance(t2, dict):
                    inner = t2
                    name = str(db_input.get("Name") or name)
            if inner:
                model.resource_links.append(
                    LFResourceLink(
                        name=name,
                        target_database=str(
                            inner.get("database_name") or inner.get("DatabaseName") or ""
                        ),
                        target_catalog=str(inner.get("catalog_id") or inner.get("CatalogId") or ""),
                        file=file,
                        line=res.line,
                        source=res.source,
                    )
                )
            if res.source == "terraform" and not inner:
                # target_database may be a nested block missed by flat attrs
                td = _blocks(_block_body(ctx, res)).get("target_database")
                if td:
                    tda, _tb = td[0]
                    model.resource_links.append(
                        LFResourceLink(
                            name=name,
                            target_database=str(tda.get("database_name") or ""),
                            target_catalog=str(tda.get("catalog_id") or ""),
                            file=file,
                            line=res.line,
                            source=res.source,
                        )
                    )

        elif rtype in ("aws_glue_catalog_table", "AWS::Glue::Table"):
            db = str(attrs.get("database_name") or attrs.get("DatabaseName") or "")
            name = str(attrs.get("name") or attrs.get("Name") or res.name)
            table_input = attrs.get("TableInput")
            if isinstance(table_input, dict):
                name = str(table_input.get("Name") or name)
            model.tables.append(f"{db}.{name}" if db else name)

        # ------------------------------------------------------------- RAM
        elif rtype in ("aws_ram_resource_share", "AWS::RAM::ResourceShare"):
            external = str(
                attrs.get("allow_external_principals") or attrs.get("AllowExternalPrincipals") or ""
            ).lower()
            model.ram_shares.append(
                LFRamShare(
                    name=str(attrs.get("name") or res.name),
                    resources=(),
                    principals=() if external != "true" else ("*",),
                    file=file,
                    line=res.line,
                )
            )

        elif rtype == "aws_ram_principal_association":
            principal = str(attrs.get("principal") or "")
            if _ACCOUNT_RE.match(principal):
                model.ram_principals.add(principal)
                model.principals.add(principal)

        # --------------------------------------------------- IAM evidence
        elif rtype in (
            "aws_iam_policy",
            "aws_iam_role_policy",
            "AWS::IAM::Policy",
            "AWS::IAM::ManagedPolicy",
        ):
            # policy bodies (jsonencode / embedded JSON) live in nested
            # blocks - scan the raw resource body, not just flat attrs.
            blob = _block_body(ctx, res) if res.source == "terraform" else json.dumps(attrs)
            for action in sorted(set(re.findall(r"lakeformation:[A-Za-z*]+", blob))):
                model.iam_lf_actions.append(action)
            for action in sorted(
                set(re.findall(r"glue:(GetTable|GetDatabase|CreateDatabase)[A-Za-z*]*", blob))
            ):
                model.iam_lf_actions.append(action)

    # ------------------------------------------------------ boto3 call-sites
    index = project_index(ctx)
    for module in index.modules.values():
        _boto3_evidence(module, model)

    # Cross-account pass: an ARN's account is "external" only when it differs
    # from every locally-inferable account (admins, registration role ARNs).
    # Bare 12-digit principals are account-level grants by definition.
    local_accounts = {acct for acct in (_account_of(a.arn) for a in model.admins) if acct}
    local_accounts.update(
        acct for acct in (_account_of(d.role_arn) for d in model.data_locations) if acct
    )
    model.grants = [
        replace(
            g,
            cross_account=bool(
                g.principal
                and _account_of(g.principal)
                and (
                    _ACCOUNT_RE.match(g.principal)
                    or (local_accounts and _account_of(g.principal) not in local_accounts)
                )
            ),
        )
        for g in model.grants
    ]
    model.external_accounts = {
        acct for acct in (_account_of(g.principal) for g in model.grants) if acct
    } - local_accounts
    model.external_accounts.update(
        acct
        for acct in (link.target_catalog for link in model.resource_links)
        if _ACCOUNT_RE.match(acct)
    )
    model.external_accounts.update(model.ram_principals - local_accounts)

    model.has_lakeformation = bool(
        model.resources or model.grants or model.data_locations or model.lf_tags
    )
    model.databases = sorted(set(model.databases))
    model.tables = sorted(set(model.tables))
    model.columns = sorted(set(model.columns))
    model.iam_lf_actions = sorted(set(model.iam_lf_actions))
    return model


def _boto3_evidence(module: Any, model: LakeFormationProjectModel) -> None:
    """``boto3.client('lakeformation'|'glue')`` call-sites -> model facts."""
    if module.tree is None:
        return
    bindings: dict[str, str] = {}
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
        if lit0 in ("lakeformation", "glue"):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = lit0
    if not bindings:
        return
    for node in ast.walk(module.tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)):
            continue
        service = bindings.get(func.value.id)
        api = func.attr
        if service == "lakeformation" and api in _LF_APIS:
            _lf_api_call(node, api, module, model)
        elif service == "glue" and api == "create_database":
            kw = _literal_kwargs(node)
            target = kw.get("DatabaseInput", {}).get("TargetDatabase")
            if isinstance(target, dict):
                model.resource_links.append(
                    LFResourceLink(
                        name=str(kw.get("DatabaseInput", {}).get("Name") or kw.get("Name") or ""),
                        target_database=str(target.get("DatabaseName") or ""),
                        target_catalog=str(target.get("CatalogId") or ""),
                        file=module.file,
                        line=node.lineno,
                        source="boto3",
                    )
                )


def _literal_kwargs(call: ast.Call) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for kw in call.keywords:
        if kw.arg:
            try:
                out[kw.arg] = ast.literal_eval(kw.value)
            except (ValueError, TypeError):
                continue
    return out


def _lf_api_call(node: ast.Call, api: str, module: Any, model: LakeFormationProjectModel) -> None:
    kw = _literal_kwargs(node)
    if api in ("grant_permissions", "batch_grant_permissions"):
        entries = kw.get("Entries") if api == "batch_grant_permissions" else [kw]
        entries = entries if isinstance(entries, list) else [entries]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            principal = _principal_from(entry.get("Principal") or entry.get("principal") or "")
            res_block = entry.get("Resource") or entry.get("resource") or {}
            perms = tuple(str(p) for p in _str_list(entry.get("Permissions")))
            kind, rname = _boto3_resource(res_block)
            acct = _account_of(principal)
            model.grants.append(
                LFGrant(
                    principal=principal,
                    permissions=perms,
                    resource_kind=kind,
                    resource_name=rname,
                    file=module.file,
                    line=node.lineno,
                    source="boto3",
                    cross_account=bool(acct),
                    grant_option=bool(entry.get("PermissionsWithGrantOption")),
                )
            )
            if principal:
                model.principals.add(principal)
            if kind == "columns":
                model.fgac = True
            if kind in ("lf_tag", "lf_tag_expression"):
                model.fta = True
    elif api == "register_resource":
        model.data_locations.append(
            LFDataLocation(
                arn=str(kw.get("ResourceArn") or ""),
                role_arn=str(kw.get("RoleArn") or ""),
                file=module.file,
                line=node.lineno,
                source="boto3",
                hybrid=bool(kw.get("HybridAccessEnabled")),
            )
        )


def _boto3_resource(res: Any) -> tuple[str, str]:
    """boto3 ``Resource={...}`` dict -> (kind, name)."""
    if not isinstance(res, dict):
        return "catalog", "account"
    if "TableWithColumnsResource" in res or "TableWithColumns" in res:
        inner = res.get("TableWithColumnsResource") or res.get("TableWithColumns") or {}
        db, tbl = str(inner.get("DatabaseName") or ""), str(inner.get("Name") or "")
        return "columns", f"{db}.{tbl}".strip(".")
    if "Table" in res or "TableResource" in res:
        inner = res.get("Table") or res.get("TableResource") or {}
        db, tbl = str(inner.get("DatabaseName") or ""), str(inner.get("Name") or "")
        return "table", f"{db}.{tbl}".strip(".")
    if "Database" in res or "DatabaseResource" in res:
        inner = res.get("Database") or res.get("DatabaseResource") or {}
        return "database", str(inner.get("Name") or "")
    if "LFTag" in res or "LFTagExpression" in res:
        return "lf_tag", "lf-tag"
    if "DataLocationResource" in res:
        inner = res.get("DataLocationResource") or {}
        return "data_location", str(inner.get("S3Resource") or inner.get("ResourceArn") or "")
    if "DataCellsFilter" in res:
        inner = res.get("DataCellsFilter") or {}
        return "data_cells_filter", str(inner.get("Name") or "")
    return "catalog", "account"


def _attr_dotted(node: Any) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _block_body(ctx: ProjectContext, res: Any) -> str:
    text = (ctx.root / res.file).read_text(encoding="utf-8", errors="replace")
    for rtype, _name, body, line in _tf_blocks_per_resource(text):
        if rtype == res.type and line == res.line:
            return body
    return ""
