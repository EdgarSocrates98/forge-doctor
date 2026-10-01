"""Control-M checks (CTM###) over the shared ControlMModel - never re-parse."""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge_doctor.analyzers.controlm_model import (
    ControlMModel,
    controlm_model,
    required_metadata,
)
from forge_doctor.core.models import EvidenceKind, Severity
from forge_doctor.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.models import CheckResult


def _model(ctx: ProjectContext) -> ControlMModel:
    return controlm_model(ctx)


class _ControlMCheck(CheckBase):
    category = "controlm"
    evidence_kind = EvidenceKind.CONFIG


class ControlMUsage(_ControlMCheck):
    """CTM000: how much of the project touches Control-M."""

    id = "CTM000"
    title = "Control-M usage"
    why = "Anchor: sizes the Control-M surface feeding the other CTM checks."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the counts to size the orchestration surface."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.has_controlm:
            return [self.result(Severity.PASS, "no Control-M usage detected")]
        parts = [
            f"{len(model.jobs)} jobs",
            f"{len(model.folders)} folders",
            f"{len(model.events_produced)} events produced",
            f"{len(model.events_consumed)} events consumed",
            f"{len(model.calendars)} calendars",
        ]
        if model.cli_refs:
            parts.append(f"{len(model.cli_refs)} ctm cli refs")
        if model.api_refs:
            parts.append(f"{len(model.api_refs)} automation-api refs")
        return [self.result(Severity.INFO, ", ".join(parts))]


class JobWithoutTarget(_ControlMCheck):
    """CTM002: job has no execution target (no Host and no HostGroup)."""

    id = "CTM002"
    title = "Job without execution target"
    why = "Jobs without Host/HostGroup rely on defaults that may not exist at deploy."
    when_ok = "Execution target resolved by a server-side default or folder policy."
    fix = "Set Host/HostGroup on the job or in a Defaults.Job block."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        return [
            self.result(
                Severity.WARNING,
                f"job '{job.name}' has no Host or HostGroup",
                file=job.file,
                line=job.line or None,
                evidence=self.evidence_at(ctx, job.file, job.line or None),
            )
            for job in model.jobs
            if not job.host and not job.hostgroup
        ]


class DuplicateName(_ControlMCheck):
    """CTM003: same job or folder name defined more than once."""

    id = "CTM003"
    title = "Duplicate job/folder name"
    evidence_kind = EvidenceKind.DERIVED  # correlates names across definitions
    why = "Duplicate names across definitions collide at deploy or shadow each other."
    when_ok = "Each name defined exactly once across the project."
    fix = "Rename or merge the duplicated definitions."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        results: list[CheckResult] = []
        for name, jobs in sorted(model.job_names.items()):
            if len(jobs) < 2:
                continue
            for job in jobs[1:]:
                first = jobs[0]
                results.append(
                    self.result(
                        Severity.WARNING,
                        f"job '{name}' defined again - first at "
                        f"{first.file.as_posix()}:{first.line}",
                        file=job.file,
                        line=job.line or None,
                        evidence=self.evidence_at(ctx, job.file, job.line or None),
                    )
                )
        folders: dict[str, list[tuple[str, int]]] = {}
        for folder in model.folders:
            folders.setdefault(folder.name, []).append((folder.file.as_posix(), folder.line))
        for name, sites in sorted(folders.items()):
            if len(sites) < 2:
                continue
            results.append(
                self.result(
                    Severity.WARNING,
                    f"folder '{name}' defined {len(sites)} times "
                    f"({', '.join(f'{f}:{ln}' for f, ln in sites)})",
                )
            )
        return results


class JobNeverFires(_ControlMCheck):
    """CTM004: job has no schedule and waits on no event - it can never fire."""

    id = "CTM004"
    title = "Job can never fire"
    evidence_kind = EvidenceKind.DERIVED  # job facts + project-wide absence
    why = "A job with no scheduling criteria and no upstream events only runs manually."
    when_ok = "Job is intentionally manual/on-request."
    fix = "Give the job a When schedule, an event dependency, or mark it dummy."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        return [
            self.result(
                Severity.INFO,
                f"job '{job.name}' has no schedule and waits on no event",
                file=job.file,
                line=job.line or None,
                evidence=self.evidence_at(ctx, job.file, job.line or None),
            )
            for job in model.jobs
            if not job.has_schedule and not job.wait_events
        ]


class EventNeverProduced(_ControlMCheck):
    """CTM009: an event is waited on but nothing in the project produces it."""

    id = "CTM009"
    title = "Event consumed but never produced"
    evidence_kind = EvidenceKind.DERIVED  # producer/consumer correlation
    why = "Jobs waiting on an event nobody emits stay pending forever."
    when_ok = "The event is produced by another application outside this repo."
    fix = "Add an EventsToAdd/OUT producer, or fix the waited event name."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        produced = model.events_produced
        return [
            self.result(
                Severity.WARNING,
                f"event '{name}' is waited on but never produced here "
                "(or produced outside this repo)",
                file=file,
                line=line or None,
                evidence=self.evidence_at(ctx, file, line or None),
            )
            for name, (file, line) in sorted(model.events_consumed.items())
            if name not in produced
        ]


class EventNeverConsumed(_ControlMCheck):
    """CTM010: an event is produced but nothing waits on it."""

    id = "CTM010"
    title = "Event produced but never consumed"
    evidence_kind = EvidenceKind.DERIVED  # producer/consumer correlation
    why = "Dead events hide broken chains - a consumer was likely dropped or renamed."
    when_ok = "The event is consumed by a downstream outside this repo."
    fix = "Remove the event or wire the intended consumer."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        consumed = model.events_consumed
        return [
            self.result(
                Severity.INFO,
                f"event '{name}' is produced but never consumed here",
                file=file,
                line=line or None,
            )
            for name, (file, line) in sorted(model.events_produced.items())
            if name not in consumed
        ]


class CalendarNotDefined(_ControlMCheck):
    """CTM028: a calendar name is referenced but never defined in the project."""

    id = "CTM028"
    title = "Calendar referenced but not defined"
    evidence_kind = EvidenceKind.DERIVED  # reference vs definition correlation
    why = "Jobs referencing an undefined calendar silently inherit no schedule."
    when_ok = "The calendar is defined in the target environment, not in code."
    fix = "Add the calendar definition or fix the referenced name."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        defined = model.calendars
        return [
            self.result(
                Severity.WARNING,
                f"calendar '{name}' referenced but not defined in definitions",
                file=file,
                line=line or None,
                evidence=self.evidence_at(ctx, file, line or None),
            )
            for name, (file, line) in sorted(model.calendar_refs.items())
            if name not in defined
        ]


class MissingMetadata(_ControlMCheck):
    """CTM051: job lacks required metadata (Owner/RunAs/Application/...)."""

    id = "CTM051"
    title = "Missing required metadata"
    why = "Site standards and audits require Owner/RunAs/Application on every job."
    when_ok = "Fields supplied by a server-side site standard at deploy time."
    fix = "Set the missing fields on the job or in a Defaults.Job block."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        results: list[CheckResult] = []
        for job in model.jobs:
            props = {
                "Application": job.application,
                "SubApplication": job.subapplication,
                "Owner": job.owner,
                "RunAs": job.runas,
            }
            missing = [name for name in required_metadata() if name in props and not props[name]]
            if missing:
                results.append(
                    self.result(
                        Severity.WARNING,
                        f"job '{job.name}' missing required metadata: {', '.join(missing)}",
                        file=job.file,
                        line=job.line or None,
                        evidence=self.evidence_at(ctx, job.file, job.line or None),
                    )
                )
        return results


class CredentialLiteral(_ControlMCheck):
    """CTM070: credential-shaped property holds a literal value in defs."""

    id = "CTM070"
    title = "Credential literal in definitions"
    why = "Passwords/tokens embedded in workflow JSON leak via VCS history."
    when_ok = "Value is a variable reference (%%VAR%%, ${...}) not a literal."
    fix = "Replace the literal with a named variable or vault reference."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        return [
            self.result(
                Severity.WARNING,
                f"property '{name}' holds a literal credential value (value not shown)",
                file=file,
                line=line or None,
                # No evidence line: it would expose the value (V3).
            )
            for name, file, line in model.credential_props
        ]


CHECKS: list[Check] = [
    ControlMUsage(),
    JobWithoutTarget(),
    DuplicateName(),
    JobNeverFires(),
    EventNeverProduced(),
    EventNeverConsumed(),
    CalendarNotDefined(),
    MissingMetadata(),
    CredentialLiteral(),
]
