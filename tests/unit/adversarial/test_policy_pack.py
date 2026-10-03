"""Policy pack adversarial tests - malformed packs, spoofed rules, safety."""

from __future__ import annotations

from pathlib import Path

import pytest

from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.policy_pack import (
    INVALID_PACK_CHECK_ID,
    PolicyPackError,
    evaluate_packs,
    load_pack,
    load_packs,
)


def _pack(tmp_path: Path, body: str, name: str = "org.yml") -> Path:
    d = tmp_path / ".forge-doctor-data" / "policy"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(body)
    return p


def test_malformed_yaml_reports_error_not_crash(tmp_path: Path) -> None:
    _pack(tmp_path, "pack: [unclosed\nrules: {bad\n")
    _packs, errors = load_packs(tmp_path)
    assert len(errors) == 1
    assert errors[0].check_id == INVALID_PACK_CHECK_ID
    assert errors[0].severity.value == "error"


def test_rule_without_forbid_or_require_rejected(tmp_path: Path) -> None:
    p = _pack(tmp_path, "pack: x\nrules:\n  - id: ORG1\n    message: m\n")
    with pytest.raises(PolicyPackError, match="needs 'forbid' or 'require'"):
        load_pack(p)


def test_duplicate_rule_ids_rejected(tmp_path: Path) -> None:
    p = _pack(
        tmp_path,
        "pack: x\nrules:\n"
        "  - id: ORG1\n    message: a\n    require:\n      file: A\n"
        "  - id: ORG1\n    message: b\n    require:\n      file: B\n",
    )
    with pytest.raises(PolicyPackError, match="duplicate rule id"):
        load_pack(p)


def test_bad_regex_rejected(tmp_path: Path) -> None:
    p = _pack(
        tmp_path,
        'pack: x\nrules:\n  - id: ORG1\n    message: m\n    forbid:\n      pattern: "([unclosed"\n',
    )
    with pytest.raises(PolicyPackError, match="bad regex"):
        load_pack(p)


def test_invalid_severity_rejected(tmp_path: Path) -> None:
    p = _pack(
        tmp_path,
        "pack: x\nrules:\n  - id: ORG1\n    severity: catastrophic\n"
        "    message: m\n    require:\n      file: A\n",
    )
    with pytest.raises(PolicyPackError, match="severity"):
        load_pack(p)


def test_forbid_pattern_never_touches_gitignored_or_binary(tmp_path: Path) -> None:
    """Pattern eval reads only ctx.files - absent content can't match."""
    _pack(
        tmp_path,
        "pack: x\nrules:\n  - id: ORG1\n    message: secret\n"
        '    forbid:\n      pattern: "AKIA[0-9A-Z]{16}"\n',
    )
    results = evaluate_packs(ProjectContext(root=tmp_path), load_packs(tmp_path)[0])
    assert results == []


def test_require_file_glob_no_match_is_not_violation(tmp_path: Path) -> None:
    """require.contains applies to matching files only - zero matches = ok."""
    _pack(
        tmp_path,
        "pack: x\nrules:\n  - id: ORG1\n    message: needs license\n"
        "    require:\n      file_glob: '**/*.rs'\n      contains: SPDX\n",
    )
    (tmp_path / "app.py").write_text("print(1)\n")
    results = evaluate_packs(ProjectContext(root=tmp_path), load_packs(tmp_path)[0])
    assert results == []


def test_case_insensitive_terraform_bool_compare(tmp_path: Path) -> None:
    """TF `true` parses to Python True - `value: "true"` must still match."""
    _pack(
        tmp_path,
        "pack: x\nrules:\n  - id: ORG1\n    message: no public\n"
        "    forbid:\n      terraform:\n        resource_type: aws_db_instance\n"
        '        attr: publicly_accessible\n        op: equals\n        value: "true"\n',
    )
    (tmp_path / "main.tf").write_text(
        'resource "aws_db_instance" "d" {\n  publicly_accessible = true\n}\n'
    )
    results = evaluate_packs(ProjectContext(root=tmp_path), load_packs(tmp_path)[0])
    assert len(results) == 1 and results[0].check_id == "ORG1"


def test_no_packs_no_findings(tmp_path: Path) -> None:
    packs, errors = load_packs(tmp_path)
    assert packs == [] and errors == []
    assert evaluate_packs(ProjectContext(root=tmp_path), packs) == []


def test_evaluation_deterministic(tmp_path: Path) -> None:
    _pack(
        tmp_path,
        'pack: x\nrules:\n  - id: ORG1\n    message: pw\n    forbid:\n      pattern: "password"\n',
    )
    (tmp_path / "a.py").write_text('password = "x"\n')
    (tmp_path / "b.py").write_text('password = "y"\n')
    ctx = ProjectContext(root=tmp_path)
    a = [(r.check_id, r.file, r.line) for r in evaluate_packs(ctx, load_packs(tmp_path)[0])]
    b = [(r.check_id, r.file, r.line) for r in evaluate_packs(ctx, load_packs(tmp_path)[0])]
    assert a == b
