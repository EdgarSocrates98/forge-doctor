"""Step Functions (ASL) semantic model - stdlib-only JSON parsing.

Sources: ``*.asl.json``/``*.states.json``/``*.sfn.json``, any ``*.json``
document with ``StartAt`` + ``States``, Terraform ``aws_sfn_state_machine``
definition strings/heredocs, and CFN ``AWS::StepFunctions::StateMachine``
Definition/DefinitionString.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from forge_doctor.analyzers.hcl_lite import _brace_block

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext

_CACHE_ATTR = "_forge_doctor_sfn_model"

_ASL_NAMES = {".asl.json", ".states.json", ".sfn.json"}
_HEREDOC_RE = re.compile(r"<<-?(\w+)\s*\n(.*?)\n\s*\1", re.DOTALL)
_ARN_SVC_RE = re.compile(r"arn:aws:states:::(?:aws-sdk:)?([a-z0-9-]+)")
_ARN_DIRECT_RE = re.compile(r"arn:aws:([a-z0-9-]+):")
_CFN_SFN_RE = re.compile(r"AWS::StepFunctions::StateMachine")


@dataclass(frozen=True)
class SfnState:
    """One ASL state."""

    name: str
    type: str
    next: str  # "" when absent
    end: bool
    default: str  # Choice default, "" when absent
    choices: tuple[str, ...]  # Next targets inside Choices
    catches: tuple[str, ...]  # Catch[].Next targets
    resource: str
    integration: str  # lambda|athena|glue|sns|sqs|dynamodb|sdk:<svc>|""
    timeout_seconds: int | None
    heartbeat_seconds: int | None
    retry_count: int
    catch_count: int
    map_mode: str  # "" | INLINE | DISTRIBUTED
    line: int
    query_language: str = ""  # JSONata|JSONPath|"" (inherit)
    payload_keys: tuple[str, ...] = ()  # InputPath|Parameters|Arguments|Output|...
    retry_max_attempts: int = 0  # summed MaxAttempts across Retry entries
    retry_errors: tuple[str, ...] = ()  # ErrorEquals across Retry entries
    catch_errors: tuple[str, ...] = ()  # ErrorEquals across Catch entries
    max_concurrency: int = 0  # Map MaxConcurrency (0 = unset/default)
    tolerated_failure: float = 0.0  # Map ToleratedFailurePercentage
    target: str = ""  # lambda FunctionName / literal arn tail when resolvable


@dataclass(frozen=True)
class SfnMachine:
    """One state machine definition."""

    name: str
    file: Path
    line: int
    type: str  # STANDARD|EXPRESS|""
    start_at: str
    states: tuple[SfnState, ...]
    nested: tuple[SfnMachine, ...] = ()  # Map Iterator/ItemProcessor bodies
    query_language: str = "JSONPath"  # top-level QueryLanguage (ASL default)


@dataclass
class StepFunctionsModel:
    """All ASL machines + IaC references in a project."""

    machines: list[SfnMachine] = field(default_factory=list)
    iac_refs: list[str] = field(default_factory=list)  # IaC resource addrs

    @property
    def has_machines(self) -> bool:
        return bool(self.machines) or bool(self.iac_refs)

    @property
    def all_states(self) -> list[SfnState]:
        out: list[SfnState] = []

        def walk(m: SfnMachine) -> None:
            out.extend(m.states)
            for n in m.nested:
                walk(n)

        for m in self.machines:
            walk(m)
        return out


def _integration(resource: str) -> str:
    if not resource:
        return ""
    m = _ARN_SVC_RE.search(resource)
    if m:
        family = m.group(1)
        action = resource.rsplit(":", 1)[-1]
        if ".waitForTaskToken" in resource:
            return f"{family}:callback"
        if action.endswith(".sync"):
            return f"{family}:sync"
        return family
    m = _ARN_DIRECT_RE.search(resource)
    return m.group(1) if m else ""


def _states_of(doc: dict[str, Any]) -> tuple[SfnState, ...]:
    states = doc.get("States")
    if not isinstance(states, dict):
        return ()
    out: list[SfnState] = []
    for name, body in states.items():
        if not isinstance(body, dict):
            continue
        choices = tuple(
            str(c.get("Next", ""))
            for c in body.get("Choices", [])
            if isinstance(c, dict) and c.get("Next")
        )
        catches = tuple(
            str(c.get("Next", ""))
            for c in body.get("Catch", [])
            if isinstance(c, dict) and c.get("Next")
        )
        resource = str(body.get("Resource", "") or "")
        map_mode = ""
        max_concurrency = 0
        tolerated_failure = 0.0
        if body.get("Type") == "Map":
            proc = body.get("ItemProcessor") or body.get("Iterator") or {}
            mode = proc.get("ProcessorConfig", {}).get("Mode", "") if isinstance(proc, dict) else ""
            map_mode = str(mode).upper() or "INLINE"
            mc = body.get("MaxConcurrency")
            max_concurrency = int(mc) if isinstance(mc, (int, float)) else 0
            tfail = body.get("ToleratedFailurePercentage")
            tolerated_failure = float(tfail) if isinstance(tfail, (int, float)) else 0.0
        timeout = body.get("TimeoutSeconds")
        heartbeat = body.get("HeartbeatSeconds")
        retries = [r for r in body.get("Retry", []) or [] if isinstance(r, dict)]
        retry_max_attempts = 0
        retry_errors: list[str] = []
        for r in retries:
            ma = r.get("MaxAttempts")
            retry_max_attempts += int(ma) if isinstance(ma, (int, float)) else 1
            retry_errors.extend(str(e) for e in (r.get("ErrorEquals") or []) if isinstance(e, str))
        catch_errors = [
            str(e)
            for c in body.get("Catch", []) or []
            if isinstance(c, dict)
            for e in (c.get("ErrorEquals") or ["States.ALL"])
            if isinstance(e, str)
        ]
        payload_keys = tuple(
            k
            for k in (
                "InputPath",
                "OutputPath",
                "Parameters",
                "ResultSelector",
                "ResultPath",
                "Arguments",
                "Output",
                "Assign",
                "ItemSelector",
                "ItemBatcher",
            )
            if k in body
        )
        target = ""
        params = body.get("Parameters") or body.get("Arguments") or {}
        if isinstance(params, dict):
            for key in ("FunctionName", "FunctionArn", "functionName"):
                v = params.get(key)
                if isinstance(v, str) and not v.endswith(".$"):
                    target = v.rsplit(":", 1)[-1].rsplit("/", 1)[-1]
                    break
        if not target and resource.startswith("arn:aws:lambda:"):
            target = resource.rsplit(":", 1)[-1].rsplit("/", 1)[-1]
        out.append(
            SfnState(
                name=name,
                type=str(body.get("Type", "")),
                next=str(body.get("Next", "") or ""),
                end=bool(body.get("End", False)),
                default=str(body.get("Default", "") or ""),
                choices=choices,
                catches=catches,
                resource=resource,
                integration=_integration(resource),
                timeout_seconds=int(timeout) if isinstance(timeout, (int, float)) else None,
                heartbeat_seconds=int(heartbeat) if isinstance(heartbeat, (int, float)) else None,
                retry_count=len(retries),
                catch_count=len(catches),
                map_mode=map_mode,
                line=0,
                query_language=str(body.get("QueryLanguage", "") or ""),
                payload_keys=payload_keys,
                retry_max_attempts=retry_max_attempts,
                retry_errors=tuple(sorted(set(retry_errors))),
                catch_errors=tuple(sorted(set(catch_errors))),
                max_concurrency=max_concurrency,
                tolerated_failure=tolerated_failure,
                target=target,
            )
        )
    return tuple(out)


def _nested_of(doc: dict[str, Any], file: Path) -> tuple[SfnMachine, ...]:
    nested: list[SfnMachine] = []
    states = doc.get("States")
    if not isinstance(states, dict):
        return ()
    for name, body in states.items():
        if not isinstance(body, dict):
            continue
        inner = body.get("Iterator") or body.get("ItemProcessor")
        if isinstance(inner, dict) and isinstance(inner.get("States"), dict):
            nested.append(
                SfnMachine(
                    name=f"{name}.iterator",
                    file=file,
                    line=0,
                    type="",
                    start_at=str(inner.get("StartAt", "")),
                    states=_states_of(inner),
                    nested=(),
                )
            )
    return tuple(nested)


def _machines_from_json(
    text: str, file: Path, name: str, mtype: str = "", line: int = 0
) -> list[SfnMachine]:
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(doc, dict) or not isinstance(doc.get("States"), dict):
        return []
    qlang = str(doc.get("QueryLanguage", "") or "JSONPath")
    return [
        SfnMachine(
            name=name,
            file=file,
            line=line,
            type=mtype or str(doc.get("Type", "") or ""),
            start_at=str(doc.get("StartAt", "")),
            states=_states_of(doc),
            nested=_nested_of(doc, file),
            query_language=qlang,
        )
    ]


def _definition_json(body: str) -> str:
    """Extract a JSON object containing StartAt from an IaC block body."""
    for m in _HEREDOC_RE.finditer(body):
        if '"StartAt"' in m.group(2) or "'StartAt'" in m.group(2):
            return m.group(2)
    idx = body.find('"StartAt"')
    if idx == -1:
        idx = body.find("'StartAt'")
    if idx == -1:
        return ""
    start = body.rfind("{", 0, idx)
    while start != -1:
        inner, _ = _brace_block(body, start)
        if "StartAt" in inner:
            return body[start : start + len(inner) + 1]
        start = body.rfind("{", 0, start)
    return ""


def stepfunctions_model(ctx: ProjectContext) -> StepFunctionsModel:
    """Build (once, memoized on ctx) the Step Functions model."""
    cached = getattr(ctx, _CACHE_ATTR, None)
    if cached is not None:
        return cast(StepFunctionsModel, cached)

    machines: list[SfnMachine] = []
    iac_refs: list[str] = []
    for relative in sorted(ctx.files):
        text = ctx.read_text(relative)
        if text is None:
            continue

        is_asl = any(relative.name.endswith(n) for n in _ASL_NAMES)
        is_json = relative.suffix.lower() == ".json"
        if is_asl or (is_json and '"StartAt"' in text and '"States"' in text):
            idx = text.find('"StartAt"')
            line = text.count("\n", 0, idx) + 1 if idx != -1 else 0
            machines.extend(_machines_from_json(text, relative, relative.name, line=line))
            continue

        if relative.suffix.lower() == ".tf" and "aws_sfn_state_machine" in text:
            head = re.compile(r'resource\s+"aws_sfn_state_machine"\s+"([a-zA-Z0-9_.-]+)"\s*\{')
            for m in head.finditer(text):
                body, _ = _brace_block(text, m.end() - 1)
                iac_refs.append(m.group(1))
                mtype = ""
                type_match = re.search(r'^\s*type\s*=\s*"([A-Z]+)"', body, re.MULTILINE)
                if type_match:
                    mtype = type_match.group(1)
                raw = _definition_json(body)
                if raw:
                    machines.extend(
                        _machines_from_json(
                            raw,
                            relative,
                            m.group(1),
                            mtype=mtype,
                            line=text.count("\n", 0, m.start()) + 1,
                        )
                    )

        if relative.suffix.lower() in {".yml", ".yaml", ".json"} and _CFN_SFN_RE.search(text):
            raw = _definition_json(text)
            if raw:
                machines.extend(_machines_from_json(raw, relative, relative.name))
            else:
                iac_refs.append(relative.as_posix())

    setattr(ctx, _CACHE_ATTR, StepFunctionsModel(machines=machines, iac_refs=iac_refs))
    return cast(StepFunctionsModel, getattr(ctx, _CACHE_ATTR))
