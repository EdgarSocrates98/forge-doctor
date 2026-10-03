"""Experiment engine: validate remediation hypotheses on fixture copies.

A hypothesis is a named, deterministic transform applied to a *copy* of
a lab scenario; the engine rescans the copy hermetically and reports a
before/after comparison (findings resolved/introduced by fingerprint,
severity deltas, file counts) and a verdict:

- ``improved`` — warning/error findings resolved, none introduced;
- ``regressed`` — new warning/error findings introduced;
- ``neutral`` — no meaningful change in findings.

Experiments never mutate the original scenario and never measure live
runtime — transforms are pure file edits over a temporary copy.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from forge_doctor_data.core.context import ProjectContext, ScanOptions
from forge_doctor_data.core.lab import _run_checks
from forge_doctor_data.core.models import CheckResult, Severity

_FINDING_SEVERITIES = (Severity.WARNING, Severity.ERROR)


@dataclass(frozen=True)
class Hypothesis:
    """A named deterministic transform over a fixture tree.

    ``apply(root)`` mutates the fixture *copy* and returns the sorted
    list of changed project-relative paths. Transforms must be pure
    file edits — no subprocesses, no network, no target execution.
    """

    name: str
    description: str
    apply: Callable[[Path], list[str]]


@dataclass
class ExperimentResult:
    """Before/after comparison of one hypothesis against one scenario."""

    scenario: str
    hypothesis: str
    changed_files: list[str]
    resolved: list[str] = field(default_factory=list)  # fingerprints gone after
    introduced: list[str] = field(default_factory=list)  # fingerprints new after
    resolved_findings: list[dict[str, object]] = field(default_factory=list)
    introduced_findings: list[dict[str, object]] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)
    verdict: str = "neutral"
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "scenario": self.scenario,
            "hypothesis": self.hypothesis,
            "verdict": self.verdict,
            "reasons": self.reasons,
            "changed_files": self.changed_files,
            "resolved_findings": self.resolved_findings,
            "introduced_findings": self.introduced_findings,
            "stats": self.stats,
        }


def _scan(root: Path) -> list[CheckResult]:
    ctx = ProjectContext(root=root.resolve(), options=ScanOptions(hermetic=True))
    return _run_checks(ctx)


def _fp(result: CheckResult) -> str:
    return result.fingerprint or ""


def _row(result: CheckResult) -> dict[str, object]:
    return {
        "check_id": result.check_id,
        "severity": result.severity.value,
        "file": result.file.as_posix() if result.file else None,
        "message": result.message,
        "fingerprint": result.fingerprint,
    }


def _counts(results: list[CheckResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in results:
        counts[r.severity.value] = counts.get(r.severity.value, 0) + 1
    return counts


def run_experiment(
    scenario: Path, hypothesis: str, workdir: Path | None = None
) -> ExperimentResult:
    """Scan ``scenario``, apply ``hypothesis`` to a temp copy, rescan, diff."""
    hyp = HYPOTHESES.get(hypothesis)
    if hyp is None:
        known = ", ".join(sorted(HYPOTHESES))
        raise KeyError(f"unknown hypothesis '{hypothesis}' (known: {known})")

    before = _scan(scenario)
    tmp = Path(tempfile.mkdtemp(prefix="fd-exp-", dir=workdir))
    copy_root = tmp / scenario.name
    shutil.copytree(scenario, copy_root)
    try:
        changed = sorted(hyp.apply(copy_root))
        after = _scan(copy_root)
        files_after = sum(1 for p in copy_root.rglob("*") if p.is_file())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    before_fps = {_fp(r): r for r in before}
    after_fps = {_fp(r): r for r in after}
    resolved = sorted(set(before_fps) - set(after_fps))
    introduced = sorted(set(after_fps) - set(before_fps))

    result = ExperimentResult(
        scenario=scenario.name,
        hypothesis=hypothesis,
        changed_files=changed,
        resolved=resolved,
        introduced=introduced,
        resolved_findings=[_row(before_fps[fp]) for fp in resolved],
        introduced_findings=[_row(after_fps[fp]) for fp in introduced],
        stats={
            "files_before": sum(1 for p in scenario.rglob("*") if p.is_file()),
            "files_after": files_after,
            "findings_before": len(before),
            "findings_after": len(after),
            **{f"before_{k}": v for k, v in _counts(before).items()},
            **{f"after_{k}": v for k, v in _counts(after).items()},
        },
    )
    _verdict(result, before_fps, after_fps, resolved, introduced)
    return result


def _verdict(
    result: ExperimentResult,
    before_fps: dict[str, CheckResult],
    after_fps: dict[str, CheckResult],
    resolved: list[str],
    introduced: list[str],
) -> None:
    bad_resolved = [fp for fp in resolved if before_fps[fp].severity in _FINDING_SEVERITIES]
    bad_introduced = [fp for fp in introduced if after_fps[fp].severity in _FINDING_SEVERITIES]
    if bad_introduced:
        result.verdict = "regressed"
        result.reasons.append(
            f"{len(bad_introduced)} warning/error finding(s) introduced: "
            + ", ".join(sorted({after_fps[fp].check_id for fp in bad_introduced}))
        )
    elif bad_resolved:
        result.verdict = "improved"
        result.reasons.append(
            f"{len(bad_resolved)} warning/error finding(s) resolved: "
            + ", ".join(sorted({before_fps[fp].check_id for fp in bad_resolved}))
        )
    else:
        result.verdict = "neutral"
        if not result.changed_files:
            result.reasons.append("hypothesis changed no files")
        else:
            result.reasons.append("findings unchanged at warning/error severity")
    info_shift = len(resolved) - len(bad_resolved) + len(introduced) - len(bad_introduced)
    if info_shift:
        result.reasons.append(f"{info_shift} info/pass-level identity change(s)")


# --- built-in hypotheses -----------------------------------------------------


def _edit_files(root: Path, pattern: str, subst: str, glob: str) -> list[str]:
    """Regex-rewrite every ``glob`` file under root; return changed paths."""
    changed: list[str] = []
    rx = re.compile(pattern)
    for path in sorted(root.rglob(glob)):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        new = rx.sub(subst, text)
        if new != text:
            path.write_text(new, encoding="utf-8")
            changed.append(path.relative_to(root).as_posix())
    return changed


def _bump_glue_version(root: Path) -> list[str]:
    return _edit_files(root, r'glue_version\s*=\s*"4\.0"', 'glue_version = "5.0"', "*.tf")


def _partition_data(root: Path) -> list[str]:
    changed = _edit_files(root, r"\.repartition\(1\)", ".repartition(8)", "*.py")
    changed += _edit_files(root, r"\.coalesce\(1\)", ".coalesce(8)", "*.py")
    return changed


def _add_checkpoint(root: Path) -> list[str]:
    changed: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if ".writeStream" not in text or "checkpointLocation" in text:
            continue
        # Insert after the format() call so sink detection keeps working;
        # fall back to right after `.writeStream` when no format exists.
        new = re.sub(
            r"(\.writeStream\.format\([^)]*\))",
            r'\1.option("checkpointLocation", "s3://checkpoints/exp")',
            text,
        )
        if new == text:
            new = text.replace(
                ".writeStream",
                '.writeStream.option("checkpointLocation", "s3://checkpoints/exp")',
            )
        path.write_text(new, encoding="utf-8")
        changed.append(path.relative_to(root).as_posix())
    return changed


def _increase_trigger_interval(root: Path) -> list[str]:
    return _edit_files(
        root,
        r"processingTime\s*=\s*['\"]\d+\s*(?:seconds?|ms|milliseconds?|minutes?)['\"]",
        "processingTime='30 seconds'",
        "*.py",
    )


HYPOTHESES: dict[str, Hypothesis] = {
    h.name: h
    for h in (
        Hypothesis(
            "bump-glue-version",
            "Upgrade Terraform `glue_version` 4.0 -> 5.0 in .tf files.",
            _bump_glue_version,
        ),
        Hypothesis(
            "partition-data",
            "Widen `repartition(1)`/`coalesce(1)` to 8 partitions in .py files.",
            _partition_data,
        ),
        Hypothesis(
            "add-checkpoint",
            "Add a `checkpointLocation` option to every `.writeStream`.",
            _add_checkpoint,
        ),
        Hypothesis(
            "increase-trigger-interval",
            "Raise `trigger(processingTime=...)` to 30 seconds in .py files.",
            _increase_trigger_interval,
        ),
    )
}
