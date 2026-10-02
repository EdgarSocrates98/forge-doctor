"""Compatibility shim: ``forge_doctor.cli:app`` keeps working.

Command modules register on ``app`` at import time; importing them here is
what populates the CLI. ``_snapshot`` is re-exported for the test-suite.
"""

from forge_doctor.cli import (  # noqa: F401 - import-time command registration
    airflow,
    bench,
    capabilities,
    compatibility,
    contract,
    controlm,
    datamodel,
    diff,
    dynamodb,
    export,
    fix,
    fleet,
    golden,
    graph,
    history,
    iceberg,
    lab,
    lakeformation,
    misc,
    neptune,
    parquet,
    platform,
    platforms,
    plugins,
    policy,
    remediate,
    rootcause,
    runtime,
    scan,
    serverless,
    stepfunctions,
    streaming,
    streaming_bus,
    terraform,
    twin,
    whatif,
    workspace,
)
from forge_doctor.cli.app import app
from forge_doctor.cli.common import _snapshot  # noqa: F401

# Help-panel grouping for the (large) top-level command list. Assigned
# post-registration so command modules stay decoupled; anything not listed
# falls back to the default Commands group.
_PANELS: dict[str, dict[str, str]] = {
    "Scan & findings": dict.fromkeys(
        (
            "scan",
            "diff",
            "checks",
            "explain",
            "trace",
            "diagnose",
            "suppressions",
            "remediate",
            "fix",
            "root-cause",
            "repo",
            "python",
            "dependencies",
            "git",
            "aws",
            "docker",
            "glue",
            "ci",
            "iac",
        ),
        "Scan & findings",
    ),
    "Platform intelligence": dict.fromkeys(
        (
            "platform",
            "graph",
            "data-model",
            "lineage",
            "schema",
            "runtime",
            "airflow",
            "controlm",
            "terraform",
            "parquet",
            "spark",
            "iceberg",
            "delta",
            "emr",
            "databricks",
            "athena",
            "lambda",
            "stepfunctions",
            "streaming",
            "kafka",
            "kinesis",
            "flink",
            "dynamodb",
            "neptune",
            "lakeformation",
        ),
        "Platform intelligence",
    ),
    "Estate & change": dict.fromkeys(
        (
            "workspace",
            "fleet",
            "history",
            "architecture",
            "contract",
            "capabilities",
            "compatibility",
            "migrate",
            "what-if",
            "twin",
        ),
        "Estate & change",
    ),
    "Quality gates & supply chain": dict.fromkeys(
        (
            "lab",
            "golden",
            "bench",
            "policy",
            "plugins",
            "knowledge",
            "sbom",
            "contracts",
            "ontology",
        ),
        "Quality gates & supply chain",
    ),
    "Setup & integrations": dict.fromkeys(
        ("init", "info", "doctor", "cache", "mcp", "lsp", "export", "version"),
        "Setup & integrations",
    ),
}
_PANEL_OF = {name: panel for panel, members in _PANELS.items() for name in members}


def _apply_help_metadata() -> None:
    from typer.models import CommandInfo, TyperInfo

    infos: list[CommandInfo | TyperInfo] = [
        *app.registered_commands,
        *app.registered_groups,
    ]
    for info in infos:
        name = info.name or getattr(info.callback, "__name__", "")
        if name in _PANEL_OF:
            info.rich_help_panel = _PANEL_OF[name]

    # Groups with an ``invoke_without_command`` callback do useful work when
    # invoked bare (e.g. ``history`` lists snapshots) - make that discoverable
    # in ``<group> --help`` instead of hiding it behind a missing-command error.
    for group in app.registered_groups:
        sub = group.typer_instance
        if sub is None:
            continue
        cb = getattr(sub, "registered_callback", None)
        if cb is None or not getattr(cb, "invoke_without_command", False):
            continue
        cb_fn = getattr(cb, "callback", None)
        doc = ((cb_fn.__doc__ or "").strip().splitlines() or [""])[0] if cb_fn else ""
        if doc:
            base = (group.help or getattr(sub.info, "help", "") or "").rstrip()
            # Skip when the callback doc just restates the group help.
            if base.lower().split(":", 1)[0].rstrip(". ") != doc.lower().rstrip(". "):
                group.help = f"{base} Bare: {doc.replace('``', '')}"


_apply_help_metadata()

__all__ = ["app"]
