"""Built-in checks, grouped by category module."""

from __future__ import annotations

from types import ModuleType

from forge_doctor.checks import (
    airflow,
    aws,
    ci,
    controlm,
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
    python_env,
    repository,
    spark,
    stepfunctions,
    streaming,
    terraform,
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
    airflow,
    terraform,
    parquet,
    stepfunctions,
    streaming,
    lakeformation,
    graph,
    dynamodb,
    neptune,
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
