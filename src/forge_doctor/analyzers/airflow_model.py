"""Airflow project model - DAG/task/scheduling evidence, static and offline.

Follows V11: one model per scan; every AIR### check and the ``airflow``
command group query it - no per-check parsing. The semantic index flags
airflow files (imports of ``airflow*``); each flagged file gets ONE targeted
``ast.parse`` because edges (``>>``, ``chain``, ``set_downstream``) and
non-string kwargs (``catchup=False``, ``start_date=datetime.now()``) are not
capturable from ``CallSite`` alone.

Never imports or executes target code (V2); fully offline (V9).
"""

from __future__ import annotations

import ast
import itertools
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext

_CACHE_ATTR = "_forge_doctor_airflow_model"

# Call names that make a start_date / schedule argument runtime-computed.
_DYNAMIC_DATE_CALLS = {"now", "today", "utcnow", "days_ago", "get_current_date"}

_DEFAULT_PARSE_MODULES = (
    "requests",
    "urllib",
    "httpx",
    "boto3",
    "botocore",
    "psycopg2",
    "sqlalchemy",
    "paramiko",
    "subprocess",
)

_DEFAULT_SENSORS = (
    "Sensor",
    "ExternalTaskSensor",
    "TimeSensor",
    "TimeDeltaSensor",
    "DateTimeSensor",
)


def _objects_pack() -> dict[str, Any]:
    from forge_doctor.core.knowledge import load_pack

    return load_pack("airflow", "objects")


def pack_list(key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = _objects_pack().get(key)
    if isinstance(value, list):
        return tuple(str(v) for v in value)
    return default


def parse_time_modules() -> frozenset[str]:
    return frozenset(pack_list("parse_time_modules", _DEFAULT_PARSE_MODULES))


def sensor_names() -> frozenset[str]:
    return frozenset(pack_list("sensor_types", _DEFAULT_SENSORS))


@dataclass(frozen=True)
class AirflowDag:
    dag_id: str
    var: str  # python binding (with-as target / assigned name / func name)
    file: Path
    line: int
    schedule: str
    catchup: bool | None
    start_date_dynamic: bool
    max_active_runs: str
    task_count: int = 0
    default_retries: str = ""  # retries from default_args when literal


@dataclass(frozen=True)
class AirflowTask:
    task_id: str
    var: str
    operator: str  # class name, or "@task" for TaskFlow
    file: Path
    line: int
    dag: str  # bound dag var/dag_id, "" when unbound
    is_sensor: bool
    deferrable: bool
    has_timeout: bool
    retries: str
    has_retry_delay: bool
    wired: bool
    target: str = ""  # external resource name for orchestrating operators


@dataclass(frozen=True)
class AirflowEdge:
    src: str
    dst: str
    file: Path
    line: int


@dataclass
class AirflowModel:
    """Everything the project evidences about Airflow, sorted."""

    files: list[Path] = field(default_factory=list)
    dags: list[AirflowDag] = field(default_factory=list)
    tasks: list[AirflowTask] = field(default_factory=list)
    edges: list[AirflowEdge] = field(default_factory=list)
    providers: dict[str, Path] = field(default_factory=dict)
    # (dotted call root, file, line) for module-scope external calls.
    parse_calls: list[tuple[str, Path, int]] = field(default_factory=list)
    variable_gets: list[tuple[Path, int]] = field(default_factory=list)

    @property
    def has_airflow(self) -> bool:
        return bool(self.dags or self.tasks or self.providers)

    @property
    def wired_names(self) -> set[str]:
        names: set[str] = set()
        for edge in self.edges:
            names.add(edge.src)
            names.add(edge.dst)
        return names


def _dotted(node: ast.expr) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Call):
        return _dotted(node.func) + "(...)"
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _root(node: ast.expr) -> str:
    while isinstance(node, ast.Attribute):
        node = node.value
    if isinstance(node, ast.Call):
        return _root(node.func)
    return node.id if isinstance(node, ast.Name) else ""


def _leaf_names(node: ast.expr) -> list[str]:
    """Operand names of a ``>>`` expression (lists expand)."""
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return [n for elt in node.elts for n in _leaf_names(elt)]
    if isinstance(node, ast.Call):
        return []  # `expand()`/mapped results - no static name
    if isinstance(node, ast.Attribute):
        return [_dotted(node).rsplit(".", 1)[-1]]
    return []


def _kwargs(call: ast.Call) -> dict[str, ast.expr]:
    return {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}


def _lit(value: ast.expr) -> str:
    if isinstance(value, ast.Constant):
        return str(value.value)
    return ""


# Operator class -> kwargs naming the external resource it invokes. Used
# for orchestration->compute edges (cross-domain rules, platform graph).
_OPERATOR_TARGET_KWARGS: dict[str, tuple[str, ...]] = {
    "GlueJobOperator": ("job_name",),
    "GlueJobRunTrigger": ("job_name",),
    "LambdaInvokeFunctionOperator": ("function_name",),
    "LambdaInvokeAsyncOperator": ("function_name",),
    "StepFunctionStartExecutionOperator": ("state_machine_arn", "state_machine_name"),
    "EmrAddStepsOperator": ("job_flow_id",),
    "EmrServerlessStartJobOperator": ("application_id",),
    "DatabricksRunNowOperator": ("job_id",),
    "DatabricksSubmitRunOperator": ("notebook_task",),
    "AthenaOperator": ("query",),
}


def _operator_target(operator: str, kws: dict[str, ast.expr]) -> str:
    for key in _OPERATOR_TARGET_KWARGS.get(operator, ()):
        if key in kws:
            value = _lit(kws[key])
            if value:
                return value
    return ""


def _default_args_retries(value: ast.expr | None) -> str:
    """Literal ``retries`` inside a ``default_args={...}`` dict."""
    if not isinstance(value, ast.Dict):
        return ""
    for key, item in zip(value.keys, value.values, strict=True):
        if isinstance(key, ast.Constant) and key.value == "retries":
            return _lit(item)
    return ""


def _truthy(value: ast.expr) -> bool | None:
    if isinstance(value, ast.Constant) and isinstance(value.value, bool):
        return value.value
    return None


def _dynamic_date(value: ast.expr) -> bool:
    """True when the expression computes a date at parse time."""
    for node in ast.walk(value):
        if isinstance(node, ast.Call):
            name = _dotted(node.func).rsplit(".", 1)[-1].lower()
            if name in _DYNAMIC_DATE_CALLS:
                return True
    return False


def _is_dag_call(node: ast.expr) -> bool:
    if not isinstance(node, ast.Call):
        return False
    name = _dotted(node.func)
    return name == "dag" or name.endswith(".dag") or name.split("(", 1)[0].endswith("DAG")


def _is_taskflow_decorator(node: ast.expr) -> bool:
    target = node.func if isinstance(node, ast.Call) else node
    name = _dotted(target)
    return name == "task" or name.startswith("task.")


def _is_operator_call(node: ast.expr, sensors: frozenset[str]) -> str | None:
    """Operator class name when ``node`` instantiates one."""
    if not isinstance(node, ast.Call):
        return None
    name = _dotted(node.func).rsplit(".", 1)[-1]
    if name.endswith("Operator") or name.endswith("Sensor") or name in sensors:
        return name
    return None


class _FileWalk:
    """One-pass AST walk over a single airflow-flagged file."""

    def __init__(self, file: Path, text: str, sensors: frozenset[str]) -> None:
        self.file = file
        self.text = text
        self.sensors = sensors
        self.dags: list[AirflowDag] = []
        self.tasks: list[AirflowTask] = []
        self.edges: list[AirflowEdge] = []
        self.parse_calls: list[tuple[str, Path, int]] = []
        self.variable_gets: list[tuple[Path, int]] = []
        self.taskflow_defs: dict[str, AirflowTask] = {}
        self._dag_body_calls: dict[str, set[str]] = {}
        self._module_calls: set[str] = set()
        self._edge_seen: set[tuple[str, str]] = set()
        self._parse_seen: set[tuple[str, int]] = set()
        self._dag_vars: dict[str, str] = {}  # var -> dag_id
        # Aliased imports: `from airflow import DAG as Dag` /
        # `from airflow.decorators import dag as my_dag`.
        self._dag_ctor_aliases: set[str] = set()
        self._dag_deco_aliases: set[str] = set()

    def run(self, tree: ast.Module) -> None:
        for stmt in tree.body:
            if isinstance(stmt, ast.ImportFrom) and (stmt.module or "").startswith("airflow"):
                for alias in stmt.names:
                    if alias.name == "DAG":
                        self._dag_ctor_aliases.add(alias.asname or "DAG")
                    elif alias.name == "dag":
                        self._dag_deco_aliases.add(alias.asname or "dag")
        for stmt in tree.body:
            self._top_level(stmt)
        # TaskFlow funcs are wired when called; a call inside a @dag body
        # also binds the task to that dag.
        for name, task in self.taskflow_defs.items():
            bound = next((d for d, calls in self._dag_body_calls.items() if name in calls), "")
            self.tasks.append(
                replace(task, wired=bool(bound) or name in self._all_calls(), dag=bound)
            )
        self._link_dags()

    def _all_calls(self) -> set[str]:
        out: set[str] = set(self._module_calls)
        for calls in self._dag_body_calls.values():
            out.update(calls)
        return out

    # -- module-scope statements ------------------------------------------
    def _top_level(self, stmt: ast.stmt) -> None:
        if isinstance(stmt, ast.With):
            self._with(stmt, "")
            return
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            self._function(stmt)
            return
        if isinstance(stmt, ast.ClassDef):
            return
        for node in ast.walk(stmt):
            self._generic(node, dag="")
            self._parse_time_call(node)
            if isinstance(node, ast.Call):
                root = _root(node.func)
                if root:
                    self._module_calls.add(root)

    def _function(self, func: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        is_dag = False
        for deco in func.decorator_list:
            target = deco.func if isinstance(deco, ast.Call) else deco
            name = _dotted(target).rsplit(".", 1)[-1]
            if name == "dag" or _dotted(target).endswith(".dag") or name in self._dag_deco_aliases:
                kws = _kwargs(deco) if isinstance(deco, ast.Call) else {}
                self._dag(
                    var=func.name,
                    line=func.lineno,
                    kws=kws,
                    fallback_id=func.name,
                    call=deco if isinstance(deco, ast.Call) else None,
                )
                is_dag = True
            elif _is_taskflow_decorator(deco):
                kws = _kwargs(deco) if isinstance(deco, ast.Call) else {}
                self.taskflow_defs[func.name] = self._task(
                    var=func.name,
                    operator="@task",
                    line=func.lineno,
                    dag="",
                    call=deco if isinstance(deco, ast.Call) else None,
                    kws=kws,
                )
                return
        body_calls: set[str] = set()
        for node in ast.walk(func):
            if node is func:
                continue
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                # Nested @task defs inside a @dag body are TaskFlow tasks.
                for deco in node.decorator_list:
                    if _is_taskflow_decorator(deco):
                        kws = _kwargs(deco) if isinstance(deco, ast.Call) else {}
                        self.taskflow_defs[node.name] = self._task(
                            var=node.name,
                            operator="@task",
                            line=node.lineno,
                            dag="",
                            call=deco if isinstance(deco, ast.Call) else None,
                            kws=kws,
                        )
                        break
                continue
            if isinstance(node, ast.Call):
                root = _root(node.func)
                if root:
                    body_calls.add(root)
            self._generic(node, dag=func.name if is_dag else "")
        if is_dag:
            self._dag_body_calls[func.name] = body_calls

    def _dag_call(self, node: ast.expr) -> bool:
        if _is_dag_call(node):
            return True
        return isinstance(node, ast.Call) and _dotted(node.func) in self._dag_ctor_aliases

    def _with(self, stmt: ast.With, outer_dag: str) -> None:
        dag = outer_dag
        for item in stmt.items:
            expr = item.context_expr
            if self._dag_call(expr) and isinstance(expr, ast.Call):
                var = item.optional_vars.id if isinstance(item.optional_vars, ast.Name) else ""
                dag = self._dag(
                    var=var,
                    line=expr.lineno,
                    kws=_kwargs(expr),
                    fallback_id=var,
                    call=expr,
                )
        for inner in stmt.body:
            if isinstance(inner, ast.With):
                self._with(inner, dag)
            else:
                for node in ast.walk(inner):
                    self._generic(node, dag=dag)
                    self._parse_time_call(node)
                    if isinstance(node, ast.Call):
                        root = _root(node.func)
                        if root:
                            self._module_calls.add(root)

    # -- node handling -----------------------------------------------------
    def _generic(self, node: ast.AST, dag: str) -> None:
        if isinstance(node, ast.Assign):
            self._assign(node, dag)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.RShift | ast.LShift):
            self._edge_chain(node)
        elif isinstance(node, ast.Call):
            func_name = _dotted(node.func).rsplit(".", 1)[-1]
            if func_name in {"set_upstream", "set_downstream", "set_left", "set_right"}:
                self._dep_call(node, func_name)
            elif func_name == "chain":
                self._chain(node)
            elif func_name == "cross_downstream":
                self._cross(node)

    def _assign(self, node: ast.Assign, dag: str) -> None:
        value = node.value
        target = node.targets[0]
        var = target.id if isinstance(target, ast.Name) else ""
        if self._dag_call(value) and isinstance(value, ast.Call):
            self._dag(var=var, line=value.lineno, kws=_kwargs(value), fallback_id=var, call=value)
            return
        op = _is_operator_call(value, self.sensors)
        if op is not None and isinstance(value, ast.Call):
            kws = _kwargs(value)
            dag_kw = kws.get("dag")
            if isinstance(dag_kw, ast.Name):
                bound = dag_kw.id
            elif dag_kw is not None:
                bound = _lit(dag_kw)
            else:
                bound = ""
            task = self._task(
                var=var,
                operator=op,
                line=value.lineno,
                dag=dag or bound,
                call=value,
                kws=kws,
            )
            self.tasks.append(task)

    def _dag(
        self,
        var: str,
        line: int,
        kws: dict[str, ast.expr],
        fallback_id: str,
        call: ast.Call | None = None,
    ) -> str:
        dag_id = _lit(kws["dag_id"]) if "dag_id" in kws else ""
        if not dag_id and call is not None and call.args:
            dag_id = _lit(call.args[0])
        dag_id = dag_id or fallback_id
        schedule = ""
        for key in ("schedule", "schedule_interval", "timetable"):
            if key in kws:
                raw = kws[key]
                schedule = _lit(raw) or ast.dump(raw)[:60]
                break
        dag = AirflowDag(
            dag_id=dag_id,
            var=var,
            file=self.file,
            line=line,
            schedule=schedule,
            catchup=_truthy(kws["catchup"]) if "catchup" in kws else None,
            start_date_dynamic=_dynamic_date(kws["start_date"]) if "start_date" in kws else False,
            max_active_runs=_lit(kws.get("max_active_runs", ast.Constant(value=""))),
            default_retries=_default_args_retries(kws.get("default_args")),
        )
        self.dags.append(dag)
        if var:
            self._dag_vars[var] = dag_id
        return dag_id or var

    def _task(
        self,
        var: str,
        operator: str,
        line: int,
        dag: str,
        call: ast.Call | None,
        kws: dict[str, ast.expr],
    ) -> AirflowTask:
        task_id = _lit(kws["task_id"]) if "task_id" in kws else var
        deferrable = _truthy(kws["deferrable"]) if "deferrable" in kws else None
        return AirflowTask(
            task_id=task_id,
            var=var,
            operator=operator,
            file=self.file,
            line=line,
            dag=dag,
            is_sensor=operator.endswith("Sensor") or operator in self.sensors,
            deferrable=bool(deferrable),
            has_timeout=bool({"timeout", "execution_timeout"} & set(kws)),
            retries=_lit(kws.get("retries", ast.Constant(value=""))),
            has_retry_delay="retry_delay" in kws,
            wired=False,
            target=_operator_target(operator, kws),
        )

    def _edge_chain(self, node: ast.BinOp) -> None:
        """Decompose ``a >> b >> c`` / ``a << b << c`` shift chains.

        ``>>``/``<<`` both evaluate to the right operand, so a nested left
        chain contributes its inner edges and its right operand's names as
        the continuation point. Nested BinOps are walked again by the
        visitor - ``_edge_seen`` dedupes.
        """
        left, right = node.left, node.right
        if isinstance(left, ast.BinOp) and isinstance(left.op, ast.RShift | ast.LShift):
            tail = _leaf_names(left.right)
        else:
            tail = _leaf_names(left)
        if isinstance(node.op, ast.RShift):
            srcs, dsts = tail, _leaf_names(right)
        else:  # LShift: a << b means b runs before a.
            srcs, dsts = _leaf_names(right), tail
        for src in srcs:
            for dst in dsts:
                if (src, dst) in self._edge_seen:
                    continue
                self._edge_seen.add((src, dst))
                self.edges.append(AirflowEdge(src, dst, self.file, node.lineno))

    def _dep_call(self, node: ast.Call, func_name: str) -> None:
        receiver = _root(node.func)
        targets = [n for arg in node.args for n in _leaf_names(arg)]
        for t in targets:
            if func_name in {"set_downstream", "set_right"}:
                self.edges.append(AirflowEdge(receiver, t, self.file, node.lineno))
            else:
                self.edges.append(AirflowEdge(t, receiver, self.file, node.lineno))

    def _chain(self, node: ast.Call) -> None:
        names = [n for arg in node.args for n in _leaf_names(arg)]
        for src, dst in itertools.pairwise(names):
            self.edges.append(AirflowEdge(src, dst, self.file, node.lineno))

    def _cross(self, node: ast.Call) -> None:
        if len(node.args) < 2:
            return
        srcs = _leaf_names(node.args[0])
        dsts = _leaf_names(node.args[1])
        for src in srcs:
            for dst in dsts:
                self.edges.append(AirflowEdge(src, dst, self.file, node.lineno))

    def _parse_time_call(self, node: ast.AST) -> None:
        if not isinstance(node, ast.Call):
            return
        dotted = _dotted(node.func)
        root = dotted.split(".", 1)[0]
        if root in {"DAG", "dag"} or root in self._dag_vars:
            return
        if dotted in {"Variable.get", "Variable.setdefault"}:
            key = ("variable", node.lineno)
            if key not in self._parse_seen:
                self._parse_seen.add(key)
                self.variable_gets.append((self.file, node.lineno))
        elif root in parse_time_modules() and (root, node.lineno) not in self._parse_seen:
            self._parse_seen.add((root, node.lineno))
            self.parse_calls.append((root, self.file, node.lineno))

    # -- dag <-> task linkage ---------------------------------------------
    def _link_dags(self) -> None:
        counts: dict[str, int] = {}
        for task in self.tasks:
            bound = self._dag_vars.get(task.dag, task.dag)
            if bound:
                counts[bound] = counts.get(bound, 0) + 1
        self.dags = [
            replace(dag, task_count=counts.get(dag.dag_id or dag.var, 0)) for dag in self.dags
        ]


def _airflow_files(ctx: ProjectContext) -> list[Path]:
    from forge_doctor.analyzers.index import project_index

    index = project_index(ctx)
    files: list[Path] = []
    for relative, module in sorted(index.modules.items(), key=lambda kv: kv[0].as_posix()):
        if any(
            imp.module == "airflow" or imp.module.startswith("airflow.") for imp in module.imports
        ):
            files.append(relative)
        else:
            for imported in module.imports:
                if imported.is_from and imported.module.split(".")[0] == "airflow":
                    files.append(relative)
                    break
    return files


def airflow_model(ctx: ProjectContext) -> AirflowModel:
    """Build (once, memoized on ctx) the project's Airflow model."""
    cached = getattr(ctx, _CACHE_ATTR, None)
    if cached is not None:
        return cast(AirflowModel, cached)

    model = AirflowModel()
    sensors = sensor_names()
    for relative in _airflow_files(ctx):
        text = ctx.read_text(relative)
        if text is None:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        walk = _FileWalk(relative, text, sensors)
        walk.run(tree)
        model.dags.extend(walk.dags)
        model.tasks.extend(walk.tasks)
        model.edges.extend(walk.edges)
        model.parse_calls.extend(walk.parse_calls)
        model.variable_gets.extend(walk.variable_gets)
        if walk.dags or walk.tasks:
            model.files.append(relative)

    # Provider imports come straight from the index (no re-parse needed).
    from forge_doctor.analyzers.index import project_index

    index = project_index(ctx)
    for relative, module in index.modules.items():
        for imp in module.imports:
            if imp.module.startswith("airflow.providers."):
                segment = imp.module.split(".")[2]
                model.providers.setdefault(segment, relative)

    model.dags.sort(key=lambda d: (d.file.as_posix(), d.line, d.dag_id))
    model.tasks.sort(key=lambda t: (t.file.as_posix(), t.line, t.var))
    model.edges.sort(key=lambda e: (e.file.as_posix(), e.line, e.src, e.dst))
    model.files.sort(key=lambda p: p.as_posix())
    setattr(ctx, _CACHE_ATTR, model)
    return model
