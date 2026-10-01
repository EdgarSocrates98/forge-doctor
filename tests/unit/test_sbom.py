import json
from pathlib import Path

from forge_doctor.core.context import ProjectContext
from forge_doctor.core.sbom import build_sbom


def test_sbom_cyclonedx_shape(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["requests>=2", "pyspark==3.5.1"]\n',
        encoding="utf-8",
    )
    bom = build_sbom(ProjectContext(root=tmp_path))
    assert bom["bomFormat"] == "CycloneDX"
    assert bom["specVersion"] == "1.5"
    names = {c["name"] for c in bom["components"]}
    assert "requests" in names and "pyspark" in names
    # knowledge packs + forge-doctor itself
    types = {c["type"] for c in bom["components"]}
    assert "library" in types
    json.dumps(bom)


def test_sbom_project_metadata(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    bom = build_sbom(ProjectContext(root=tmp_path))
    assert bom["metadata"]["component"]["name"] == "demo"


_LOCK = """
[[package]]
name = "requests"
version = "2.32.3"
groups = ["main"]

[package.dependencies]
urllib3 = "^3.0"

[[package]]
name = "urllib3"
version = "3.0.1"
groups = ["main"]

[[package]]
name = "pytest"
version = "8.3.0"
groups = ["dev"]
"""


def _lock_project(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "1.2.3"\ndependencies = ["requests>=2"]\n',
        encoding="utf-8",
    )
    (tmp_path / "poetry.lock").write_text(_LOCK, encoding="utf-8")


def test_sbom_includes_transitives_and_graph(tmp_path: Path):
    _lock_project(tmp_path)
    bom = build_sbom(ProjectContext(root=tmp_path))
    names = {c["name"] for c in bom["components"]}
    assert {"requests", "urllib3", "pytest"} <= names
    scopes = {c["name"]: c.get("scope") for c in bom["components"]}
    assert scopes["urllib3"] == "required" and scopes["pytest"] == "optional"

    graph = {d["ref"]: d["dependsOn"] for d in bom["dependencies"]}
    assert graph["root"] == ["pkg:pypi/requests@2.32.3"]
    assert graph["pkg:pypi/requests@2.32.3"] == ["pkg:pypi/urllib3@3.0.1"]
    assert graph["pkg:pypi/urllib3@3.0.1"] == []


def test_sbom_serial_is_path_independent(tmp_path: Path):
    """Serial derives from project name+version, not the checkout dir."""
    a = tmp_path / "checkout-a"
    b = tmp_path / "checkout-b"
    a.mkdir()
    b.mkdir()
    _lock_project(a)
    _lock_project(b)
    bom_a = build_sbom(ProjectContext(root=a))
    bom_b = build_sbom(ProjectContext(root=b))
    assert bom_a["serialNumber"] == bom_b["serialNumber"]
    assert bom_a["serialNumber"].startswith("urn:uuid:")
