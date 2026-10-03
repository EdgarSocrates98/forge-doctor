"""Plugin ecosystem commands: init/lock/verify/install + strict mode."""

from __future__ import annotations

import json
from pathlib import Path

from forge_doctor_data.core.config import ForgeDoctorDataConfig
from forge_doctor_data.plugins.manager import (
    install_plan,
    install_plugin,
    lock_plugins,
    scaffold_plugin,
    verify_plugins,
)


def test_scaffold_renders_valid_package(tmp_path: Path) -> None:
    written = scaffold_plugin("forge-doctor-data-demo", tmp_path)
    names = {p.relative_to(tmp_path / "forge-doctor-data-demo").as_posix() for p in written}
    assert names == {
        "pyproject.toml",
        "forge_doctor_data_demo/__init__.py",
        "forge_doctor_data_demo/checks.py",
        "tests/test_checks.py",
        "README.md",
    }
    pyproject = (tmp_path / "forge-doctor-data-demo/pyproject.toml").read_text()
    assert '"forge_doctor_data.checks"' in pyproject
    assert 'demo = "forge_doctor_data_demo.checks:describe"' in pyproject

    import tomllib

    parsed = tomllib.loads(pyproject)
    ep = parsed["project"]["entry-points"]["forge_doctor_data.checks"]
    assert ep == {"demo": "forge_doctor_data_demo.checks:describe"}


def test_scaffold_refuses_overwrite_and_bad_names(tmp_path: Path) -> None:
    import pytest

    scaffold_plugin("forge-doctor-data-demo", tmp_path)
    with pytest.raises(FileExistsError):
        scaffold_plugin("forge-doctor-data-demo", tmp_path)
    with pytest.raises(ValueError, match="invalid distribution name"):
        scaffold_plugin("Bad Name!", tmp_path)


def test_scaffolded_check_is_runnable(tmp_path: Path) -> None:
    import sys

    scaffold_plugin("forge-doctor-data-demo", tmp_path)
    sys.path.insert(0, str(tmp_path / "forge-doctor-data-demo"))
    try:
        import forge_doctor_data_demo.checks as plugin_checks

        import forge_doctor_data

        descriptor = plugin_checks.describe()
        assert descriptor.check_compatibility(forge_doctor_data.__version__) is None
        check = descriptor.checks[0]
        results = check.run(
            __import__("forge_doctor_data.sdk", fromlist=["x"]).ProjectContext(root=tmp_path)
        )
        assert results and results[0].check_id.endswith("001")
    finally:
        sys.path.remove(str(tmp_path / "forge-doctor-data-demo"))


def test_lock_then_verify_roundtrip(tmp_path: Path, monkeypatch) -> None:
    """Lock captures digests; verify reports ok, tamper -> changed."""
    import importlib.metadata as md

    dist = next(iter(md.distributions()))
    monkeypatch.setattr(
        "forge_doctor_data.plugins.manager._plugin_distributions",
        lambda: {dist.name: dist},
    )
    pins = lock_plugins(tmp_path)
    assert pins and pins[0].name == dist.name
    results = verify_plugins(tmp_path)
    assert [r.status for r in results] == ["ok"]
    lock = tmp_path / ".forge-doctor-data" / "plugins.lock"
    assert json.loads(lock.read_text())["lock_version"] == 1


def test_verify_detects_missing_and_added(tmp_path: Path, monkeypatch) -> None:
    import importlib.metadata as md

    from forge_doctor_data.plugins import manager

    dist = next(iter(md.distributions()))
    monkeypatch.setattr(manager, "_plugin_distributions", lambda: {dist.name: dist})
    lock_plugins(tmp_path)
    # plugin uninstalled since lock
    monkeypatch.setattr(manager, "_plugin_distributions", lambda: {})
    results = verify_plugins(tmp_path)
    assert results[0].status == "missing"
    # plugin installed but never locked
    monkeypatch.setattr(manager, "_plugin_distributions", lambda: {dist.name: dist})
    (tmp_path / ".forge-doctor-data" / "plugins.lock").write_text(
        json.dumps({"lock_version": 1, "plugins": []})
    )
    results = verify_plugins(tmp_path)
    assert results[0].status == "added"


def test_verify_detects_tampering(tmp_path: Path, monkeypatch) -> None:
    import importlib.metadata as md

    from forge_doctor_data.plugins import manager

    dist = next(iter(md.distributions()))
    monkeypatch.setattr(manager, "_plugin_distributions", lambda: {dist.name: dist})
    lock_plugins(tmp_path)
    lock = tmp_path / ".forge-doctor-data" / "plugins.lock"
    data = json.loads(lock.read_text())
    data["plugins"][0]["sha256"] = "0" * 64
    lock.write_text(json.dumps(data))
    results = verify_plugins(tmp_path)
    assert results[0].status == "changed"


def test_strict_mode_denies_untrusted(monkeypatch) -> None:
    """``plugins.mode = "strict"`` blocks plugins absent from trusted."""
    from types import SimpleNamespace

    from forge_doctor_data.plugins import discovery

    dist = SimpleNamespace(name="demo-dist", version="1.0")
    ep = SimpleNamespace(name="demo", value="x.checks:describe", dist=dist)
    monkeypatch.setattr(discovery, "iter_entry_points", lambda: [ep])

    _checks, infos, _errs = discovery.load_plugins(strict=True)
    assert infos and "strict" in infos[0].status
    _checks, infos2, errs = discovery.load_plugins(strict=True, trusted=("demo-dist",))
    # trusted passes the gate; the entry then fails to import (fake module)
    assert not infos2 and errs and "demo" in errs[0]


def test_config_parses_strict_mode(tmp_path: Path) -> None:
    import tomllib

    cfg = ForgeDoctorDataConfig.from_pyproject(
        tomllib.loads('[tool.forge-doctor-data.plugins]\nmode = "strict"\n')
    )
    assert cfg.plugins.mode == "strict"
    cfg2 = ForgeDoctorDataConfig.from_pyproject(tomllib.loads("[tool.forge-doctor-data]\n"))
    assert cfg2.plugins.mode == "open"


def test_install_plan_prefers_pipx(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _x: "/usr/bin/pipx")
    assert install_plan("forge-doctor-data-x") == [
        "pipx",
        "inject",
        "forge-doctor-data",
        "forge-doctor-data-x",
    ]
    monkeypatch.setattr("shutil.which", lambda _x: None)
    plan = install_plan("forge-doctor-data-x")
    assert plan[-2:] == ["install", "forge-doctor-data-x"]


def test_install_plugin_uses_injected_runner() -> None:
    calls: list[list[str]] = []
    cmd, code = install_plugin("forge-doctor-data-x", runner=lambda c: calls.append(c) or 0)
    assert code == 0 and calls == [cmd]
    _cmd, code = install_plugin("forge-doctor-data-x", runner=lambda c: 3)
    assert code == 3
