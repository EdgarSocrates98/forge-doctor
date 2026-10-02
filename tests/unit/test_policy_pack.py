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
