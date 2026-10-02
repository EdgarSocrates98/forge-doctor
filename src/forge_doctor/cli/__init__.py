"""Compatibility shim: ``forge_doctor.cli:app`` keeps working.

Command modules register on ``app`` at import time; importing them here is
what populates the CLI. ``_snapshot`` is re-exported for the test-suite.
"""

from forge_doctor.cli import (  # noqa: F401 - import-time command registration
    airflow,
    capabilities,
    compatibility,
    contract,
    controlm,
    datamodel,
    diff,
    dynamodb,
    graph,
    iceberg,
    lab,
    lakeformation,
    misc,
    neptune,
    parquet,
    platform,
    platforms,
    plugins,
    remediate,
    rootcause,
    runtime,
    scan,
    serverless,
    stepfunctions,
    streaming,
    streaming_bus,
    terraform,
    whatif,
    workspace,
)
from forge_doctor.cli.app import app
from forge_doctor.cli.common import _snapshot  # noqa: F401

__all__ = ["app"]
