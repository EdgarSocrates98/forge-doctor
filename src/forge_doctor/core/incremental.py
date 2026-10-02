"""Continuous incremental analysis (spec 207).

Every check resolves to the *evidence domains* it can observe - file
families (``python``, ``terraform``, ...), project inputs (``packaging``,
``contract``), or host state (``env``, ``git``, ``host``). A changed file
invalidates its domains; checks whose declared domains intersect the
change rerun while the rest reuse the previous scan's results.

Host-state domains can drift without any file event, so checks declaring
them - plus ``unbounded`` checks consuming derived cross-domain state -
always rerun. Correctness over speed: when in doubt, run the check.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from forge_doctor.core.cache import cache_root
from forge_doctor.core.models import (
    CheckResult,
    Confidence,
    EvidenceKind,
    Severity,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from forge_doctor.core.context import ProjectContext
    from forge_doctor.plugins.protocol import Check

# -- Evidence domains --------------------------------------------------------
#
# A domain is a channel a check can observe. File-family domains map a
# changed file to the models it feeds; host domains (env/git/host) can move
# without any file event, so checks declaring them always rerun.

PYTHON = "python"  # .py/.pyi - AST index, boto3, spark/glue/streaming models
CODE = "code"  # other source languages - .scala/.sh/.bat/.cmd/.ps1
SQL = "sql"  # .sql/.hql
TERRAFORM = "terraform"  # .tf/.tfvars/.hcl
CONFIG = "config"  # .yml/.yaml/.json/.toml/.ini/.cfg/.conf/.properties/.xml
NOTEBOOK = "notebook"  # .ipynb
GRAPH = "graph"  # cypher/gremlin/sparql/rdf families
PACKAGING = "packaging"  # pyproject.toml, requirements, lock files
DOCKER = "docker"  # Dockerfile, compose, .dockerignore
CI = "ci"  # workflow/pipeline definitions
RUNTIME = "runtime"  # runtime/ evidence artifacts
CONTRACT = "contract"  # platform-contract.*
FILES = "files"  # file-tree enumeration (names, presence)

ENV = "env"  # host environment variables
GIT = "git"  # git index / tracked-file set
HOST = "host"  # host tools, home dir, PATH interpreters
UNBOUNDED = "unbounded"  # derived multi-domain state - cannot be bounded

# Domains that can change without a file event: checks declaring them are
# conservative always-run.
ALWAYS_RUN = frozenset({ENV, GIT, HOST, UNBOUNDED})

# Module-level declarations: checks in a module share the same evidence
# surface (they consume the same analyzers/models). A check class may
# override with its own ``evidence_domains`` attribute; plugin checks
# without a declaration resolve to UNBOUNDED (conservative).
MODULE_DOMAINS: dict[str, frozenset[str]] = {
    "airflow": frozenset({PYTHON, PACKAGING}),
    "analytical": frozenset({SQL, CONFIG, FILES}),
    "architecture": frozenset({CONTRACT}),
    "aws": frozenset({ENV, HOST}),
    "bigquery": frozenset({SQL, TERRAFORM, CONFIG, FILES}),
    "ci": frozenset({CI, FILES}),
    "controlm": frozenset({CONFIG, CODE, FILES}),
    "datacontract": frozenset({SQL, TERRAFORM, CONFIG, FILES}),
    "dbt": frozenset({SQL, CONFIG, FILES}),
    "dependencies": frozenset({PACKAGING, HOST}),
    "docker": frozenset({DOCKER, FILES}),
    "dynamodb": frozenset({PYTHON, TERRAFORM}),
    "git_checks": frozenset({GIT}),
    "glue": frozenset({PYTHON}),
    "graph": frozenset({GRAPH, PYTHON}),
    "iac": frozenset({TERRAFORM, CONFIG}),
    "iceberg": frozenset({SQL, CONFIG, PYTHON, TERRAFORM}),
    "lakeformation": frozenset({PYTHON, TERRAFORM, CONFIG}),
    "neptune": frozenset({PYTHON, TERRAFORM, GRAPH}),
    "parquet": frozenset({PYTHON, SQL, CONFIG}),
    "platform_rules": frozenset({UNBOUNDED}),
    "platforms": frozenset({CODE, PYTHON, CONFIG, NOTEBOOK, SQL, TERRAFORM}),
    "policy_pack": frozenset({PACKAGING, CONFIG, FILES}),
    "python_env": frozenset({ENV, HOST, PACKAGING, CONFIG, FILES}),
    "redshift": frozenset({SQL, TERRAFORM, CONFIG, FILES}),
    "search": frozenset({CONFIG, TERRAFORM, FILES}),
    "repository": frozenset({FILES}),
    "serverless": frozenset({PYTHON, SQL, TERRAFORM, CONFIG}),
    "snowflake": frozenset({SQL, TERRAFORM, CONFIG, FILES}),
    "spark": frozenset({PYTHON}),
    "sql": frozenset({SQL}),
    "stepfunctions": frozenset({TERRAFORM, CONFIG}),
    "streaming": frozenset({PYTHON}),
    "streaming_bus": frozenset({PYTHON, CONFIG}),
    "terraform": frozenset({TERRAFORM}),
    "trino": frozenset({CONFIG, SQL, FILES}),
    "warehouse": frozenset({TERRAFORM, SQL}),
}

_UNBOUNDED_SET = frozenset({UNBOUNDED})

_PACKAGING_NAMES = frozenset(
    {
        "pyproject.toml",
        "poetry.lock",
        "uv.lock",
        "pipfile",
        "pipfile.lock",
        "setup.cfg",
        "setup.py",
        "manifest.in",
        "pdm.lock",
        "requirements.txt",
    }
)

_CONTRACT_NAMES = frozenset(
    {
        "platform-contract.yml",
        "platform-contract.yaml",
        "platform.contract.yml",
        "platform.contract.yaml",
    }
)

_SUFFIX_DOMAINS: dict[str, str] = {
    ".py": PYTHON,
    ".pyi": PYTHON,
    ".scala": CODE,
    ".sh": CODE,
    ".bash": CODE,
    ".bat": CODE,
    ".cmd": CODE,
    ".ps1": CODE,
    ".sql": SQL,
    ".hql": SQL,
    ".tf": TERRAFORM,
    ".tfvars": TERRAFORM,
    ".hcl": TERRAFORM,
    ".ipynb": NOTEBOOK,
    ".cql": GRAPH,
    ".cypher": GRAPH,
    ".gremlin": GRAPH,
    ".opencypher": GRAPH,
    ".rq": GRAPH,
    ".sparql": GRAPH,
    ".nt": GRAPH,
    ".rdf": GRAPH,
    ".ttl": GRAPH,
    ".yml": CONFIG,
    ".yaml": CONFIG,
    ".json": CONFIG,
    ".toml": CONFIG,
    ".ini": CONFIG,
    ".cfg": CONFIG,
    ".conf": CONFIG,
    ".properties": CONFIG,
    ".xml": CONFIG,
}

_CI_NAMES = frozenset(
    {
        ".gitlab-ci.yml",
        ".gitlab-ci.yaml",
        "azure-pipelines.yml",
        "azure-pipelines.yaml",
        "bitbucket-pipelines.yml",
        "bitbucket-pipelines.yaml",
        ".drone.yml",
        "jenkinsfile",
    }
)


def check_domains(check: Check) -> frozenset[str]:
    """Evidence domains a check observes - per-check attr, else module map.

    Anything undeclared resolves to ``unbounded`` so the check always runs:
    conservative by construction, per the spec's correctness-first rule.
    """
    declared = getattr(check, "evidence_domains", None)
    if declared:
        return frozenset(declared)
    module = type(check).__module__.rsplit(".", 1)[-1]
    return MODULE_DOMAINS.get(module, _UNBOUNDED_SET)


def classify_path(relative: str) -> frozenset[str]:
    """Evidence domains one changed file can feed. Always includes ``files``."""
    path = PurePosixPath(relative)
    name = path.name.lower()
    parts = tuple(part.lower() for part in path.parts)
    domains: set[str] = {FILES}

    if "runtime" in parts[:-1]:
        domains.add(RUNTIME)
    if name in _CONTRACT_NAMES:
        domains.add(CONTRACT)
    if name in _PACKAGING_NAMES or name.startswith(("requirements", "constraints")):
        domains.add(PACKAGING)
    if name.startswith(("dockerfile", ".dockerignore")) or name in {
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yml",
        "compose.yaml",
    }:
        domains.add(DOCKER)
    if (
        name in _CI_NAMES
        or name.startswith("jenkinsfile")
        or (".github" in parts and "workflows" in parts)
        or ".circleci" in parts
    ):
        domains.add(CI)

    domain = _SUFFIX_DOMAINS.get(path.suffix.lower())
    if domain is not None:
        domains.add(domain)
    return frozenset(domains)


@dataclass(frozen=True)
class IncrementalPlan:
    """What one change set invalidates - the rerun/reuse decision."""

    changed_files: frozenset[str]
    invalidated: frozenset[str]  # evidence domains touched by the change
    rerun: frozenset[str]  # check ids that must execute
    reuse: frozenset[str]  # check ids eligible for cached results


def plan_incremental(changed: frozenset[str], checks: list[Check]) -> IncrementalPlan:
    """Split selected checks into rerun vs reuse for a change set."""
    invalidated: set[str] = set()
    for relative in changed:
        invalidated |= set(classify_path(relative))
    rerun: set[str] = set()
    reuse: set[str] = set()
    for check in checks:
        domains = check_domains(check)
        if domains & ALWAYS_RUN or domains & frozenset(invalidated):
            rerun.add(check.id)
        else:
            reuse.add(check.id)
    return IncrementalPlan(
        changed_files=changed,
        invalidated=frozenset(invalidated),
        rerun=frozenset(rerun),
        reuse=frozenset(reuse),
    )


def file_states(ctx: ProjectContext) -> dict[str, str]:
    """``relpath -> "mtime_ns:size"`` - cheap stat-based change detection."""
    states: dict[str, str] = {}
    for relative in ctx.files:
        try:
            stat = (ctx.root / relative).stat()
        except OSError:
            continue
        states[relative.as_posix()] = f"{stat.st_mtime_ns}:{stat.st_size}"
    return states


def detect_changes(ctx: ProjectContext, prior_states: Mapping[str, str]) -> frozenset[str]:
    """Diff the current tree against stored states.

    Cold start (no prior states) reports every file changed - a full run,
    which is the conservative answer.
    """
    current = file_states(ctx)
    if not prior_states:
        return frozenset(current)
    changed = {p for p, state in current.items() if prior_states.get(p) != state}
    changed |= set(prior_states) - set(current)  # deleted files
    return frozenset(changed)


def result_from_dict(payload: dict[str, Any]) -> CheckResult | None:
    """Inverse of ``json_renderer.result_to_dict``; None on malformed rows."""
    check_id = payload.get("check_id")
    severity = payload.get("severity")
    if not isinstance(check_id, str) or not isinstance(severity, str):
        return None
    try:
        sev = Severity.parse(severity)
    except ValueError:
        return None
    file = payload.get("file")
    confidence = payload.get("confidence")
    evidence_kind = payload.get("evidence_kind")
    tags = payload.get("tags")
    return CheckResult(
        check_id=check_id,
        title=str(payload.get("title") or ""),
        severity=sev,
        category=str(payload.get("category") or ""),
        message=str(payload.get("message") or ""),
        file=Path(file) if isinstance(file, str) else None,
        line=payload.get("line") if isinstance(payload.get("line"), int) else None,
        column=payload.get("column") if isinstance(payload.get("column"), int) else None,
        end_line=(payload.get("end_line") if isinstance(payload.get("end_line"), int) else None),
        end_column=(
            payload.get("end_column") if isinstance(payload.get("end_column"), int) else None
        ),
        recommendation=payload.get("recommendation"),
        confidence=Confidence.parse(confidence) if isinstance(confidence, str) else None,
        evidence=payload.get("evidence"),
        evidence_kind=(
            EvidenceKind.parse(evidence_kind) if isinstance(evidence_kind, str) else None
        ),
        tags=tuple(str(t) for t in tags) if isinstance(tags, list) else (),
        docs_uri=payload.get("docs_uri"),
        source=payload.get("source"),
        fixable=payload.get("fixable") if isinstance(payload.get("fixable"), bool) else None,
        fingerprint=payload.get("fingerprint"),
        symbol=payload.get("symbol"),
    )


RESULTS_SCHEMA = 1


class ResultStore:
    """Prior-scan results + file states - the baseline ``--incremental`` diffs.

    Lives beside the scan cache (platform user cache dir, repo-keyed,
    tool-version-keyed). It is derived data: absent or corrupt means cold
    start = full scan, never an error.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._states: dict[str, str] = {}
        self._results: dict[str, list[CheckResult]] = {}
        self._load()

    @classmethod
    def for_root(cls, root: Path) -> ResultStore:
        from forge_doctor import __version__

        repo_key = hashlib.sha256(root.resolve().as_posix().encode()).hexdigest()[:16]
        return cls(cache_root() / f"results-{repo_key}-v{__version__}.json")

    @property
    def file_states(self) -> dict[str, str]:
        return self._states

    @property
    def results_by_check(self) -> dict[str, list[CheckResult]]:
        return self._results

    def detect_changes(self, ctx: ProjectContext) -> frozenset[str]:
        return detect_changes(ctx, self._states)

    def save(self, results: list[CheckResult], ctx: ProjectContext) -> None:
        from forge_doctor.output.json_renderer import result_to_dict

        grouped: dict[str, list[dict[str, Any]]] = {}
        for result in results:
            grouped.setdefault(result.check_id, []).append(result_to_dict(result))
        payload = {
            "schema": RESULTS_SCHEMA,
            "files": file_states(ctx),
            "results": grouped,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass  # cache writes are best-effort

    def _load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(payload, dict) or payload.get("schema") != RESULTS_SCHEMA:
            return
        states = payload.get("files")
        if isinstance(states, dict):
            self._states = {str(k): str(v) for k, v in states.items()}
        results = payload.get("results")
        if isinstance(results, dict):
            for check_id, rows in results.items():
                if not isinstance(rows, list):
                    continue
                parsed = [r for r in (result_from_dict(row) for row in rows) if r is not None]
                if parsed:
                    self._results[str(check_id)] = parsed
