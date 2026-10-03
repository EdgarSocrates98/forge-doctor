"""Safe-fix intelligence: deterministic, class-gated fix proposals.

``remediate`` describes *what to change*; ``fix`` can actually produce
bounded text transforms for findings whose repair is a pure, reversible
edit. Every proposal carries a safety class:

- ``safe`` — deterministic defaults / ignore-file bookkeeping. Applied
  by ``--apply``.
- ``review`` — real but reversible edits that deserve a human glance.
  Applied only by ``--apply --class review``.
- ``manual`` — dangerous classes (IaC resource semantics, IAM/Lake
  Formation, partition/table migration). Never has a transform; the
  planner emits guidance only and there is no code path that can apply
  one.

Transforms are pure ``(old_text) -> new_text`` functions evaluated
against the scanned tree. ``apply_fix`` re-reads the target and aborts
when the current bytes differ from the captured ``before`` — a stale
source is never patched. Writes never leave the project root and no
git operations are performed (version control is the rollback).
"""

from __future__ import annotations

import difflib
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from forge_doctor_data.core.models import Severity

if TYPE_CHECKING:
    from forge_doctor_data.core.context import ProjectContext
    from forge_doctor_data.core.models import CheckResult
    from forge_doctor_data.plugins.protocol import Check

SAFE = "safe"
REVIEW = "review"
MANUAL = "manual"
FIX_CLASSES = (SAFE, REVIEW, MANUAL)

# Categories whose remediation would edit IaC resource semantics, IAM /
# Lake Formation grants, table formats or partitioning: MANUAL_ONLY
# forever. Registering a transform for one of these raises ValueError.
MANUAL_ONLY_CATEGORIES: frozenset[str] = frozenset(
    {
        "terraform",
        "iac",
        "lakeformation",
        "aws",
        "dynamodb",
        "neptune",
        "serverless",
        "architecture",
        "streaming",
        "streaming-bus",
        "controlm",
        "glue",
        "stepfunctions",
    }
)


class FixRefused(RuntimeError):
    """A transform tried to run where safety rules forbid it."""


@dataclass(frozen=True)
class FixAction:
    """One deterministic, linkable fix proposal for a set of findings."""

    check_id: str  # primary check id (all contributing ids in ``check_ids``)
    check_ids: tuple[str, ...]
    fingerprints: tuple[str, ...]
    file: str  # project-relative posix path of the target
    title: str
    fix_class: str
    transform: str  # registry transform name
    before: str  # exact current bytes ("" when ``create``)
    after: str  # proposed bytes ("" when ``delete``)
    create: bool = False
    delete: bool = False
    guidance: str | None = None  # manual-class: what a human should do
    superseded: bool = False  # another action already claimed ``file``

    def diff(self) -> str:
        """Unified diff of the proposal; empty for delete/manual rows."""
        if self.fix_class == MANUAL:
            return ""
        if self.delete:
            return f"--- a/{self.file}\n+++ /dev/null\n(delete file)\n"
        before = self.before.splitlines(keepends=True)
        after = self.after.splitlines(keepends=True)
        a_name = f"a/{self.file}" if not self.create else "/dev/null"
        return "".join(difflib.unified_diff(before, after, a_name, f"b/{self.file}"))


Transform = Callable[["ProjectContext", list["CheckResult"]], list[FixAction]]

_TRANSFORMS: dict[str, Transform] = {}  # check_id -> transform
_TRANSFORM_NAMES: dict[int, str] = {}


def register_transform(
    transform: Transform,
    check_ids: tuple[str, ...],
    *,
    category_lookup: Callable[[str], str | None] | None = None,
) -> None:
    """Register ``transform`` for ``check_ids``. Manual-category check ids
    are rejected outright: there is intentionally no code path that can
    apply a transform to them."""
    if category_lookup is not None:
        for cid in check_ids:
            if category_lookup(cid) in MANUAL_ONLY_CATEGORIES:
                raise FixRefused(f"{cid}: checks in MANUAL_ONLY categories cannot carry transforms")
    for cid in check_ids:
        _TRANSFORMS[cid] = transform
        _TRANSFORM_NAMES[id(transform)] = transform.__name__


def _transform_name(transform: Transform) -> str:
    return _TRANSFORM_NAMES.get(id(transform), transform.__name__)


def plan_fixes(
    results: list[CheckResult],
    checks: list[Check],
    ctx: ProjectContext,
) -> list[FixAction]:
    """Turn findings into fix actions.

    Findings whose check has a registered transform go through it (each
    transform sees *all* its findings at once, so several findings can
    fold into one file edit). Unregistered findings in MANUAL_ONLY
    categories yield guidance rows. Everything else is not fixable and
    is left out. Actions claiming the same file are superseded in
    deterministic registration order.
    """
    categories = {c.id: c.category for c in checks}
    fixes = {c.id: getattr(c, "fix", None) for c in checks}
    by_transform: dict[int, tuple[Transform, list[CheckResult]]] = {}
    order: list[int] = []
    actions: list[FixAction] = []
    manual: list[FixAction] = []

    for result in results:
        if result.severity not in (Severity.WARNING, Severity.ERROR):
            continue
        transform = _TRANSFORMS.get(result.check_id)
        if transform is not None:
            key = id(transform)
            if key not in by_transform:
                by_transform[key] = (transform, [])
                order.append(key)
            by_transform[key][1].append(result)
        elif categories.get(result.check_id) in MANUAL_ONLY_CATEGORIES:
            manual.append(
                FixAction(
                    check_id=result.check_id,
                    check_ids=(result.check_id,),
                    fingerprints=(result.fingerprint or "",),
                    file=result.file.as_posix() if result.file else "",
                    title=f"{result.check_id} {result.title}",
                    fix_class=MANUAL,
                    transform="",
                    before="",
                    after="",
                    guidance=fixes.get(result.check_id) or result.recommendation,
                )
            )

    by_fp = {r.fingerprint: r for r in results}
    for key in order:
        transform, grouped = by_transform[key]
        for action in transform(ctx, grouped):
            for fp in action.fingerprints:
                if fp in by_fp:
                    object.__setattr__(by_fp[fp], "fixable", True)
            actions.append(action)

    # Deterministic conflict resolution: first action to claim a file
    # wins; later actions on the same path are reported as superseded.
    claimed: set[str] = set()
    resolved: list[FixAction] = []
    for action in actions:
        if action.file in claimed:
            resolved.append(replace(action, superseded=True))
        else:
            claimed.add(action.file)
            resolved.append(action)
    return resolved + manual


# --- transforms ------------------------------------------------------------


def _line_present(text: str, needle: str) -> bool:
    return any(line.strip() == needle for line in text.splitlines())


def requires_python_default(ctx: ProjectContext, results: list[CheckResult]) -> list[FixAction]:
    """PY002: insert ``requires-python = \">=3.10\"`` under ``[project]``."""
    target = Path("pyproject.toml")
    text = ctx.read_text(target)
    if text is None or "requires-python" in text:
        return []
    lines = text.splitlines(keepends=True)
    insert_at = None
    for i, line in enumerate(lines):
        if line.strip() == "[project]":
            insert_at = i + 1
            break
    if insert_at is None:
        return []  # poetry-style manifests keep their own constraint
    lines.insert(insert_at, 'requires-python = ">=3.10"\n')
    return [
        FixAction(
            check_id="PY002",
            check_ids=("PY002",),
            fingerprints=tuple(r.fingerprint or "" for r in results),
            file="pyproject.toml",
            title="Declare requires-python under [project]",
            fix_class=SAFE,
            transform="requires_python_default",
            before=text,
            after="".join(lines),
        )
    ]


_GITIGNORE_ARTIFACT_PATTERNS = ("__pycache__/", "*.py[cod]", "*.egg-info/")


def gitignore_patterns(ctx: ProjectContext, results: list[CheckResult]) -> list[FixAction]:
    """GIT002/GIT003: ensure ignore patterns for tracked sensitive /
    artifact files exist in ``.gitignore`` (untracking stays manual)."""
    patterns: set[str] = set()
    for result in results:
        if result.check_id == "GIT003":
            patterns.update(_GITIGNORE_ARTIFACT_PATTERNS)
        elif result.file is not None:
            patterns.add(PurePosixPath(result.file.as_posix()).name)
    if not patterns:
        return []
    existing = ctx.read_text(Path(".gitignore"))
    text = existing or ""
    missing = sorted(p for p in patterns if not _line_present(text, p))
    if not missing:
        return []
    sep = "" if not text or text.endswith("\n") else "\n"
    check_ids = tuple(sorted({r.check_id for r in results}))
    return [
        FixAction(
            check_id=check_ids[0],
            check_ids=check_ids,
            fingerprints=tuple(r.fingerprint or "" for r in results),
            file=".gitignore",
            title=f"Add {', '.join(missing)} to .gitignore",
            fix_class=SAFE,
            transform="gitignore_patterns",
            before=text,
            after=text + sep + "\n".join(missing) + "\n",
            create=existing is None,
        )
    ]


def create_gitignore(ctx: ProjectContext, results: list[CheckResult]) -> list[FixAction]:
    """REP004: create a minimal ``.gitignore`` when none exists."""
    if ctx.read_text(Path(".gitignore")) is not None:
        return []
    body = "__pycache__/\n*.py[cod]\n*.egg-info/\n.env\n.venv/\nbuild/\ndist/\n"
    return [
        FixAction(
            check_id="REP004",
            check_ids=("REP004",),
            fingerprints=tuple(r.fingerprint or "" for r in results),
            file=".gitignore",
            title="Create .gitignore with standard Python patterns",
            fix_class=SAFE,
            transform="create_gitignore",
            before="",
            after=body,
            create=True,
        )
    ]


def drop_requirements_txt(ctx: ProjectContext, results: list[CheckResult]) -> list[FixAction]:
    """REP007: propose deleting ``requirements.txt`` when it conflicts
    with ``poetry.lock`` (which manifest to keep is a human decision -
    REVIEW class, applied only with ``--apply --class review``)."""
    relevant = [r for r in results if "requirements.txt + poetry.lock" in r.message]
    if not relevant or ctx.read_text(Path("requirements.txt")) is None:
        return []
    before = ctx.read_text(Path("requirements.txt")) or ""
    return [
        FixAction(
            check_id="REP007",
            check_ids=("REP007",),
            fingerprints=tuple(r.fingerprint or "" for r in relevant),
            file="requirements.txt",
            title="Remove requirements.txt (keep poetry.lock)",
            fix_class=REVIEW,
            transform="drop_requirements_txt",
            before=before,
            after="",
            delete=True,
            guidance="Confirm poetry is the single dependency manager first.",
        )
    ]


TRANSFORM_CHECKS: dict[Transform, tuple[str, ...]] = {
    requires_python_default: ("PY002",),
    gitignore_patterns: ("GIT002", "GIT003"),
    create_gitignore: ("REP004",),
    drop_requirements_txt: ("REP007",),
}

for _transform, _ids in TRANSFORM_CHECKS.items():
    for _cid in _ids:
        _TRANSFORMS[_cid] = _transform
        _TRANSFORM_NAMES[id(_transform)] = _transform.__name__


# --- apply -----------------------------------------------------------------


def apply_fix(action: FixAction, root: Path) -> dict[str, object]:
    """Write one action to disk. Returns an audit record; refuses stale
    sources, manual classes, superseded actions, and paths outside root."""
    if action.fix_class == MANUAL:
        raise FixRefused(f"{action.check_id}: manual-class actions never apply")
    if action.superseded:
        return {"file": action.file, "status": "superseded"}
    resolved_root = root.resolve()
    target = (resolved_root / action.file).resolve()
    if not target.is_relative_to(resolved_root):
        raise FixRefused(f"{action.file}: target escapes the project root")
    current = target.read_text(encoding="utf-8") if target.exists() else ""
    if current != action.before:
        return {"file": action.file, "status": "stale-source"}
    if action.delete:
        target.unlink()
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(action.after, encoding="utf-8")
    return {
        "file": action.file,
        "check_id": action.check_id,
        "transform": action.transform,
        "fingerprints": list(action.fingerprints),
        "status": "deleted" if action.delete else ("created" if action.create else "updated"),
    }
