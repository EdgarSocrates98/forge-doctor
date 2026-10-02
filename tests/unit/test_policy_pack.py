"""Organization policy packs (roadmap-2 phase 7)."""

from __future__ import annotations

from pathlib import Path

from forge_doctor.core.context import ProjectContext
from forge_doctor.core.models import Severity
from forge_doctor.core.policy_pack import (
    discover_packs,
    evaluate_packs,
    load_pack,
    load_packs,
)

_PACK = """
pack: org-security
version: "1.0"
rules:
  - id: ORG001
    severity: error
    message: RDS must not be public
    forbid:
      terraform:
        resource_type: aws_db_instance
        attr: publicly_accessible
        op: equals
        value: "true"
  - id: ORG002
    severity: warning
    message: S3 buckets need tags
    require:
      terraform:
        resource_type: aws_s3_bucket
        attr: tags
        op: present
  - id: ORG003
    severity: warning
    message: CODEOWNERS required
    require:
      file: CODEOWNERS
  - id: ORG004
    severity: error
    message: No hardcoded passwords
    forbid:
      file_glob: "**/*.py"
      pattern: 'password\\s*=\\s*"[^"]+"'
"""


def _ctx(tmp_path: Path) -> ProjectContext:
    return ProjectContext(root=tmp_path)


def _write_pack(tmp_path: Path, text: str = _PACK) -> Path:
    d = tmp_path / ".forge-doctor" / "policy"
    d.mkdir(parents=True)
    p = d / "org.yml"
    p.write_text(text)
    return p


def test_load_pack_schema(tmp_path: Path) -> None:
    p = _write_pack(tmp_path)
    pack = load_pack(p)
    assert pack.name == "org-security"
    assert len(pack.rules) == 4
    assert pack.rules[0].forbid is not None
    assert pack.rules[0].forbid.resource_type == "aws_db_instance"
    assert pack.rules[2].require is not None
    assert pack.rules[2].require.file == "CODEOWNERS"


def test_discover_packs_locations(tmp_path: Path) -> None:
    _write_pack(tmp_path)
    (tmp_path / "org-policy.yml").write_text("pack: root\nrules: []\n")
    ctx = _ctx(tmp_path)
    found = discover_packs(tmp_path, ctx.config.policy_packs)
    assert len(found) == 2
    assert {p.name for p in found} == {"org.yml", "org-policy.yml"}


def test_forbid_terraform_rule(tmp_path: Path) -> None:
    _write_pack(tmp_path)
    (tmp_path / "main.tf").write_text(
        'resource "aws_db_instance" "db" {\n  publicly_accessible = true\n}\n'
        'resource "aws_db_instance" "ok" {\n  publicly_accessible = false\n}\n'
    )
    packs, errors = load_packs(tmp_path)
    assert errors == []
    results = evaluate_packs(_ctx(tmp_path), packs)
    org001 = [r for r in results if r.check_id == "ORG001"]
    assert len(org001) == 1
    assert org001[0].severity is Severity.ERROR
    assert "aws_db_instance.db" in (org001[0].message + (org001[0].evidence or ""))


def test_require_terraform_rule(tmp_path: Path) -> None:
    _write_pack(tmp_path)
    (tmp_path / "main.tf").write_text(
        'resource "aws_s3_bucket" "bare" {}\n'
        'resource "aws_s3_bucket" "tagged" {\n  tags = { env = "dev" }\n}\n'
    )
    results = evaluate_packs(_ctx(tmp_path), load_packs(tmp_path)[0])
    org002 = [r for r in results if r.check_id == "ORG002"]
    assert len(org002) == 1
    assert "aws_s3_bucket.bare" in org002[0].message


def test_require_file_and_forbid_pattern(tmp_path: Path) -> None:
    _write_pack(tmp_path)
    (tmp_path / "app.py").write_text('password = "hunter2"\n')
    results = evaluate_packs(_ctx(tmp_path), load_packs(tmp_path)[0])
    ids = {r.check_id for r in results}
    assert "ORG003" in ids  # missing CODEOWNERS
    org004 = [r for r in results if r.check_id == "ORG004"]
    assert len(org004) == 1 and org004[0].line == 1


def test_glob_root_level_match(tmp_path: Path) -> None:
    """**/*.py must match files directly under root (not only subdirs)."""
    _write_pack(tmp_path)
    (tmp_path / "top.py").write_text('password = "x"\n')
    results = evaluate_packs(_ctx(tmp_path), load_packs(tmp_path)[0])
    assert any(r.check_id == "ORG004" for r in results)


def test_clean_project_no_violations(tmp_path: Path) -> None:
    _write_pack(tmp_path)
    (tmp_path / "CODEOWNERS").write_text("* @team\n")
    (tmp_path / "app.py").write_text("print('ok')\n")
    results = evaluate_packs(_ctx(tmp_path), load_packs(tmp_path)[0])
    assert results == []


def test_pack_via_pyproject_config(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[tool.forge-doctor]\npolicy_packs = ["rules.yml"]\n')
    (tmp_path / "rules.yml").write_text(
        "pack: extra\nrules:\n  - id: ORG009\n    message: need README\n"
        "    require:\n      file: README.md\n"
    )
    ctx = _ctx(tmp_path)
    packs, errors = load_packs(tmp_path, ctx.config.policy_packs)
    assert errors == []
    assert packs and packs[0].name == "extra"
    assert evaluate_packs(ctx, packs)[0].check_id == "ORG009"


def test_check_runs_inside_scan(tmp_path: Path) -> None:
    _write_pack(tmp_path)
    (tmp_path / "main.tf").write_text(
        'resource "aws_db_instance" "db" {\n  publicly_accessible = true\n}\n'
    )
    from forge_doctor.checks.policy_pack import OrgPolicyPacks

    results = OrgPolicyPacks().run(_ctx(tmp_path))
    assert any(r.check_id == "ORG001" for r in results)


# -- pack layering (extends) -------------------------------------------------

_BASE = """
pack: org-base
rules:
  - id: ORG001
    severity: error
    message: base rule
    require:
      file: BASE_ONLY.md
"""

_CHILD = """
pack: repo-rules
extends: org-base
rules:
  - id: ORG001
    severity: warning
    message: child override
    require:
      file: CHILD_ONLY.md
  - id: REPO001
    severity: warning
    message: repo rule
    require:
      file: REPO.md
"""


def _write_pack_named(tmp_path: Path, name: str, text: str) -> Path:
    d = tmp_path / ".forge-doctor" / "policy"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(text)
    return p


def test_extends_merges_and_child_overrides(tmp_path: Path) -> None:
    _write_pack_named(tmp_path, "base.yml", _BASE)
    _write_pack_named(tmp_path, "repo.yml", _CHILD)
    packs, errors = load_packs(tmp_path)
    assert errors == []
    child = next(p for p in packs if p.name == "repo-rules")
    ids = [r.id for r in child.rules]
    assert sorted(ids) == ["ORG001", "REPO001"]
    org001 = next(r for r in child.rules if r.id == "ORG001")
    assert org001.severity == "warning"  # child override wins
    assert org001.require is not None and org001.require.file == "CHILD_ONLY.md"


def test_extends_unknown_pack_is_error(tmp_path: Path) -> None:
    _write_pack_named(tmp_path, "repo.yml", _CHILD)
    packs, errors = load_packs(tmp_path)
    assert any("'org-base' not found" in e.message for e in errors)
    # Pack still evaluates with its own rules.
    assert any(r.id == "REPO001" for p in packs for r in p.rules)


def test_extends_cycle_is_error(tmp_path: Path) -> None:
    _write_pack_named(tmp_path, "a.yml", "pack: a\nextends: b\nrules: []\n")
    _write_pack_named(tmp_path, "b.yml", "pack: b\nextends: a\nrules: []\n")
    _, errors = load_packs(tmp_path)
    assert any("cycle" in e.message for e in errors)


def test_extends_empty_rules_pack(tmp_path: Path) -> None:
    """A pack may consist purely of extends (rule-less overlay)."""
    d = tmp_path / ".forge-doctor" / "policy"
    d.mkdir(parents=True)
    (d / "base.yml").write_text(_BASE)
    (d / "overlay.yml").write_text("pack: overlay\nextends: org-base\nrules: []\n")
    packs, errors = load_packs(tmp_path)
    assert errors == []
    overlay = next(p for p in packs if p.name == "overlay")
    assert [r.id for r in overlay.rules] == ["ORG001"]


# -- require_approval --------------------------------------------------------


def test_require_approval_flags_unapproved_suppressions(tmp_path: Path) -> None:
    _write_pack_named(
        tmp_path,
        "strict.yml",
        "pack: strict\nrequire_approval: true\nrules: []\n",
    )
    (tmp_path / "pyproject.toml").write_text(
        "[tool.forge-doctor]\n"
        "[[tool.forge-doctor.suppressions]]\n"
        'rule = "GLUE001"\n'
        'owner = "alice"\n'
        "[[tool.forge-doctor.suppressions]]\n"
        'rule = "S3_001"\n'
        'approved_by = "bob"\n'
    )
    ctx = _ctx(tmp_path)
    results = evaluate_packs(ctx, load_packs(tmp_path)[0])
    flagged = [r for r in results if r.check_id == "POLICY011"]
    assert len(flagged) == 1
    assert "GLUE001" in flagged[0].message


def test_no_require_approval_no_findings(tmp_path: Path) -> None:
    _write_pack_named(tmp_path, "loose.yml", "pack: loose\nrules: []\n")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.forge-doctor]\n[[tool.forge-doctor.suppressions]]\nrule = "GLUE001"\n'
    )
    results = evaluate_packs(_ctx(tmp_path), load_packs(tmp_path)[0])
    assert not any(r.check_id == "POLICY011" for r in results)
