"""Airflow checks (AIR###) over the shared AirflowModel - never re-parse."""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge_doctor.analyzers.airflow_model import (
    AirflowModel,
    airflow_model,
)
from forge_doctor.core.models import Severity
from forge_doctor.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.models import CheckResult


def _model(ctx: ProjectContext) -> AirflowModel:
    return airflow_model(ctx)


class _AirflowCheck(CheckBase):
    category = "airflow"


class AirflowUsage(_AirflowCheck):
    """AIR000: how much of the project is Airflow."""

    id = "AIR000"
    title = "Airflow usage"
    why = "Anchor: sizes the Airflow surface feeding the other AIR checks."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the counts to size the orchestration surface."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.has_airflow:
            return [self.result(Severity.PASS, "no Airflow usage detected")]
        sensors = sum(1 for t in model.tasks if t.is_sensor)
        parts = [
            f"{len(model.dags)} dags",
            f"{len(model.tasks)} tasks ({sensors} sensors)",
            f"{len(model.edges)} dependencies",
        ]
        if model.providers:
            parts.append(f"providers: {', '.join(sorted(model.providers))}")
        if model.parse_calls or model.variable_gets:
            parts.append(f"{len(model.parse_calls) + len(model.variable_gets)} parse-time calls")
        return [self.result(Severity.INFO, ", ".join(parts))]


class DuplicateDagId(_AirflowCheck):
    """AIR002: the same dag_id is defined more than once."""

    id = "AIR002"
    title = "Duplicate dag_id"
    why = "Two DAGs with the same dag_id collide; one silently wins at load."
    when_ok = "Each dag_id is unique across the project."
    fix = "Rename one of the duplicated DAG definitions."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        by_id: dict[str, list[tuple[str, int]]] = {}
        for dag in model.dags:
            if dag.dag_id:
                by_id.setdefault(dag.dag_id, []).append((dag.file.as_posix(), dag.line))
        return [
            self.result(
                Severity.WARNING,
                f"dag_id '{dag_id}' defined {len(sites)} times "
                f"({', '.join(f'{f}:{ln}' for f, ln in sites)})",
            )
            for dag_id, sites in sorted(by_id.items())
            if len(sites) > 1
        ]


class EmptyDag(_AirflowCheck):
    """AIR003: a DAG declares no tasks."""

    id = "AIR003"
    title = "DAG with no tasks"
    why = "An empty DAG does nothing but still costs a scheduler slot."
    when_ok = "Intentional placeholder DAG."
    fix = "Add tasks or remove the DAG."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.WARNING,
                f"dag '{dag.dag_id or dag.var}' declares no tasks",
                file=dag.file,
                line=dag.line,
                evidence=self.evidence_at(ctx, dag.file, dag.line),
            )
            for dag in _model(ctx).dags
            if dag.task_count == 0
        ]


class OrphanTask(_AirflowCheck):
    """AIR004: task never wired into its DAG's dependency graph."""

    id = "AIR004"
    title = "Orphan task"
    why = "An unwired task sits in the DAG but runs immediately with no ordering."
    when_ok = "Intentional root task; or wiring happens via TaskFlow returns."
    fix = "Wire the task with >>/set_upstream, or remove it."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        wired = model.wired_names
        counts: dict[tuple[str, str], int] = {}
        for task in model.tasks:
            key = (task.file.as_posix(), task.dag)
            counts[key] = counts.get(key, 0) + 1
        results: list[CheckResult] = []
        for task in model.tasks:
            if task.var in wired or task.task_id in wired:
                continue
            if counts.get((task.file.as_posix(), task.dag), 0) <= 1:
                continue
            results.append(
                self.result(
                    Severity.INFO,
                    f"task '{task.task_id or task.var}' is never wired into a dependency",
                    file=task.file,
                    line=task.line,
                    evidence=self.evidence_at(ctx, task.file, task.line),
                )
            )
        return results


class DynamicStartDate(_AirflowCheck):
    """AIR013: start_date computed at parse time (datetime.now() etc.)."""

    id = "AIR013"
    title = "Dynamic start_date"
    why = "start_date evaluated at parse drifts per run and breaks scheduling."
    when_ok = "start_date is a fixed instant (pendulum/datetime literal)."
    fix = "Use a constant start_date (e.g. datetime(2024, 1, 1))."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.WARNING,
                f"dag '{dag.dag_id or dag.var}' computes start_date at parse time",
                file=dag.file,
                line=dag.line,
                evidence=self.evidence_at(ctx, dag.file, dag.line),
            )
            for dag in _model(ctx).dags
            if dag.start_date_dynamic
        ]


class ParseTimeExternalCall(_AirflowCheck):
    """AIR021: network/client call executed at DAG parse time."""

    id = "AIR021"
    title = "External call at parse time"
    why = "Module-level calls run on every scheduler parse pass - slow + fragile."
    when_ok = "Call inside a task body/operator, not at module scope."
    fix = "Move the call into a task; use Variables/connections lazily."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.WARNING,
                f"'{root}' call executes at DAG parse time",
                file=file,
                line=line,
                evidence=self.evidence_at(ctx, file, line),
            )
            for root, file, line in _model(ctx).parse_calls
        ]


class ParseTimeVariableGet(_AirflowCheck):
    """AIR025: Variable.get() at parse time hits the metadata DB per parse."""

    id = "AIR025"
    title = "Variable.get at parse time"
    why = "Top-level Variable.get() queries the metastore on every DAG parse."
    when_ok = "Fetched inside a task or via Jinja templating."
    fix = "Use {{ var.value.x }} templates or fetch inside the task."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.WARNING,
                "Variable.get() at module scope hits the metadata DB per parse",
                file=file,
                line=line,
                evidence=self.evidence_at(ctx, file, line),
            )
            for file, line in _model(ctx).variable_gets
        ]


class SensorPokeMode(_AirflowCheck):
    """AIR040: sensor runs in poke mode, holding a worker slot."""

    id = "AIR040"
    title = "Sensor in poke mode"
    why = "Poke-mode sensors occupy a worker slot while waiting."
    when_ok = "Short waits; or no deferrable variant exists in the provider."
    fix = "Set deferrable=True where the provider supports it."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.INFO,
                f"sensor '{task.task_id or task.var}' runs in poke mode (no deferrable=True)",
                file=task.file,
                line=task.line,
                evidence=self.evidence_at(ctx, task.file, task.line),
            )
            for task in _model(ctx).tasks
            if task.is_sensor and not task.deferrable
        ]


class SensorNoTimeout(_AirflowCheck):
    """AIR042: sensor without timeout can wait forever."""

    id = "AIR042"
    title = "Sensor without timeout"
    why = "A sensor with no timeout holds its slot indefinitely on a stuck dep."
    when_ok = "A global default timeout is configured on the deployment."
    fix = "Set timeout/execution_timeout on the sensor."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.INFO,
                f"sensor '{task.task_id or task.var}' has no timeout",
                file=task.file,
                line=task.line,
                evidence=self.evidence_at(ctx, task.file, task.line),
            )
            for task in _model(ctx).tasks
            if task.is_sensor and not task.has_timeout
        ]


class RetriesNoDelay(_AirflowCheck):
    """AIR100: retries>0 with no retry_delay configured."""

    id = "AIR100"
    title = "Retries without retry_delay"
    why = "Immediate retries hammer a dependency that just failed."
    when_ok = "Retries disabled, or a sane default_retry_delay is set on the DAG."
    fix = "Add retry_delay (and consider exponential_backoff)."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        results: list[CheckResult] = []
        for task in _model(ctx).tasks:
            try:
                retries = int(task.retries)
            except (TypeError, ValueError):
                continue
            if retries > 0 and not task.has_retry_delay:
                results.append(
                    self.result(
                        Severity.INFO,
                        f"task '{task.task_id or task.var}' retries={retries} with no retry_delay",
                        file=task.file,
                        line=task.line,
                        evidence=self.evidence_at(ctx, task.file, task.line),
                    )
                )
        return results


class UndeclaredProvider(_AirflowCheck):
    """AIR130: an imported provider has no matching declared dependency."""

    id = "AIR130"
    title = "Imported provider not declared"
    why = "airflow.providers.<x> imports fail at runtime when the package is missing."
    when_ok = "Provider installed transitively by another requirement."
    fix = "Add the apache-airflow-providers-<x> dependency explicitly."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.providers or ctx.pyproject is None:
            return []
        deps: set[str] = set()

        def _collect(value: object) -> None:
            if isinstance(value, str):
                # PEP 508: strip extras, markers, version pins; normalize _/-.
                name = value.split("[")[0].split(" ")[0].split(";")[0]
                name = name.split("=")[0].split("<")[0].split(">")[0].split("!")[0]
                deps.add(name.lower().replace("_", "-"))
            elif isinstance(value, dict):
                for v in value.values():
                    _collect(v)
            elif isinstance(value, list):
                for v in value:
                    _collect(v)

        project = ctx.pyproject.get("project", {})
        _collect(project.get("dependencies", []))
        _collect(project.get("optional-dependencies", {}))
        results: list[CheckResult] = []
        for provider, file in sorted(model.providers.items()):
            package = f"apache-airflow-providers-{provider.lower()}"
            if package not in deps and "apache-airflow-providers" not in deps:
                results.append(
                    self.result(
                        Severity.WARNING,
                        f"imports airflow.providers.{provider} but "
                        f"'{package}' is not declared in pyproject",
                        file=file,
                        line=1,
                    )
                )
        return results


CHECKS: list[Check] = [
    AirflowUsage(),
    DuplicateDagId(),
    EmptyDag(),
    OrphanTask(),
    DynamicStartDate(),
    ParseTimeExternalCall(),
    ParseTimeVariableGet(),
    SensorPokeMode(),
    SensorNoTimeout(),
    RetriesNoDelay(),
    UndeclaredProvider(),
]
