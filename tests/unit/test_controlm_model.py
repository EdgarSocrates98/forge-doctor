"""Unit tests for the ControlMModel analyzer."""

from __future__ import annotations

from pathlib import Path

from forge_doctor.analyzers.controlm_model import controlm_model
from forge_doctor.core.context import ProjectContext


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


DEFS = """\
{
  "Defaults": {"Job": {"Application": "ETL", "RunAs": "svc"}},
  "DAILY_LOAD": {
    "Type": "Folder",
    "JobExtract": {
      "Type": "Job",
      "SubApplication": "load",
      "Host": "agent1",
      "Owner": "ops",
      "Command": "extract.sh",
      "When": {"FromTime": "01:00", "ToTime": "03:00", "WeekDays": ["1","2","3","4","5"]},
      "EventsToAdd": {"Events": [{"Event": "ORDER_READY"}]}
    },
    "JobLoad": {
      "Type": "Job",
      "SubApplication": "load",
      "Host": "agent1",
      "Owner": "ops",
      "Command": "load.sh",
      "EventsToWaitFor": {"Events": [{"Event": "ORDER_READY"}]}
    }
  },
  "BUSINESS_DAYS": {"Type": "RBC", "WeekDays": ["1","2","3","4","5"]}
}
"""


def test_detects_folders_and_jobs(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"defs/jobs.json": DEFS})
    model = controlm_model(ctx)
    assert model.has_controlm
    assert {f.name for f in model.folders} == {"DAILY_LOAD"}
    assert {j.name for j in model.jobs} == {"JobExtract", "JobLoad"}
    assert Path("defs/jobs.json") in model.files


def test_job_effective_props_and_folder(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"jobs.json": DEFS})
    model = controlm_model(ctx)
    job = next(j for j in model.jobs if j.name == "JobExtract")
    assert job.folder == "DAILY_LOAD"
    assert job.application == "ETL"  # from Defaults.Job
    assert job.runas == "svc"
    assert job.host == "agent1"
    assert job.has_schedule
    assert job.line > 0


def test_events_produced_consumed(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"jobs.json": DEFS})
    model = controlm_model(ctx)
    assert "ORDER_READY" in model.events_produced
    assert "ORDER_READY" in model.events_consumed


def test_calendar_defined(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"jobs.json": DEFS})
    model = controlm_model(ctx)
    assert "BUSINESS_DAYS" in model.calendars


def test_non_controlm_json_ignored(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "package.json": '{"name": "x", "dependencies": {}}\n',
            "jobs.json": DEFS,
        },
    )
    model = controlm_model(ctx)
    assert len(model.jobs) == 2
    assert all(j.file == Path("jobs.json") for j in model.jobs)


def test_malformed_json_skipped(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"jobs.json": '{"F": {"Type": "Folder", broken'})
    model = controlm_model(ctx)
    assert not model.jobs


def test_cli_and_api_refs(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "ci/deploy.sh": "#!/bin/sh\nctm build jobs.json\nctm deploy jobs.json\n",
            "call.py": 'import requests\nrequests.post("https://em/automation-api/deploy")\n',
        },
    )
    model = controlm_model(ctx)
    assert len(model.cli_refs) == 2
    assert model.cli_refs[0] == (Path("ci/deploy.sh"), 2)
    assert len(model.api_refs) == 1
    assert model.has_controlm


def test_deploy_descriptor_detected(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"deployDescriptor.json": '{"a": 1}\n', "jobs.json": DEFS})
    assert Path("deployDescriptor.json") in controlm_model(ctx).deploy_descriptors


def test_credential_names_not_values(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "jobs.json": """\
{"F": {"Type": "Folder", "j": {
  "Type": "Job", "Command": "x.sh", "Host": "h",
  "Profile": {"Type": "ConnectionProfile", "Password": "hunter2", "Token": "%%TK%%"}
}}}
"""
        },
    )
    model = controlm_model(ctx)
    names = [n for n, _, _ in model.credential_props]
    assert "Password" in names
    assert "Token" not in names  # %%TK%% is a variable reference, not a literal
    # the secret value must never reach the model
    assert "hunter2" not in repr(model)


def test_site_standard_detected(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "std.json": '{"ProductionJobs": {"Type": "SiteStandard", "Rules": {}}}\n',
            "jobs.json": DEFS,
        },
    )
    model = controlm_model(ctx)
    assert [s[0] for s in model.site_standards] == ["ProductionJobs"]


def test_empty_project(tmp_path: Path) -> None:
    model = controlm_model(make_context(tmp_path, {"a.py": "x = 1\n"}))
    assert not model.has_controlm
    assert model.jobs == []


def test_deterministic_order(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"jobs.json": DEFS})
    m1, m2 = controlm_model(ctx), controlm_model(make_context(tmp_path, {"jobs.json": DEFS}))
    assert [(j.name, j.line) for j in m1.jobs] == [(j.name, j.line) for j in m2.jobs]
