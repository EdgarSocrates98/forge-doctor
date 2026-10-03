"""Stable SDK surface for Forge Doctor Data plugin authors.

Everything re-exported here follows the package semver contract - it is
the *only* module third-party plugins should import. Anything else in
``forge_doctor_data.*`` is internal and may move without notice.

Register a plugin via the ``forge_doctor_data.checks`` entry-point group::

    # pyproject.toml of the plugin package
    [project.entry-points."forge_doctor_data.checks"]
    my_plugin = "my_plugin.checks:describe"

    # my_plugin/checks.py
    from forge_doctor_data.sdk import CheckBase, PluginDescriptor, Severity

    class MyCheck(CheckBase):
        id = "MYP001"
        title = "..."
        category = "myplugin"

        def run(self, ctx):
            ...

    def describe() -> PluginDescriptor:
        return PluginDescriptor(
            name="forge-doctor-data-myplugin",
            version="0.1.0",
            api_version=CURRENT_API_VERSION,
            checks=(MyCheck(),),
            requires_forge_doctor_data=">=0.7,<2.0",
        )
"""

from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.models import (
    CheckResult,
    Confidence,
    EvidenceKind,
    Severity,
)
from forge_doctor_data.plugins.discovery import ENTRY_POINT_GROUP
from forge_doctor_data.plugins.protocol import (
    CURRENT_API_VERSION,
    SUPPORTED_API_VERSIONS,
    Check,
    CheckBase,
    PluginDescriptor,
    PluginIdentity,
)

__all__ = [
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
]
