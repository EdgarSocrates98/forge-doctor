"""Azure data-platform model (spec 233).

Deterministic, offline evidence only: ``azurerm_*`` Terraform blocks and
committed config artifacts. Every record keeps its file/line provenance;
fields carry strings so "unset" stays distinguishable from "false".
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext

_CACHE_ATTR = "_forge_doctor_azure_model"


@dataclass(frozen=True)
class AdlsAccount:
    """A storage account hosting (or claiming) ADLS Gen2 filesystems."""

    name: str
    file: Path
    line: int
    hns_enabled: str = ""  # is_hns_enabled true|false|""
    replication: str = ""  # account_replication_type
    public_access: str = ""  # public_network_access_enabled
    filesystems: tuple[str, ...] = ()


@dataclass(frozen=True)
class EventHub:
    name: str
    file: Path
    line: int
    namespace: str = ""
    partitions: str = ""  # partition_count
    message_retention_days: str = ""  # message_retention_in_days
    capture: bool = False  # capture_description block present


@dataclass(frozen=True)
class EventHubNamespace:
    name: str
    file: Path
    line: int
    resource: str = ""  # terraform label (ref lookups)
    sku: str = ""  # Basic|Standard|Premium
    zone_redundant: str = ""


@dataclass(frozen=True)
class SynapseWorkspace:
    name: str
    file: Path
    line: int
    sql_pools: tuple[str, ...] = ()
    spark_pools: tuple[str, ...] = ()
    serverless_sql: bool = False  # workspace exists → serverless endpoint implied


@dataclass(frozen=True)
class AdfFactory:
    name: str
    file: Path
    line: int
    pipelines: tuple[str, ...] = ()


@dataclass(frozen=True)
class PurviewAccount:
    name: str
    file: Path
    line: int


@dataclass(frozen=True)
class FabricCapacity:
    """Microsoft Fabric capacity/workspace surface (azurerm_fabric_*)."""

    name: str
    file: Path
    line: int
    sku: str = ""


@dataclass(frozen=True)
class FunctionApp:
    name: str
    file: Path
    line: int


@dataclass
class AzurePlatformModel:
    """All Azure data-platform evidence for a project."""

    adls_accounts: list[AdlsAccount] = field(default_factory=list)
    eventhubs: list[EventHub] = field(default_factory=list)
    eventhub_namespaces: list[EventHubNamespace] = field(default_factory=list)
    synapse_workspaces: list[SynapseWorkspace] = field(default_factory=list)
    adf_factories: list[AdfFactory] = field(default_factory=list)
    purview_accounts: list[PurviewAccount] = field(default_factory=list)
    fabric_capacities: list[FabricCapacity] = field(default_factory=list)
    function_apps: list[FunctionApp] = field(default_factory=list)

    @property
    def has_evidence(self) -> bool:
        return bool(self.services())

    def services(self) -> list[str]:
        """Distinct Azure services detected (deterministic order)."""
        out: list[str] = []
        for attr, name in (
            ("adls_accounts", "adls_gen2"),
            ("eventhubs", "eventhubs"),
            ("eventhub_namespaces", "eventhubs"),
            ("synapse_workspaces", "synapse"),
            ("adf_factories", "adf"),
            ("purview_accounts", "purview"),
            ("fabric_capacities", "fabric"),
            ("function_apps", "azure_functions"),
        ):
            if getattr(self, attr):
                out.append(name)
        return sorted(set(out))


def _s(attrs: dict[str, Any], *keys: str) -> str:
    for key in keys:
        v = attrs.get(key)
        if v is not None and str(v) != "":
            if isinstance(v, bool):
                return str(v).lower()
            return str(v)
    return ""


def _body_has(body: str, pattern: str) -> bool:
    """True when a nested block appears (bodies carry nested config — the
    flat attr map only covers top-level scalars)."""
    import re

    return bool(re.search(pattern, body, re.IGNORECASE | re.DOTALL))


def _body_value(body: str, pattern: str) -> str:
    import re

    m = re.search(pattern, body, re.IGNORECASE | re.DOTALL)
    return m.group(1) if m else ""


def azure_model(ctx: ProjectContext) -> AzurePlatformModel:
    """Memoized Azure evidence model over Terraform resources."""
    cached = getattr(ctx, _CACHE_ATTR, None)
    if cached is not None:
        return cast(AzurePlatformModel, cached)

    from forge_doctor.analyzers.terraform_model import terraform_model

    model = AzurePlatformModel()
    tf = terraform_model(ctx)
    accounts: dict[str, AdlsAccount] = {}
    filesystems: dict[str, list[str]] = {}
    synapse: dict[str, list[str]] = {"sql": [], "spark": []}
    factories: dict[str, AdfFactory] = {}
    pipelines: dict[str, list[str]] = {}

    for block in tf.blocks:
        if block.kind != "resource" or len(block.labels) < 2:
            continue
        rtype, rname = block.labels[0], block.labels[1]
        attrs = block.attrs or {}
        if not rtype.startswith("azurerm_"):
            continue
        if rtype == "azurerm_storage_account":
            accounts[rname] = AdlsAccount(
                name=_s(attrs, "name") or rname,
                file=block.file,
                line=block.line,
                hns_enabled=_s(attrs, "is_hns_enabled"),
                replication=_s(attrs, "account_replication_type"),
                public_access=_s(attrs, "public_network_access_enabled"),
            )
        elif rtype == "azurerm_storage_data_lake_gen2_filesystem":
            # filesystem belongs to a storage account via storage_account_id
            owner = _s(attrs, "storage_account_id")
            filesystems.setdefault(owner, []).append(_s(attrs, "name") or rname)
        elif rtype == "azurerm_eventhub_namespace":
            model.eventhub_namespaces.append(
                EventHubNamespace(
                    name=_s(attrs, "name") or rname,
                    file=block.file,
                    line=block.line,
                    resource=rname,
                    sku=_s(attrs, "sku"),
                    zone_redundant=_s(attrs, "zone_redundant"),
                )
            )
        elif rtype == "azurerm_eventhub":
            model.eventhubs.append(
                EventHub(
                    name=_s(attrs, "name") or rname,
                    file=block.file,
                    line=block.line,
                    namespace=_s(attrs, "namespace_name"),
                    partitions=_s(attrs, "partition_count"),
                    message_retention_days=_s(attrs, "message_retention_in_days"),
                    capture=_body_has(block.body, r"capture_description\s*\{"),
                )
            )
        elif rtype == "azurerm_synapse_workspace":
            model.synapse_workspaces.append(
                SynapseWorkspace(
                    name=_s(attrs, "name") or rname,
                    file=block.file,
                    line=block.line,
                    serverless_sql=True,
                )
            )
        elif rtype == "azurerm_synapse_sql_pool":
            synapse["sql"].append(_s(attrs, "name") or rname)
        elif rtype == "azurerm_synapse_spark_pool":
            synapse["spark"].append(_s(attrs, "name") or rname)
        elif rtype == "azurerm_data_factory":
            factories[rname] = AdfFactory(
                name=_s(attrs, "name") or rname, file=block.file, line=block.line
            )
        elif rtype == "azurerm_data_factory_pipeline":
            pipelines.setdefault("", []).append(_s(attrs, "name") or rname)
        elif rtype == "azurerm_purview_account":
            model.purview_accounts.append(
                PurviewAccount(name=_s(attrs, "name") or rname, file=block.file, line=block.line)
            )
        elif rtype in ("azurerm_fabric_capacity",):
            model.fabric_capacities.append(
                FabricCapacity(
                    name=_s(attrs, "name") or rname,
                    file=block.file,
                    line=block.line,
                    sku=_s(attrs, "sku"),
                )
            )
        elif rtype in (
            "azurerm_function_app",
            "azurerm_linux_function_app",
            "azurerm_windows_function_app",
        ):
            model.function_apps.append(
                FunctionApp(name=_s(attrs, "name") or rname, file=block.file, line=block.line)
            )

    # Filesystem attribution is explicit only: a storage_account_id that
    # names an account wins; an unresolvable id stays unattributed unless
    # exactly one account exists (unambiguous by construction).
    for owner, fs_names in filesystems.items():
        match = None
        if owner:
            for key, acc in accounts.items():
                # resource attr form: azurerm_storage_account.<name>.id
                if f"azurerm_storage_account.{key}." in owner or owner.endswith(f"/{acc.name}"):
                    match = key
                    break
        elif len(accounts) == 1:
            match = next(iter(accounts))
        if match is not None:
            accounts[match] = replace(accounts[match], filesystems=tuple(sorted(fs_names)))
    model.adls_accounts.extend(accounts.values())
    model.adls_accounts.sort(key=lambda a: (str(a.file), a.line, a.name))
    model.synapse_workspaces.sort(key=lambda s: (str(s.file), s.line))
    if model.synapse_workspaces and (synapse["sql"] or synapse["spark"]):
        ws = model.synapse_workspaces[0]
        model.synapse_workspaces[0] = replace(
            ws,
            sql_pools=tuple(sorted(synapse["sql"])),
            spark_pools=tuple(sorted(synapse["spark"])),
        )
    for _name, factory in factories.items():
        model.adf_factories.append(
            AdfFactory(
                name=factory.name,
                file=factory.file,
                line=factory.line,
                pipelines=tuple(sorted(pipelines.get("", []))),
            )
        )
    model.adf_factories.sort(key=lambda f: (str(f.file), f.line))
    model.eventhubs.sort(key=lambda e: (str(e.file), e.line))
    model.eventhub_namespaces.sort(key=lambda e: (str(e.file), e.line))
    model.purview_accounts.sort(key=lambda p: (str(p.file), p.line))
    model.fabric_capacities.sort(key=lambda f: (str(f.file), f.line))
    model.function_apps.sort(key=lambda f: (str(f.file), f.line))

    setattr(ctx, _CACHE_ATTR, model)
    return model
