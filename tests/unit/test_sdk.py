"""forge_doctor_data.sdk - the stable plugin-author surface (R3 phase 1)."""

from __future__ import annotations

import forge_doctor_data.sdk as sdk


def test_sdk_surface_is_pinned() -> None:
    """``sdk.__all__`` is the contract third parties import."""
    assert set(sdk.__all__) == {
        "CURRENT_API_VERSION",
        "ENTRY_POINT_GROUP",
        "SUPPORTED_API_VERSIONS",
        "Check",
        "CheckBase",
        "CheckResult",
        "Confidence",
        "EvidenceKind",
        "PluginDescriptor",
        "PluginIdentity",
        "ProjectContext",
        "Severity",
    }


def test_sdk_names_are_real() -> None:
    for name in sdk.__all__:
        assert getattr(sdk, name, None) is not None, name


def test_sdk_versions_match_engine() -> None:
    from forge_doctor_data.plugins.protocol import (
        CURRENT_API_VERSION,
        SUPPORTED_API_VERSIONS,
    )

    assert sdk.CURRENT_API_VERSION == CURRENT_API_VERSION
    assert sdk.SUPPORTED_API_VERSIONS == SUPPORTED_API_VERSIONS
    assert sdk.ENTRY_POINT_GROUP == "forge_doctor_data.checks"


def test_sdk_checkbase_produces_results(tmp_path) -> None:
    """A plugin written against only the SDK produces valid findings."""

    class Demo(sdk.CheckBase):
        id = "DEMO001"
        title = "demo"
        category = "demo"

        def run(self, ctx: sdk.ProjectContext) -> list[sdk.CheckResult]:
            return [self.result(sdk.Severity.WARNING, "demo finding")]

    results = Demo().run(sdk.ProjectContext(root=tmp_path))
    assert results[0].check_id == "DEMO001"
    assert results[0].severity is sdk.Severity.WARNING
