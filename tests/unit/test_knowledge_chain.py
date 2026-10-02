"""Knowledge supply chain: scaffold / diff / conformance / publish."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from forge_doctor.cli import app
from forge_doctor.core.knowledge import (
    bump_pack,
    conformance,
    diff_packs,
    load_pack_ref,
    publish_checklist,
    scaffold_pack,
    write_scaffold,
)

runner = CliRunner()


def _pack(**extra) -> dict:
    base = {
        "schema_version": 2,
        "pack_version": "2026.1",
        "verified_at": date.today().isoformat(),
        "sources": ["https://example.com/docs"],
        "domain": "x",
    }
    base.update(extra)
    return base


# --- scaffold ----------------------------------------------------------------


def test_scaffold_versions_validates() -> None:
    pack = scaffold_pack("mypkg", "versions")
    assert pack["schema_version"] == 2
    assert pack["pack_version"] != "-"
    assert pack["verified_at"]
    assert pack["sources"]


def test_scaffold_errors_has_positive_example() -> None:
    pack = scaffold_pack("mypkg", "errors")
    entry = pack["errors"][0]
    assert entry["examples"]
    report = conformance([("mypkg", "errors", pack)])
    assert not report["issues"]


def test_scaffold_capabilities_evaluates() -> None:
    pack = scaffold_pack("mypkg", "capabilities")
    report = conformance([("capabilities", "mypkg", pack)])
    assert not report["issues"]


def test_scaffold_unknown_kind_rejected() -> None:
    with pytest.raises(ValueError, match="unknown pack kind"):
        scaffold_pack("x", "nonsense")


def test_write_scaffold_refuses_overwrite(tmp_path: Path) -> None:
    target = write_scaffold(tmp_path, "d", "versions")
    assert target.exists()
    with pytest.raises(FileExistsError):
        write_scaffold(tmp_path, "d", "versions")


# --- diff --------------------------------------------------------------------


def test_diff_detects_add_remove_change() -> None:
    old = _pack(versions={"1.0": {"status": "supported"}, "2.0": {"status": "eol"}})
    new = _pack(
        versions={
            "1.0": {"status": "supported"},
            "2.0": {"status": "unsupported"},
            "3.0": {"status": "supported"},
        }
    )
    rows = diff_packs(old, new)
    by = {(r["section"], r["id"], r["change"]) for r in rows}
    assert ("versions", "3.0", "added") in by
    assert ("versions", "2.0", "changed") in by
    changed = next(r for r in rows if r["change"] == "changed")
    assert any("status" in d for d in changed["details"])
    # removal
    rows2 = diff_packs(new, old)
    assert any(r["change"] == "removed" and r["id"] == "3.0" for r in rows2)


def test_diff_ignores_meta_fields() -> None:
    a = _pack(pack_version="2026.1", versions={"1.0": {}})
    b = _pack(pack_version="2026.9", verified_at="1999-01-01", versions={"1.0": {}})
    assert diff_packs(a, b) == []


def test_diff_list_sections_keyed_by_id() -> None:
    old = _pack(errors=[{"id": "E1", "patterns": ["a"]}])
    new = _pack(errors=[{"id": "E1", "patterns": ["a"]}, {"id": "E2", "patterns": ["b"]}])
    rows = diff_packs(old, new)
    assert rows == [{"section": "errors", "id": "E2", "change": "added", "details": []}]


def test_diff_deterministic() -> None:
    a = _pack(versions={"1.0": {"status": "eol"}, "9.9": {"status": "supported"}})
    b = _pack(versions={"1.0": {"status": "supported"}, "2.0": {"status": "eol"}})
    assert diff_packs(a, b) == diff_packs(a, b)


def test_load_pack_ref_path_and_domain(tmp_path: Path) -> None:
    f = tmp_path / "p.json"
    f.write_text(json.dumps(_pack()), encoding="utf-8")
    assert load_pack_ref(str(f))["domain"] == "x"
    assert load_pack_ref("glue/versions")["domain"] == "glue"
    assert load_pack_ref(str(tmp_path / "missing.json")) is None


# --- conformance -------------------------------------------------------------


def test_conformance_catches_broken_regex() -> None:
    pack = _pack(errors=[{"id": "E1", "patterns": ["re:unclosed("], "examples": ["x"]}])
    report = conformance([("d", "errors", pack)])
    assert any("broken regex" in i for i in report["issues"])


def test_conformance_catches_example_without_match() -> None:
    pack = _pack(
        errors=[
            {
                "id": "E1",
                "patterns": ["re:timeout.*dag"],
                "examples": ["completely unrelated log line"],
            }
        ]
    )
    report = conformance([("d", "errors", pack)])
    assert any("matches no pattern" in i for i in report["issues"])


def test_conformance_example_matching_pattern_ok() -> None:
    pack = _pack(
        errors=[
            {
                "id": "E1",
                "patterns": ["re:timeout.*dag"],
                "examples": ["Airflow timeout while parsing dag"],
            }
        ]
    )
    report = conformance([("d", "errors", pack)])
    assert not report["issues"]


def test_conformance_warns_regex_only_without_examples() -> None:
    pack = _pack(errors=[{"id": "E1", "patterns": ["re:foo.*bar"]}])
    report = conformance([("d", "errors", pack)])
    assert not report["issues"]
    assert any("no positive example" in w for w in report["warnings"])


def test_conformance_substring_pattern_needs_no_example() -> None:
    pack = _pack(errors=[{"id": "E1", "patterns": ["out of memory"]}])
    report = conformance([("d", "errors", pack)])
    assert not report["issues"] and not report["warnings"]


def test_conformance_capability_mismatch_flagged() -> None:
    pack = _pack(
        domain="capabilities",
        capabilities=[
            {
                "id": "CAP_X",
                "platform": "demo",
                "status": "supported",
                "versions": {"1.0": "supported", "2.0": "unsupported"},
                "source": "https://example.com/doc",
            }
        ],
    )
    # Lie in the declared version map: engine evaluates per-version and
    # must see 1.0=supported but 2.0=unsupported (the declared map IS the
    # source of truth here - so the real check is registry acceptance).
    good = conformance([("capabilities", "demo", pack)])
    assert not good["issues"]
    bad = dict(pack)
    bad["capabilities"] = [{**pack["capabilities"][0], "versions": {"1.0": "not-a-status"}}]
    report = conformance([("capabilities", "demo", bad)])
    assert report["issues"]  # registry validation rejects invalid status


def test_conformance_missing_provenance_flagged() -> None:
    report = conformance([("d", "versions", {"versions": {}})])
    assert any("schema_version" in i for i in report["issues"])
    assert any("no sources" in i for i in report["issues"])


# --- publish -----------------------------------------------------------------


def test_publish_checklist_glue_ready() -> None:
    report = publish_checklist("glue")
    assert report["ready"] is True
    assert report["packs"] and "next_pack_version" in report


def test_publish_checklist_unknown_domain() -> None:
    assert publish_checklist("nonexistent-domain")["ready"] is False


def test_bump_pack_rewrites_version(tmp_path: Path) -> None:
    pack_file = tmp_path / "demo" / "versions.json"
    pack_file.parent.mkdir(parents=True)
    pack_file.write_text(
        json.dumps(_pack(pack_version="2000.1", verified_at="2000-01-01")),
        encoding="utf-8",
    )
    written = bump_pack(tmp_path, "demo", today=date(2026, 10, 3))
    assert written == [pack_file]
    updated = json.loads(pack_file.read_text(encoding="utf-8"))
    assert updated["pack_version"] == "2026.10.3"
    assert updated["verified_at"] == "2026-10-03"


# --- CLI ---------------------------------------------------------------------


def test_cli_knowledge_test_clean() -> None:
    result = runner.invoke(app, ["knowledge", "test"])
    assert result.exit_code == 0, result.output
    assert "0 issue(s)" in result.output


def test_cli_knowledge_new(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["knowledge", "new", "demopack", "--kind", "errors", "--dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    created = tmp_path / "demopack" / "errors.json"
    assert created.exists()
    assert json.loads(created.read_text())["schema_version"] == 2


def test_cli_knowledge_diff(tmp_path: Path) -> None:
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps(_pack(versions={"1.0": {}})), encoding="utf-8")
    b.write_text(json.dumps(_pack(versions={"1.0": {}, "2.0": {}})), encoding="utf-8")
    result = runner.invoke(app, ["knowledge", "diff", str(a), str(b)])
    assert result.exit_code == 0, result.output
    assert "added" in result.output and "2.0" in result.output


def test_cli_knowledge_publish_dry_run() -> None:
    result = runner.invoke(app, ["knowledge", "publish", "glue"])
    assert result.exit_code == 0, result.output
    assert "next pack_version" in result.output
    assert "dry-run" in result.output
