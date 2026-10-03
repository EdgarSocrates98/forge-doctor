import pytest

from forge_doctor_data.core.registry import CheckRegistry
from forge_doctor_data.plugins.protocol import CheckBase


class _Check(CheckBase):
    def __init__(self, check_id: str, category: str) -> None:
        self.id = check_id
        self.category = category
        self.title = check_id

    def run(self, ctx):
        return []


def test_register_and_get():
    registry = CheckRegistry()
    check = registry.register(_Check("X001", "python"))
    assert registry.get("X001") is check


def test_duplicate_id_rejected():
    registry = CheckRegistry()
    registry.register(_Check("X001", "python"))
    with pytest.raises(ValueError, match="Duplicate"):
        registry.register(_Check("X001", "python"))


def test_select_filters_by_category():
    registry = CheckRegistry()
    registry.register_all([_Check("A", "python"), _Check("B", "spark"), _Check("C", "python")])
    selected = registry.select(categories=["python"])
    assert [c.id for c in selected] == ["A", "C"]


def test_select_ignores_ids():
    registry = CheckRegistry()
    registry.register_all([_Check("A", "python"), _Check("B", "spark")])
    assert [c.id for c in registry.select(ignore=["B"])] == ["A"]


def test_select_empty_categories_returns_all():
    registry = CheckRegistry()
    registry.register_all([_Check("A", "python"), _Check("B", "spark")])
    assert len(registry.select()) == 2
