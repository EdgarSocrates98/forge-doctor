"""Continuous incremental analysis (roadmap-3 spec 207).

Invariant: incremental output == full-scan output on the same tree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge_doctor.core.context import ProjectContext, ScanOptions
from forge_doctor.core.incremental import (
    ALWAYS_RUN,
    ResultStore,
    check_domains,
    classify_path,
    detect_changes,
    file_states,
    plan_incremental,
    result_from_dict,
)
from forge_doctor.core.runner import CheckRunner
from forge_doctor.core.service import ScanRequest, ScanService
from forge_doctor.output.json_renderer import result_to_dict

_SPARK = (
    "from pyspark.sql import SparkSession\n"
    "spark = SparkSession.builder.getOrCreate()\n"
    'df = spark.read.parquet("s3://b/in")\n'
    'df.repartition(1).write.mode("overwrite").parquet("s3://b/out")\n'
)
_TF = 'resource "aws_dynamodb_table" "t" {\n  name = "orders"\n}\n'


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _fixture(root: Path) -> None:
    _write(root, "jobs.py", _SPARK)
    _write(root, "main.tf", _TF)


def _ctx(root: Path) -> ProjectContext:
    return ProjectContext(root=root, options=ScanOptions(hermetic=True))


def _registry() -> CheckRunner:
    from forge_doctor.checks import builtin_checks
    from forge_doctor.core.registry import CheckRegistry

    registry = CheckRegistry()
    registry.register_all(builtin_checks())
    return CheckRunner(registry)


def _by_check(results) -> dict[str, list]:
    grouped: dict[str, list] = {}
    for r in results:
        grouped.setdefault(r.check_id, []).append(r)
    return grouped


def _sorted(results) -> list[dict]:
    import json

    return sorted(
        (result_to_dict(r) for r in results),
        key=lambda d: json.dumps(d, sort_keys=True),
    )


# --- classification ---------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("jobs.py", {"python", "files"}),
        ("main.tf", {"terraform", "files"}),
        ("vars.tfvars", {"terraform", "files"}),
        ("q.sql", {"sql", "files"}),
        ("conf/app.yml", {"config", "files"}),
        ("nb.ipynb", {"notebook", "files"}),
        ("q.cypher", {"graph", "files"}),
        ("pyproject.toml", {"packaging", "config", "files"}),
        ("requirements-dev.txt", {"packaging", "files"}),
        ("Dockerfile", {"docker", "files"}),
        ("compose.yaml", {"docker", "config", "files"}),
        (".github/workflows/ci.yml", {"ci", "config", "files"}),
        ("platform-contract.yml", {"contract", "config", "files"}),
        ("runtime/progress.json", {"runtime", "config", "files"}),
        ("README.md", {"files"}),
        ("scripts/tool.scala", {"code", "files"}),
    ],
)
def test_classify_path(path: str, expected: set[str]) -> None:
    assert classify_path(path) == frozenset(expected)


def test_every_builtin_check_module_declares_domains() -> None:
    """Coverage contract: every builtin check's module has an entry."""
    from forge_doctor.checks import builtin_checks
    from forge_doctor.core.incremental import MODULE_DOMAINS

    missing = [
        f"{c.id} ({type(c).__module__})"
        for c in builtin_checks()
        if type(c).__module__.rsplit(".", 1)[-1] not in MODULE_DOMAINS
        and not getattr(c, "evidence_domains", None)
    ]
    assert missing == []


def test_plan_reruns_only_affected() -> None:
    from forge_doctor.checks import builtin_checks
    from forge_doctor.core.registry import CheckRegistry

    registry = CheckRegistry()
    registry.register_all(builtin_checks())
    checks = registry.select()
    plan = plan_incremental(frozenset({"main.tf"}), checks)
    assert "TF000" in plan.rerun or any(c.id.startswith("TF") for c in checks if c.id in plan.rerun)
    # .tf change must not rerun the pure-Python model checks
    assert "SPARK001" in plan.reuse
    assert "GLUE001" in plan.reuse
    # host/env/git checks can drift invisibly: always rerun
    for check in checks:
        if check_domains(check) & ALWAYS_RUN:
            assert check.id in plan.rerun


def test_plan_python_change_skips_terraform() -> None:
    from forge_doctor.checks import builtin_checks
    from forge_doctor.core.registry import CheckRegistry

    registry = CheckRegistry()
    registry.register_all(builtin_checks())
    plan = plan_incremental(frozenset({"jobs.py"}), registry.select())
    assert "SPARK001" in plan.rerun
    assert "TF000" in plan.reuse or not any(c.id.startswith("TF") for c in registry.select())


def test_detect_changes_added_modified_deleted(tmp_path: Path) -> None:
    _fixture(tmp_path)
    ctx = _ctx(tmp_path)
    states = file_states(ctx)
    _write(tmp_path, "new.sql", "SELECT 1\n")
    _write(tmp_path, "jobs.py", _SPARK + "# comment\n")
    (tmp_path / "main.tf").unlink()
    ctx2 = _ctx(tmp_path)
    changed = detect_changes(ctx2, states)
    assert changed == frozenset({"new.sql", "jobs.py", "main.tf"})


def test_detect_changes_cold_start_is_full(tmp_path: Path) -> None:
    _fixture(tmp_path)
    assert detect_changes(_ctx(tmp_path), {}) == frozenset({"jobs.py", "main.tf"})


# --- result store -----------------------------------------------------------


def test_result_store_round_trip(tmp_path: Path) -> None:
    _fixture(tmp_path)
    ctx = _ctx(tmp_path)
    report = _registry().run(ctx)
    store = ResultStore(tmp_path / "store.json")
    store.save(report.results, ctx)

    reloaded = ResultStore(tmp_path / "store.json")
    assert reloaded.file_states.keys() == {f.as_posix() for f in ctx.files}
    assert set(reloaded.results_by_check) == {r.check_id for r in report.results}
    for check_id, rows in reloaded.results_by_check.items():
        assert _sorted(rows) == _sorted([r for r in report.results if r.check_id == check_id])


def test_result_store_corrupt_is_cold(tmp_path: Path) -> None:
    path = tmp_path / "store.json"
    path.write_text("{not json", encoding="utf-8")
    store = ResultStore(path)
    assert store.results_by_check == {}


# --- the invariant: incremental == full -------------------------------------


def test_incremental_equals_full_after_tf_edit(tmp_path: Path) -> None:
    _fixture(tmp_path)
    states = file_states(_ctx(tmp_path))  # last-scan baseline
    full = _registry().run(_ctx(tmp_path))
    _write(tmp_path, "main.tf", _TF + '\nresource "aws_s3_bucket" "b" {}\n')
    ctx2 = _ctx(tmp_path)
    changed = detect_changes(ctx2, states)
    runner = _registry()
    inc = runner.run_incremental(ctx2, changed=changed, prior=_by_check(full.results))
    assert _sorted(inc.results) == _sorted(_registry().run(ctx2).results)
    assert runner.reused  # something was actually reused
    assert "TF000" in (runner.last_plan.rerun if runner.last_plan else ())


def test_incremental_equals_full_after_py_edit(tmp_path: Path) -> None:
    _fixture(tmp_path)
    states = file_states(_ctx(tmp_path))
    full = _registry().run(_ctx(tmp_path))
    _write(tmp_path, "jobs.py", _SPARK + "df.collect()\n")  # adds SPARK001
    ctx2 = _ctx(tmp_path)
    changed = detect_changes(ctx2, states)
    inc = _registry().run_incremental(ctx2, changed=changed, prior=_by_check(full.results))
    assert _sorted(inc.results) == _sorted(_registry().run(ctx2).results)


def test_incremental_cold_start_runs_all(tmp_path: Path) -> None:
    _fixture(tmp_path)
    ctx = _ctx(tmp_path)
    runner = _registry()
    report = runner.run_incremental(ctx, changed=frozenset({"jobs.py"}), prior={})
    assert _sorted(report.results) == _sorted(_registry().run(ctx).results)
    assert runner.reused == frozenset()


def test_incremental_deleted_file(tmp_path: Path) -> None:
    _fixture(tmp_path)
    states = file_states(_ctx(tmp_path))
    full = _registry().run(_ctx(tmp_path))
    (tmp_path / "jobs.py").unlink()
    ctx2 = _ctx(tmp_path)
    changed = detect_changes(ctx2, states)
    inc = _registry().run_incremental(ctx2, changed=changed, prior=_by_check(full.results))
    assert _sorted(inc.results) == _sorted(_registry().run(ctx2).results)
    assert not any(r.check_id == "SPARK003" for r in inc.results)


def test_incremental_cosmetic_edit_same_output(tmp_path: Path) -> None:
    _fixture(tmp_path)
    states = file_states(_ctx(tmp_path))
    full = _registry().run(_ctx(tmp_path))
    _write(tmp_path, "jobs.py", _SPARK + "\n")  # whitespace only
    ctx2 = _ctx(tmp_path)
    changed = detect_changes(ctx2, states)
    inc = _registry().run_incremental(ctx2, changed=changed, prior=_by_check(full.results))
    assert _sorted(inc.results) == _sorted(full.results)


def test_result_from_dict_rejects_malformed() -> None:
    assert result_from_dict({}) is None
    assert result_from_dict({"check_id": "X", "severity": "bogus"}) is None


# --- service integration -----------------------------------------------------


def test_service_incremental_reuses_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end: full scan warms the store; --incremental reuses it."""
    monkeypatch.setenv("FORGE_DOCTOR_CACHE_DIR", str(tmp_path / "user-cache"))
    _fixture(tmp_path)
    service = ScanService()
    first = service.run(ScanRequest(path=tmp_path, cache=True))
    _write(tmp_path, "main.tf", _TF + '\nresource "aws_s3_bucket" "b" {}\n')
    inc = service.run(ScanRequest(path=tmp_path, incremental=True, cache=True))
    full = service.run(ScanRequest(path=tmp_path, cache=True))
    assert _sorted(inc.report.results) == _sorted(full.report.results)
    assert inc.runner.reused
    assert first.report.results  # baseline scan produced output


def test_service_incremental_cold_without_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORGE_DOCTOR_CACHE_DIR", str(tmp_path / "user-cache"))
    _fixture(tmp_path)
    out = ScanService().run(ScanRequest(path=tmp_path, incremental=True))
    full = ScanService().run(ScanRequest(path=tmp_path))
    assert _sorted(out.report.results) == _sorted(full.report.results)
    assert out.runner.reused == frozenset()
