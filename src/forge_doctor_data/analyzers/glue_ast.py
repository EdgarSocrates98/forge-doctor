"""Static AWS Glue pattern analysis via ``ast`` - never imports target code."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

_GLUE_VERSION_KWARGS = {"glue_version", "glueversion"}
_DEFAULT_ARGUMENTS_KWARGS = {"default_arguments", "defaultarguments"}
_JOB_ADMIN_CALLS = {"create_job", "update_job"}
_CLIENT_FACTORY_NAMES = {"client", "resource"}
_JOB_LIFECYCLE = {"init": "job_init", "commit": "job_commit"}
_GLUE_VERSION_ARG = "--glue-version"


@dataclass(frozen=True)
class GlueFinding:
    """One detected pattern occurrence."""

    pattern: str
    line: int


def uses_glue(tree: ast.Module) -> bool:
    """True when the module imports awsglue or builds a boto3 glue client."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            alias.name.split(".")[0] == "awsglue" for alias in node.names
        ):
            return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "awsglue":
            return True
        if isinstance(node, ast.Call) and _is_glue_factory_call(node):
            return True
    return False


class GlueAnalyzer(ast.NodeVisitor):
    """Collects AWS Glue pattern occurrences with line numbers."""

    def __init__(self) -> None:
        self.findings: list[GlueFinding] = []

    def analyze_source(self, source: str, path: Path) -> list[GlueFinding]:
        self.findings = []
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            return []
        if not uses_glue(tree):
            return []
        self.visit(tree)
        return list(self.findings)

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node)
        if name == "getresolvedoptions":
            self._add("get_resolved_options", node)
        elif name == "fromdf":
            self._add("fromDF", node)
        elif name == "todf":
            self._add("toDF", node)
        lifecycle = _job_lifecycle(node)
        if lifecycle is not None:
            self._add(lifecycle, node)
        self._check_glue_version(node, name)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id == "DynamicFrame":
            self._add("dynamic_frame", node)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.asname == "DynamicFrame":
                self._add("dynamic_frame", node)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "DynamicFrame" or alias.asname == "DynamicFrame":
                self._add("dynamic_frame", node)
        self.generic_visit(node)

    def _check_glue_version(self, node: ast.Call, name: str | None) -> None:
        """Flag pinned runtime strings on ``glue_version``/``GlueVersion`` kwargs
        and ``--glue-version`` entries in ``DefaultArguments``-style dicts."""
        for keyword in node.keywords:
            arg = (keyword.arg or "").lower()
            if arg in _GLUE_VERSION_KWARGS:
                self._add_version(keyword.value)
            elif isinstance(keyword.value, ast.Dict) and (
                arg in _DEFAULT_ARGUMENTS_KWARGS or name in _JOB_ADMIN_CALLS
            ):
                self._scan_dict(keyword.value)
        if name in _JOB_ADMIN_CALLS:
            for positional in node.args:
                if isinstance(positional, ast.Dict):
                    self._scan_dict(positional)

    def _scan_dict(self, node: ast.Dict) -> None:
        for key, value in zip(node.keys, node.values, strict=True):
            if _string_constant(key) == _GLUE_VERSION_ARG:
                self._add_version(value)
            elif isinstance(value, ast.Dict):
                self._scan_dict(value)

    def _add_version(self, node: ast.expr) -> None:
        from forge_doctor_data.core.knowledge import glue_versions

        version = _string_constant(node)
        if version is not None and (version in glue_versions() or not glue_versions()):
            self._add(f"glue_version:{version}", node)

    def _add(self, pattern: str, node: ast.expr | ast.stmt) -> None:
        self.findings.append(GlueFinding(pattern=pattern, line=node.lineno))


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr.lower()
    if isinstance(func, ast.Name):
        return func.id.lower()
    return None


def _job_lifecycle(node: ast.Call) -> str | None:
    """``job.init(...)``/``job.commit(...)`` - Glue job lifecycle anchors."""
    func = node.func
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "job"
    ):
        return _JOB_LIFECYCLE.get(func.attr)
    return None


def _is_glue_factory_call(node: ast.Call) -> bool:
    """``boto3.client("glue")``/``boto3.resource("glue")``-style calls."""
    if _call_name(node) not in _CLIENT_FACTORY_NAMES:
        return False
    return bool(node.args) and _string_constant(node.args[0]) == "glue"


def _string_constant(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None
