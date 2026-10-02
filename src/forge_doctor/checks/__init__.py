"""Built-in checks, grouped by category module."""

from __future__ import annotations

from types import ModuleType

from forge_doctor.checks import (
    airflow,
    architecture,
    aws,
    bigquery,
    ci,
    controlm,
    datacontract,
    dbt,
    dependencies,
    docker,
    dynamodb,
    git_checks,
    glue,
    graph,
    iac,
    iceberg,
    lakeformation,
    neptune,
    parquet,
    platform_rules,
    platforms,
    policy_pack,
    python_env,
    redshift,
    repository,
    serverless,
    snowflake,
    spark,
    stepfunctions,
    streaming,
    streaming_bus,
    terraform,
    trino,
    warehouse,
)
from forge_doctor.plugins.protocol import Check

BUILTIN_MODULES = (
    repository,
    python_env,
    dependencies,
    git_checks,
    spark,
    aws,
    docker,
    glue,
    ci,
    iac,
    iceberg,
    controlm,
    dbt,
    datacontract,
    airflow,
    terraform,
    parquet,
    stepfunctions,
    streaming,
    streaming_bus,
    lakeformation,
    graph,
    platform_rules,
    dynamodb,
    neptune,
    architecture,
    platforms,
    policy_pack,
    redshift,
    serverless,
    snowflake,
    bigquery,
    warehouse,
    trino,
)


# Optional-extra categories register only when their dependency is present.
def _optional_modules() -> tuple[ModuleType, ...]:
    import importlib.util

    modules = []
    if importlib.util.find_spec("sqlglot") is not None:
        from forge_doctor.checks import sql

        modules.append(sql)
    return tuple(modules)


def builtin_checks() -> list[Check]:
    checks: list[Check] = []
    for module in (*BUILTIN_MODULES, *_optional_modules()):
        checks.extend(module.CHECKS)
    return checks
