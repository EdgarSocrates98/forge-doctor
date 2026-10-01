from pathlib import Path

from forge_doctor import __version__
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.models import Severity
from forge_doctor.core.registry import CheckRegistry
from forge_doctor.core.runner import CheckRunner
from forge_doctor.plugins.protocol import CheckBase


class _Ok(CheckBase):
    id = "T001"
    title = "ok"
    category = "python"

    def run(self, ctx):
        return [self.result(Severity.PASS, "fine")]


class _Boom(CheckBase):
    id = "T002"
    title = "boom"
    category = "python"

    def run(self, ctx):
        raise RuntimeError("kaboom")


def test_runner_collects_results(tmp_path: Path):
    registry = CheckRegistry()
    registry.register_all([_Ok(), _Boom()])
    runner = CheckRunner(registry)
    report = runner.run(ProjectContext(root=tmp_path))

    assert report.summary.passed == 1
    assert report.summary.errors == 1
    internal = next(r for r in report.results if r.category == "internal")
    assert "T002" in internal.message
    assert len(runner.failures) == 1
    assert "kaboom" in runner.failures[0].traceback


def test_runner_stamps_plugin_source(tmp_path: Path):
    check = _Ok()
    check.__fd_source__ = "forge-doctor-example"  # type: ignore[attr-defined]
    registry = CheckRegistry()
    registry.register(check)
    report = CheckRunner(registry).run(ProjectContext(root=tmp_path))
    assert report.results[0].source == "forge-doctor-example"


def test_runner_builtin_results_have_no_source(tmp_path: Path):
    registry = CheckRegistry()
    registry.register(_Ok())
    report = CheckRunner(registry).run(ProjectContext(root=tmp_path))
    assert report.results[0].source is None


def test_runner_respects_ignore(tmp_path: Path):
    registry = CheckRegistry()
    registry.register(_Ok())
    ctx = ProjectContext(root=tmp_path)
    ctx.options = ctx.options.__class__(ignore=("T001",))
    report = CheckRunner(registry).run(ctx)
    assert report.results == []


def test_report_has_version_and_project(tmp_path: Path):
    report = CheckRunner(CheckRegistry()).run(ProjectContext(root=tmp_path))
    assert report.version == __version__
    assert report.project == tmp_path.resolve()
