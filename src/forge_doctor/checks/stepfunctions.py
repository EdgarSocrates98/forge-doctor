"""Step Functions checks (SFN###) over the shared StepFunctionsModel."""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge_doctor.analyzers.stepfunctions_model import (
    SfnMachine,
    StepFunctionsModel,
    stepfunctions_model,
)
from forge_doctor.core.models import EvidenceKind, Severity
from forge_doctor.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.models import CheckResult

_REACHABLE_TYPES = {"Task", "Choice", "Map", "Parallel", "Wait", "Pass"}


def _model(ctx: ProjectContext) -> StepFunctionsModel:
    return stepfunctions_model(ctx)


def _reachable(machine: SfnMachine) -> set[str]:
    by_name = {s.name: s for s in machine.states}
    seen: set[str] = set()
    queue = [machine.start_at]
    while queue:
        name = queue.pop()
        if not name or name in seen or name not in by_name:
            continue
        seen.add(name)
        s = by_name[name]
        queue.extend([s.next, s.default, *s.choices, *s.catches])
    return seen


def _machines_with_nested(model: StepFunctionsModel) -> list[SfnMachine]:
    out: list[SfnMachine] = []

    def walk(m: SfnMachine) -> None:
        out.append(m)
        for n in m.nested:
            walk(n)

    for m in model.machines:
        walk(m)
    return out


class _SfnCheck(CheckBase):
    category = "stepfunctions"
    evidence_kind = EvidenceKind.CONFIG


class SfnUsage(_SfnCheck):
    """SFN000: how many state machines / states the project defines."""

    id = "SFN000"
    title = "Step Functions usage"
    why = "Anchor: sizes the orchestration surface feeding the SFN checks."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the counts to size the surface."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.has_machines:
            return [self.result(Severity.PASS, "no Step Functions detected")]
        return [
            self.result(
                Severity.INFO,
                f"{len(model.machines)} machines, {len(model.all_states)} states, "
                f"{len(model.iac_refs)} IaC refs",
            )
        ]


class UnreachableState(_SfnCheck):
    """SFN002: a state never reached from StartAt."""

    id = "SFN002"
    title = "Unreachable state"
    why = "A state outside the StartAt reachability set is dead code - deployed but never executed."
    when_ok = "Every state is reachable via Next/Choices/Default/Catch."
    fix = "Wire the state in or delete it."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        results = []
        for m in _machines_with_nested(_model(ctx)):
            reached = _reachable(m)
            for s in m.states:
                if s.name not in reached:
                    results.append(
                        self.result(
                            Severity.WARNING,
                            f"state '{s.name}' in {m.name} is unreachable "
                            f"from StartAt '{m.start_at}'",
                            file=m.file,
                            line=m.line or None,
                        )
                    )
        return results


class DeadEndState(_SfnCheck):
    """SFN003: non-terminal state with no Next and no End."""

    id = "SFN003"
    title = "Dead-end path"
    why = (
        "A Task/Map/Wait without Next or End=true stops the execution "
        "mid-workflow - anything downstream is unreachable."
    )
    when_ok = "Terminal states are Succeed/Fail, or End=true."
    fix = "Add Next or mark the state End=true."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        results = []
        for m in _machines_with_nested(_model(ctx)):
            for s in m.states:
                if (
                    s.type not in {"Succeed", "Fail"}
                    and not s.next
                    and not s.end
                    and s.type != "Choice"
                ):
                    results.append(
                        self.result(
                            Severity.WARNING,
                            f"state '{s.name}' ({s.type}) in {m.name} has no Next and no End=true",
                            file=m.file,
                            line=m.line or None,
                        )
                    )
        return results


class ChoiceNoDefault(_SfnCheck):
    """SFN005: Choice state without a Default."""

    id = "SFN005"
    title = "Choice without Default"
    why = (
        "When no Choice rule matches and there is no Default, the "
        "execution fails with States.NoChoiceMatched."
    )
    when_ok = "Rules provably cover the input space, or Default exists."
    fix = "Add a Default path or an explicit Fail state."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        results = []
        for m in _machines_with_nested(_model(ctx)):
            for s in m.states:
                if s.type == "Choice" and not s.default:
                    results.append(
                        self.result(
                            Severity.INFO,
                            f"Choice '{s.name}' in {m.name} has no Default "
                            "- unmatched input raises NoChoiceMatched",
                            file=m.file,
                            line=m.line or None,
                        )
                    )
        return results


class SyncTaskNoTimeout(_SfnCheck):
    """SFN010: .sync/.waitForTaskToken task without TimeoutSeconds."""

    id = "SFN010"
    title = "Sync task without timeout"
    why = (
        "A `.sync` or callback task can wait on the remote service "
        "indefinitely; without TimeoutSeconds the state outlives any SLA."
    )
    when_ok = "TimeoutSeconds set on long-polling integrations."
    fix = "Set TimeoutSeconds and pair it with a Catch for States.Timeout."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        results = []
        for m in _machines_with_nested(_model(ctx)):
            for s in m.states:
                if (
                    s.type == "Task"
                    and s.timeout_seconds is None
                    and (".sync" in s.resource or ".waitForTaskToken" in s.resource)
                ):
                    results.append(
                        self.result(
                            Severity.INFO,
                            f"task '{s.name}' in {m.name} uses a sync/callback "
                            "integration with no TimeoutSeconds",
                            file=m.file,
                            line=m.line or None,
                        )
                    )
        return results


class DistributedMapInExpress(_SfnCheck):
    """SFN020: Distributed Map inside an EXPRESS machine."""

    id = "SFN020"
    title = "Distributed Map in Express workflow"
    why = (
        "Express workflows don't support Distributed Map - "
        "ProcessorMode DISTRIBUTED only runs on Standard."
    )
    when_ok = "Machine type is STANDARD (or unset)."
    fix = "Switch the state machine to type STANDARD."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        results = []
        for m in _model(ctx).machines:
            if m.type.upper() != "EXPRESS":
                continue
            for s in m.states:
                if s.map_mode == "DISTRIBUTED":
                    results.append(
                        self.result(
                            Severity.WARNING,
                            f"Map '{s.name}' uses DISTRIBUTED mode but machine {m.name} is EXPRESS",
                            file=m.file,
                            line=m.line or None,
                        )
                    )
        return results


CHECKS: list[Check] = [
    SfnUsage(),
    UnreachableState(),
    DeadEndState(),
    ChoiceNoDefault(),
    SyncTaskNoTimeout(),
    DistributedMapInExpress(),
]
