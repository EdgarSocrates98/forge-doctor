"""Organization policy packs - declarative forbid/require rules.

Teams encode org rules as data, not code:

.. code-block:: yaml

    pack: org-security
    version: "1.0"
    rules:
      - id: ORG001
        severity: error
        message: RDS instances must not be publicly accessible
        forbid:
          terraform:
            resource_type: aws_db_instance
            attr: publicly_accessible
            op: equals
            value: "true"
      - id: ORG002
        severity: warning
        message: CODEOWNERS is required
        require:
          file: CODEOWNERS

Packs are discovered at ``.forge-doctor/policy/*.yml|*.yaml|*.json``,
``policy.yml`` / ``org-policy.yml`` at the root, plus any paths in
``[tool.forge-doctor] policy_packs``. Evaluation is deterministic:
regexes on file text and attribute checks on the terraform model -
no code execution, no network.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Any

from forge_doctor.core.contract import _load_yaml
from forge_doctor.core.models import CheckResult, EvidenceKind, Severity

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext

POLICY_CATEGORY = "policy"
INVALID_PACK_CHECK_ID = "POLICY010"

_FORBID_OPS = ("equals", "matches", "present")
_REQUIRE_TF_OPS = ("equals", "matches", "present")
_SEVERITIES = ("error", "warning", "info")


class PolicyPackError(ValueError):
    """Raised when a policy pack fails to load or validate."""


@dataclass(frozen=True)
class ForbidRule:
    """Violation when the pattern/attribute IS present."""

    file_glob: str = ""  # file-content rules; empty = all files
    pattern: str = ""  # regex searched in file text
    resource_type: str = ""  # terraform resource type; "*" = any
    attr: str = ""
    op: str = ""  # equals | matches | present
    value: str = ""


@dataclass(frozen=True)
class RequireRule:
    """Violation when the required thing is absent."""

    file: str = ""  # glob that must match >=1 project file
    file_glob: str = ""  # files under glob must each satisfy `contains`
    contains: str = ""  # regex each file_glob match must contain
    resource_type: str = ""  # terraform resource type; "*" = any
    attr: str = ""  # attr each matching resource must satisfy
    op: str = "present"  # present | equals | matches
    value: str = ""


@dataclass(frozen=True)
class PolicyRule:
    id: str
    severity: str  # error | warning | info
    message: str
    forbid: ForbidRule | None = None
    require: RequireRule | None = None
    recommendation: str = ""


@dataclass(frozen=True)
class PolicyPack:
    name: str
    version: str
    rules: tuple[PolicyRule, ...]
    path: Path | None = field(default=None, compare=False)


# -- loading ---------------------------------------------------------------


def _parse_forbid(raw: Any, rid: str) -> ForbidRule:
    if not isinstance(raw, dict):
        raise PolicyPackError(f"rule {rid}: 'forbid' must be a mapping")
    tf = raw.get("terraform")
    if tf is not None:
        if not isinstance(tf, dict):
            raise PolicyPackError(f"rule {rid}: 'forbid.terraform' must be a mapping")
        op = str(tf.get("op", "present"))
        if op not in _FORBID_OPS:
            raise PolicyPackError(f"rule {rid}: forbid op '{op}' not in {_FORBID_OPS}")
        if not tf.get("attr") and op != "present":
            raise PolicyPackError(f"rule {rid}: terraform forbid needs 'attr'")
        return ForbidRule(
            resource_type=str(tf.get("resource_type", "*")),
            attr=str(tf.get("attr", "")),
            op=op,
            value=str(tf.get("value", "")),
        )
    pattern = raw.get("pattern")
    if pattern is None:
        raise PolicyPackError(f"rule {rid}: forbid needs 'pattern' or 'terraform'")
    try:
        re.compile(str(pattern))
    except re.error as exc:
        raise PolicyPackError(f"rule {rid}: bad regex {pattern!r}: {exc}") from exc
    return ForbidRule(file_glob=str(raw.get("file_glob", "**/*")), pattern=str(pattern))


def _parse_require(raw: Any, rid: str) -> RequireRule:
    if not isinstance(raw, dict):
        raise PolicyPackError(f"rule {rid}: 'require' must be a mapping")
    tf = raw.get("terraform")
    if tf is not None:
        if not isinstance(tf, dict):
            raise PolicyPackError(f"rule {rid}: 'require.terraform' must be a mapping")
        if not tf.get("attr"):
            raise PolicyPackError(f"rule {rid}: terraform require needs 'attr'")
        op = str(tf.get("op", "present"))
        if op not in _REQUIRE_TF_OPS:
            raise PolicyPackError(f"rule {rid}: require op '{op}' not in {_REQUIRE_TF_OPS}")
        return RequireRule(
            resource_type=str(tf.get("resource_type", "*")),
            attr=str(tf.get("attr", "")),
            op=op,
            value=str(tf.get("value", "")),
        )
    if raw.get("file"):
        return RequireRule(file=str(raw["file"]))
    if raw.get("file_glob") and raw.get("contains"):
        try:
            re.compile(str(raw["contains"]))
        except re.error as exc:
            raise PolicyPackError(f"rule {rid}: bad regex {raw['contains']!r}: {exc}") from exc
        return RequireRule(
            file_glob=str(raw["file_glob"]),
            contains=str(raw["contains"]),
        )
    raise PolicyPackError(
        f"rule {rid}: require needs 'file', 'file_glob'+'contains', or 'terraform'"
    )


def load_pack(path: Path) -> PolicyPack:
    """Load one policy pack file (``.yml``/``.yaml`` via the same loader
    as platform contracts, or ``.json``). Raises ``PolicyPackError``."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyPackError(f"cannot read {path}: {exc}") from exc
    if path.suffix == ".json":
        try:
            data: Any = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PolicyPackError(f"{path}: malformed JSON: {exc}") from exc
    else:
        data, err = _load_yaml(text)
        if err is not None:
            raise PolicyPackError(f"{path}: {err}")
    if not isinstance(data, dict):
        raise PolicyPackError(f"{path}: pack must be a mapping")
    name = str(data.get("pack", path.stem))
    version = str(data.get("version", "1"))
    raw_rules = data.get("rules")
    if not isinstance(raw_rules, list):
        raise PolicyPackError(f"{path}: 'rules' must be a list")
    rules: list[PolicyRule] = []
    seen: set[str] = set()
    for i, rr in enumerate(raw_rules):
        if not isinstance(rr, dict):
            raise PolicyPackError(f"{path}: rule #{i + 1} must be a mapping")
        rid = str(rr.get("id", ""))
        if not rid:
            raise PolicyPackError(f"{path}: rule #{i + 1} missing 'id'")
        if rid in seen:
            raise PolicyPackError(f"{path}: duplicate rule id '{rid}'")
        seen.add(rid)
        severity = str(rr.get("severity", "warning")).lower()
        if severity not in _SEVERITIES:
            raise PolicyPackError(f"{path}: rule {rid}: severity '{severity}' not in {_SEVERITIES}")
        if not rr.get("message"):
            raise PolicyPackError(f"{path}: rule {rid} missing 'message'")
        forbid = _parse_forbid(rr["forbid"], rid) if "forbid" in rr else None
        require = _parse_require(rr["require"], rid) if "require" in rr else None
        if forbid is None and require is None:
            raise PolicyPackError(f"{path}: rule {rid} needs 'forbid' or 'require'")
        rules.append(
            PolicyRule(
                id=rid,
                severity=severity,
                message=str(rr["message"]),
                forbid=forbid,
                require=require,
                recommendation=str(rr.get("recommendation", "")),
            )
        )
    return PolicyPack(name=name, version=version, rules=tuple(rules), path=path)


def discover_packs(root: Path, extra: tuple[str, ...] = ()) -> list[Path]:
    """Policy pack paths: ``.forge-doctor/policy/*``, root-level
    ``policy.yml``/``org-policy.yml``, plus config-declared paths."""
    found: list[Path] = []
    policy_dir = root / ".forge-doctor" / "policy"
    if policy_dir.is_dir():
        for p in sorted(policy_dir.iterdir()):
            if p.suffix in (".yml", ".yaml", ".json") and p.is_file():
                found.append(p)
    for name in ("policy.yml", "policy.yaml", "org-policy.yml", "org-policy.yaml"):
        p = root / name
        if p.is_file():
            found.append(p)
    for rel in extra:
        p = root / rel
        if p.is_file():
            found.append(p)
    return found


def load_packs(
    root: Path, extra: tuple[str, ...] = ()
) -> tuple[list[PolicyPack], list[CheckResult]]:
    """Load every discovered pack; unloadable packs become findings."""
    packs: list[PolicyPack] = []
    errors: list[CheckResult] = []
    for path in discover_packs(root, extra):
        try:
            packs.append(load_pack(path))
        except PolicyPackError as exc:
            errors.append(
                CheckResult(
                    check_id=INVALID_PACK_CHECK_ID,
                    title="Invalid policy pack",
                    severity=Severity.ERROR,
                    category=POLICY_CATEGORY,
                    message=str(exc),
                )
            )
    return packs, errors


# -- evaluation ------------------------------------------------------------


def _sev(value: str) -> Severity:
    try:
        return Severity.parse(value)
    except ValueError:
        return Severity.WARNING


def _finding(
    rule: PolicyRule,
    pack: PolicyPack,
    message: str,
    file: Path | None = None,
    line: int | None = None,
    evidence: str | None = None,
) -> CheckResult:
    return CheckResult(
        check_id=rule.id,
        title=rule.message[:60],
        severity=_sev(rule.severity),
        category=POLICY_CATEGORY,
        message=f"[{pack.name}] {message}",
        recommendation=rule.recommendation or None,
        file=file,
        line=line,
        evidence=evidence,
        evidence_kind=EvidenceKind.CONFIG,
    )


def _tf_resources(ctx: ProjectContext) -> list[Any]:
    from forge_doctor.analyzers.terraform_model import terraform_model

    return terraform_model(ctx).resources


def _glob(posix: str, pattern: str) -> bool:
    """``**/*.py`` should match ``app.py`` at the root too - fnmatch's
    ``**/`` requires a directory component, so strip it as a fallback."""
    return fnmatch(posix, pattern) or (pattern.startswith("**/") and fnmatch(posix, pattern[3:]))


def _tf_op_ok(res_attrs: dict[str, Any], attr: str, op: str, value: str) -> bool:
    present = attr in res_attrs
    if op == "present":
        return present
    if not present:
        return False
    actual = str(res_attrs[attr])
    if op == "equals":
        # Terraform booleans arrive as Python True/False - compare
        # case-insensitively so `value: "true"` matches `true`.
        return actual.lower() == value.lower()
    return bool(re.search(value, actual))  # op == "matches"


def _eval_forbid(
    rule: PolicyRule, fr: ForbidRule, pack: PolicyPack, ctx: ProjectContext
) -> list[CheckResult]:
    out: list[CheckResult] = []
    if fr.resource_type:  # terraform rule
        for res in _tf_resources(ctx):
            rtype = res.labels[0] if res.labels else ""
            if fr.resource_type not in ("*", rtype):
                continue
            if _tf_op_ok(dict(res.attrs), fr.attr, fr.op, fr.value):
                out.append(
                    _finding(
                        rule,
                        pack,
                        f"{rule.message} ({res.address})",
                        file=res.file,
                        line=res.line,
                        evidence=f"{res.address}: {fr.attr}{fr.op} {fr.value}".strip(),
                    )
                )
        return out
    rx = re.compile(fr.pattern)
    for f in sorted(ctx.files, key=lambda p: p.as_posix()):
        if not _glob(f.as_posix(), fr.file_glob):
            continue
        text = ctx.read_text(f)
        if text is None:
            continue
        for lineno, line_text in enumerate(text.splitlines(), 1):
            if rx.search(line_text):
                out.append(
                    _finding(
                        rule, pack, rule.message, file=f, line=lineno, evidence=line_text.strip()
                    )
                )
    return out


def _eval_require(
    rule: PolicyRule, rr: RequireRule, pack: PolicyPack, ctx: ProjectContext
) -> list[CheckResult]:
    out: list[CheckResult] = []
    if rr.file:  # a file must exist
        if not any(_glob(f.as_posix(), rr.file) for f in ctx.files):
            out.append(_finding(rule, pack, f"{rule.message} (missing {rr.file})"))
        return out
    if rr.file_glob:  # each matching file must contain the pattern
        rx = re.compile(rr.contains)
        for f in sorted(ctx.files, key=lambda p: p.as_posix()):
            if not _glob(f.as_posix(), rr.file_glob):
                continue
            text = ctx.read_text(f)
            if text is None or not rx.search(text):
                out.append(_finding(rule, pack, rule.message, file=f))
        return out
    # terraform require: every matching resource must satisfy the attr rule
    for res in _tf_resources(ctx):
        rtype = res.labels[0] if res.labels else ""
        if rr.resource_type not in ("*", rtype):
            continue
        if not _tf_op_ok(dict(res.attrs), rr.attr, rr.op, rr.value):
            out.append(
                _finding(
                    rule,
                    pack,
                    f"{rule.message} ({res.address} lacks {rr.attr})",
                    file=res.file,
                    line=res.line,
                    evidence=res.address,
                )
            )
    return out


def evaluate_packs(ctx: ProjectContext, packs: list[PolicyPack]) -> list[CheckResult]:
    """Evaluate all rules deterministically; returns violation findings."""
    results: list[CheckResult] = []
    for pack in packs:
        for rule in pack.rules:
            if rule.forbid is not None:
                results.extend(_eval_forbid(rule, rule.forbid, pack, ctx))
            if rule.require is not None:
                results.extend(_eval_require(rule, rule.require, pack, ctx))
    return results
