"""Docs-completeness guards: README/checks.md must cover the shipped surface.

These tests fail when a command, check id, or category is added without a
corresponding doc entry — documentation completeness is enforced, not
aspirational.
"""

import re
from pathlib import Path

import forge_doctor_data.api as api
from forge_doctor_data.cli import app
from forge_doctor_data.cli.common import _build_registry

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text(encoding="utf-8")
CHECKS_MD = (ROOT / "docs" / "checks.md").read_text(encoding="utf-8")


def _top_level_names() -> list[str]:
    names = [c.name or c.callback.__name__.replace("_", "-") for c in app.registered_commands]
    names += [g.name for g in app.registered_groups]
    return [n for n in names if n]


def test_every_cli_command_is_in_readme() -> None:
    missing = [n for n in _top_level_names() if n not in README]
    assert missing == []


def test_every_check_id_is_in_checks_md() -> None:
    registry, _ = _build_registry()
    missing = [c.id for c in registry.all() if c.id not in CHECKS_MD]
    assert missing == []


def test_every_check_category_is_in_checks_md() -> None:
    registry, _ = _build_registry()
    normalized = re.sub(r"[^a-z]", "", CHECKS_MD.lower())
    missing = [
        cat for cat in registry.categories() if re.sub(r"[^a-z]", "", cat.lower()) not in normalized
    ]
    assert missing == []


def test_readme_links_resolve() -> None:
    bad = []
    for doc in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]:
        for target in re.findall(r"\]\(([^)]+)\)", doc.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "#")):
                continue
            if not (doc.parent / target.split("#")[0]).exists():
                bad.append(f"{doc.name}: {target}")
    assert bad == []


def test_api_all_names_exist() -> None:
    for name in api.__all__:
        assert getattr(api, name, None) is not None, name
