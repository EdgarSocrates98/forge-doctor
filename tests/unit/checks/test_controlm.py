"""Unit tests for the CTM### checks (ControlMModel consumers)."""

from __future__ import annotations

from pathlib import Path

from forge_doctor_data.checks.controlm import (
    CHECKS,
    CalendarNotDefined,
    ControlMUsage,
    CredentialLiteral,
    DuplicateName,
    EventNeverConsumed,
    EventNeverProduced,
    JobNeverFires,
    JobWithoutTarget,
    MissingMetadata,
)
from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.models import Severity


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


JOB_TMPL = """\
{{
  "{folder}": {{
    "Type": "Folder",
    "{name}": {{
      "Type": "Job",
      "Host": "agent1",
      "Owner": "ops",
      "RunAs": "svc",
      "Application": "ETL",
      "SubApplication": "load",
      "Command": "run.sh"{extra}
    }}
  }}
}}
"""


def job_json(name: str = "JobA", folder: str = "F", extra: str = "") -> str:
    return JOB_TMPL.format(name=name, folder=folder, extra=extra)


def test_anchor_clean(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"a.py": "x = 1\n"})
    assert ControlMUsage().run(ctx)[0].severity == Severity.PASS


def test_anchor_reports_counts(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"jobs.json": job_json()})
    result = ControlMUsage().run(ctx)[0]
    assert result.severity == Severity.INFO
    assert "1 jobs" in result.message


def test_job_without_target(tmp_path: Path) -> None:
    no_target = job_json().replace('"Host": "agent1",\n', "")
    ctx = make_context(tmp_path, {"jobs.json": no_target})
    results = JobWithoutTarget().run(ctx)
    assert len(results) == 1
    assert results[0].severity == Severity.WARNING
    assert "JobA" in results[0].message


def test_job_with_target_ok(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"jobs.json": job_json()})
    assert JobWithoutTarget().run(ctx) == []


def test_duplicate_job_cross_file(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"a.json": job_json("Dup"), "b.json": job_json("Dup", folder="G")},
    )
    results = DuplicateName().run(ctx)
    assert len(results) == 1
    assert "Dup" in results[0].message
    assert "a.json" in results[0].message


def test_duplicate_folder(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"a.json": job_json("J1", folder="F"), "b.json": job_json("J2", folder="F")},
    )
    results = DuplicateName().run(ctx)
    assert any("folder 'F'" in r.message for r in results)


def test_job_never_fires(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"jobs.json": job_json()})
    results = JobNeverFires().run(ctx)
    assert len(results) == 1
    assert results[0].severity == Severity.INFO


def test_job_with_schedule_ok(tmp_path: Path) -> None:
    sched = job_json(extra=',\n      "When": {"WeekDays": ["1"]}')
    ctx = make_context(tmp_path, {"jobs.json": sched})
    assert JobNeverFires().run(ctx) == []


def test_job_with_wait_event_ok(tmp_path: Path) -> None:
    ev = job_json(extra=',\n      "EventsToWaitFor": {"Events": [{"Event": "GO"}]}')
    ctx = make_context(tmp_path, {"jobs.json": ev})
    assert JobNeverFires().run(ctx) == []


def test_event_consumed_never_produced(tmp_path: Path) -> None:
    consumer = job_json(
        extra=',\n      "EventsToWaitFor": {"Events": [{"Event": "CUSTOMER_READY"}]}'
    )
    ctx = make_context(tmp_path, {"jobs.json": consumer})
    results = EventNeverProduced().run(ctx)
    assert len(results) == 1
    assert results[0].severity == Severity.WARNING
    assert "CUSTOMER_READY" in results[0].message


def test_event_matched_ok(tmp_path: Path) -> None:
    defs = """\
{"F": {"Type": "Folder",
  "A": {"Type": "Job", "Host": "h", "EventsToAdd": {"Events": [{"Event": "GO"}]}},
  "B": {"Type": "Job", "Host": "h", "EventsToWaitFor": {"Events": [{"Event": "GO"}]}}}}
"""
    ctx = make_context(tmp_path, {"jobs.json": defs})
    assert EventNeverProduced().run(ctx) == []
    assert EventNeverConsumed().run(ctx) == []


def test_event_produced_never_consumed(tmp_path: Path) -> None:
    producer = job_json(extra=',\n      "EventsToAdd": {"Events": [{"Event": "ORPHAN"}]}')
    ctx = make_context(tmp_path, {"jobs.json": producer})
    results = EventNeverConsumed().run(ctx)
    assert len(results) == 1
    assert results[0].severity == Severity.INFO
    assert "ORPHAN" in results[0].message


def test_calendar_not_defined(tmp_path: Path) -> None:
    ref = job_json(extra=',\n      "When": {"Calendar": "MONTH_END"}')
    ctx = make_context(tmp_path, {"jobs.json": ref})
    results = CalendarNotDefined().run(ctx)
    assert len(results) == 1
    assert results[0].severity == Severity.WARNING
    assert "MONTH_END" in results[0].message


def test_calendar_defined_ok(tmp_path: Path) -> None:
    defs = """\
{"F": {"Type": "Folder",
  "A": {"Type": "Job", "Host": "h", "When": {"Calendar": "MONTH_END"}}},
 "MONTH_END": {"Type": "RBC", "Days": ["28"]}}
"""
    ctx = make_context(tmp_path, {"jobs.json": defs})
    assert CalendarNotDefined().run(ctx) == []


def test_missing_metadata(tmp_path: Path) -> None:
    bare = job_json().replace('"Owner": "ops",\n', "").replace('"RunAs": "svc",\n', "")
    ctx = make_context(tmp_path, {"jobs.json": bare})
    results = MissingMetadata().run(ctx)
    assert len(results) == 1
    assert results[0].severity == Severity.WARNING
    assert "Owner" in results[0].message
    assert "RunAs" in results[0].message


def test_defaults_cover_metadata(tmp_path: Path) -> None:
    defs = """\
{"Defaults": {"Job": {"Application": "ETL", "SubApplication": "s", "Owner": "o", "RunAs": "r"}},
 "F": {"Type": "Folder", "A": {"Type": "Job", "Host": "h", "Command": "x.sh"}}}
"""
    ctx = make_context(tmp_path, {"jobs.json": defs})
    assert MissingMetadata().run(ctx) == []


def test_credential_literal(tmp_path: Path) -> None:
    cred = job_json(extra=',\n      "DbPassword": "secret123"')
    ctx = make_context(tmp_path, {"jobs.json": cred})
    results = CredentialLiteral().run(ctx)
    assert len(results) == 1
    assert results[0].severity == Severity.WARNING
    assert "DbPassword" in results[0].message
    assert "secret123" not in results[0].message
    assert results[0].evidence is None  # V3: value-bearing line never emitted


def test_credential_variable_ref_ok(tmp_path: Path) -> None:
    ref = job_json(extra=',\n      "DbPassword": "%%DB_PW%%"')
    ctx = make_context(tmp_path, {"jobs.json": ref})
    assert CredentialLiteral().run(ctx) == []


def test_all_checks_run_and_category(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"jobs.json": job_json()})
    for check in CHECKS:
        for r in check.run(ctx):
            assert r.check_id == check.id
            assert r.category == "controlm"


def _cli(args: list[str]):
    from typer.testing import CliRunner

    from forge_doctor_data.cli import app

    return CliRunner().invoke(app, args)


def test_cli_inspect(tmp_path: Path) -> None:
    make_context(tmp_path, {"jobs.json": job_json()})
    result = _cli(["controlm", "inspect", str(tmp_path)])
    assert result.exit_code == 0
    assert "Control-M" in result.output
    assert "JobA" in result.output


def test_cli_inspect_empty(tmp_path: Path) -> None:
    result = _cli(["controlm", "inspect", str(tmp_path)])
    assert result.exit_code == 0
    assert "no Control-M evidence" in result.output


def test_cli_bare_group(tmp_path: Path) -> None:
    result = _cli(["controlm"])
    assert result.exit_code == 2
