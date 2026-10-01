"""Plugin discovery: entry point -> discovery -> registry -> runner."""

import types
from importlib.metadata import EntryPoint
from pathlib import Path

import forge_doctor.plugins.discovery as discovery
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.models import Severity
from forge_doctor.core.registry import CheckRegistry
from forge_doctor.core.runner import CheckRunner
from forge_doctor.plugins.protocol import CheckBase


class FakePluginCheck(CheckBase):
    id = "PLUGIN001"
    title = "plugin finding"
    category = "plugin"

    def run(self, ctx):
        return [self.result(Severity.INFO, "from plugin")]


def _fake_entry_points(monkeypatch, loaded_value, name="fake"):
    ep = EntryPoint(name=name, value="fake_plugin:FakePluginCheck", group="forge_doctor.checks")
    monkeypatch.setattr(ep.__class__, "load", lambda self: loaded_value)
    monkeypatch.setattr(discovery, "iter_entry_points", lambda: [ep])
    return ep


def test_discover_plugins_lists_entry_points(monkeypatch):
    _fake_entry_points(monkeypatch, FakePluginCheck)
    plugins = discovery.discover_plugins()
    assert [p.name for p in plugins] == ["fake"]


def test_load_plugin_checks_instantiates_class(monkeypatch):
    _fake_entry_points(monkeypatch, FakePluginCheck)
    checks, errors = discovery.load_plugin_checks()
    assert errors == []
    assert len(checks) == 1
    assert checks[0].id == "PLUGIN001"


def test_broken_plugin_degrades_to_error(monkeypatch):
    def _boom():
        raise RuntimeError("import failure")

    _fake_entry_points(monkeypatch, _boom)
    # ep.load() returning a callable gets used directly; make load itself raise
    monkeypatch.setattr(EntryPoint, "load", lambda self: (_ for _ in ()).throw(RuntimeError("x")))
    checks, errors = discovery.load_plugin_checks()
    assert checks == []
    assert len(errors) == 1


def test_plugin_check_flows_through_runner(monkeypatch, tmp_path: Path):
    _fake_entry_points(monkeypatch, FakePluginCheck)
    registry = CheckRegistry()
    checks, _ = discovery.load_plugin_checks()
    registry.register_all(checks)
    report = CheckRunner(registry).run(ProjectContext(root=tmp_path))
    assert any(r.check_id == "PLUGIN001" for r in report.results)


def test_untrusted_plugin_never_loads(monkeypatch):
    """The trust boundary: ep.load() must not run for untrusted plugins."""
    loaded: list[object] = []
    ep = EntryPoint(name="evil", value="evil:Check", group="forge_doctor.checks")

    def _load(self):  # pragma: no cover - must never be reached
        loaded.append(self)
        return FakePluginCheck

    monkeypatch.setattr(EntryPoint, "load", _load)
    monkeypatch.setattr(discovery, "iter_entry_points", lambda: [ep])
    checks, infos, errors = discovery.load_plugins(trusted=("forge-doctor-good",))
    assert loaded == []
    assert checks == []
    assert errors == []
    assert infos[0].status is not None and "untrusted" in infos[0].status


def test_trusted_plugin_loads(monkeypatch):
    _fake_entry_points(monkeypatch, FakePluginCheck, name="fake")
    checks, infos, _errors = discovery.load_plugins(trusted=("fake",))
    assert len(checks) == 1
    assert infos[0].status is None


def test_allow_identity_gates_load(monkeypatch):
    """Non-check-id allow entries act as a pre-load trust gate."""
    _fake_entry_points(monkeypatch, FakePluginCheck, name="fake")
    checks, infos, _errors = discovery.load_plugins(allow=("other-plugin",))
    assert checks == []
    assert "untrusted" in (infos[0].status or "")


def test_allow_check_id_loads_and_filters(monkeypatch):
    """Check-id allow entries can't gate loading - they filter post-load."""
    _fake_entry_points(monkeypatch, FakePluginCheck, name="fake")
    checks, _infos, _errors = discovery.load_plugins(allow=("PLUGIN001",))
    assert len(checks) == 1  # loaded; registry-level filtering drops others
    identities, check_ids = discovery.split_allow(("PLUGIN001", "forge-doctor-x"))
    assert identities == frozenset({"forge-doctor-x"})
    assert check_ids == frozenset({"PLUGIN001"})


def _ep_with_dist(name, loaded, dist_name=None, dist_version=None):
    """Duck-typed entry point carrying distribution metadata (ep.dist)."""
    return types.SimpleNamespace(
        name=name,
        value="fake_plugin:FakePluginCheck",
        dist=(
            types.SimpleNamespace(name=dist_name, version=dist_version)
            if dist_name is not None
            else None
        ),
        load=lambda: loaded,
    )


def test_trusted_by_distribution_name_loads(monkeypatch):
    """``trusted`` matches the distribution name, not only the ep name."""
    ep = _ep_with_dist("entry-name", FakePluginCheck, dist_name="forge-doctor-fake")
    monkeypatch.setattr(discovery, "iter_entry_points", lambda: [ep])
    checks, infos, _errors = discovery.load_plugins(trusted=("forge-doctor-fake",))
    assert len(checks) == 1
    assert infos[0].status is None


def test_mixed_batch_only_trusted_loads(monkeypatch):
    """One trusted + one untrusted: untrusted's ep.load() is never invoked."""
    spy: list[object] = []
    good = _ep_with_dist("good", FakePluginCheck, dist_name="good-dist")
    evil = _ep_with_dist("evil", FakePluginCheck, dist_name="evil-dist")
    evil.load = lambda: spy.append("evil")  # must never run
    monkeypatch.setattr(discovery, "iter_entry_points", lambda: [good, evil])
    checks, infos, errors = discovery.load_plugins(trusted=("good-dist",))
    assert spy == []
    assert len(checks) == 1
    assert errors == []
    untrusted = [i for i in infos if i.name == "evil"]
    assert untrusted and "untrusted" in (untrusted[0].status or "")


def test_check_id_allow_grants_no_identity(monkeypatch):
    """A distribution literally named PLUGIN001 gains no trust from a
    check-id-looking allow entry once real identity entries are present."""
    ep = _ep_with_dist("evil", FakePluginCheck, dist_name="PLUGIN001")
    monkeypatch.setattr(discovery, "iter_entry_points", lambda: [ep])
    checks, infos, _errors = discovery.load_plugins(allow=("PLUGIN001", "other-dist"))
    assert checks == []
    assert "untrusted" in (infos[0].status or "")


def test_untrusted_callable_never_invoked(monkeypatch):
    """Descriptor-producing callables are gated pre-load: never called."""
    called: list[bool] = []

    def factory():
        called.append(True)
        return FakePluginCheck()

    ep = EntryPoint(name="evil", value="evil:factory", group="forge_doctor.checks")
    monkeypatch.setattr(EntryPoint, "load", lambda self: factory)
    monkeypatch.setattr(discovery, "iter_entry_points", lambda: [ep])
    checks, infos, _errors = discovery.load_plugins(trusted=("other-dist",))
    assert called == []
    assert checks == []
    assert "untrusted" in (infos[0].status or "")


def test_build_registry_applies_check_filters(monkeypatch, tmp_path: Path):
    from forge_doctor.cli.common import _build_registry
    from forge_doctor.core.config import ForgeDoctorConfig, PluginRules

    _fake_entry_points(monkeypatch, FakePluginCheck, name="fake")
    cfg = ForgeDoctorConfig(plugins=PluginRules(trusted=("fake",), checks_disabled=("PLUGIN001",)))
    registry, errors = _build_registry(config=cfg)
    assert registry.get("PLUGIN001") is None
    assert any("disabled" in e for e in errors)

    cfg2 = ForgeDoctorConfig(plugins=PluginRules(trusted=("fake",), checks_enabled=("PLUGIN001",)))
    registry2, _ = _build_registry(config=cfg2)
    assert registry2.get("PLUGIN001") is not None
