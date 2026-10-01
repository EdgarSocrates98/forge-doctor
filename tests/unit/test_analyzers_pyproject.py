import tomllib

from forge_doctor.analyzers.pyproject import (
    declared_dependencies,
    dev_dependencies,
    is_poetry_managed,
    requires_python,
)

PEP621 = tomllib.loads(
    """
[project]
requires-python = ">=3.11"
dependencies = ["typer>=0.15", "boto3"]

[dependency-groups]
dev = ["pytest>=8", "typer>=0.15"]
"""
)

POETRY = tomllib.loads(
    """
[tool.poetry]
name = "x"
version = "0.1.0"

[tool.poetry.dependencies]
python = ">=3.11"
typer = "^0.15"
requests = {version = "^2", extras = ["socks"]}

[tool.poetry.group.dev.dependencies]
pytest = "^8"
"""
)


def test_requires_python_pep621():
    assert requires_python(PEP621) == ">=3.11"


def test_requires_python_poetry():
    assert requires_python(POETRY) == ">=3.11"


def test_is_poetry_managed():
    assert is_poetry_managed(POETRY)
    assert not is_poetry_managed(PEP621)


def test_declared_dependencies_pep621():
    deps = declared_dependencies(PEP621)
    assert deps["typer"] == ">=0.15"
    assert deps["boto3"] == ""


def test_declared_dependencies_poetry_skips_python():
    deps = declared_dependencies(POETRY)
    assert "python" not in deps
    assert deps["typer"] == "^0.15"
    assert deps["requests"] == "^2"


def test_dev_dependencies_pep735_and_poetry():
    assert dev_dependencies(PEP621)["pytest"] == ">=8"
    assert dev_dependencies(POETRY)["pytest"] == "^8"
