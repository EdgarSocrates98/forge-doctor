"""Shared fixtures."""

from pathlib import Path

import pytest

from forge_doctor.core.context import ProjectContext


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Empty project directory."""
    return tmp_path


@pytest.fixture
def ctx(project: Path) -> ProjectContext:
    return ProjectContext(root=project)
