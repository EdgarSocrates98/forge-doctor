"""Forge Lab - reproducible scenario suites with declared ground truth.

A lab scenario is a directory under ``labs/<domain>/<scenario>/`` holding a
realistic mini-project plus an ``expected.json`` ground-truth file::

    {
      "name": "glue4-small-files",
      "description": "Glue 4 job writing tiny parquet partitions",
      "expected_findings": ["PARQ040", "SPARK003@jobs/etl.py"],
      "forbidden_findings": ["DELTA003"],
      "expected_graph_edges": ["writes|compute_job:glue:etl->dataset:parquet:out"],
      "expected_capabilities": ["glue.iceberg@4.0=conditional"],
      "expected_root_causes": ["RC_STREAM_COMMITS"]
    }

Finding entries are check ids, optionally ``ID@file-fragment`` to pin a
location.  Graph edges are ``<rel-kind>|<src>-><dst>`` strings.  Capability
entries are ``<capability-id>[@version]=<status>`` where the platform is the
first ``.``-separated segment of the id.  Optional ``runtime/`` artifacts are
ingested for root-cause clustering.

Everything is deterministic and offline: no execution, no cloud calls.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from forge_doctor.core.models import Severity

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext

EXPECTED_FILE = "expected.json"
RUNTIME_DIR = "runtime"

_FINDING_SEVERITIES = frozenset({Severity.INFO, Severity.WARNING, Severity.ERROR})


@dataclass(frozen=True)
class GroundTruth:
    """Declared expectations for one scenario (all fields optional)."""

    name: str = ""
    description: str = ""
    expected_findings: tuple[str, ...] = ()
    forbidden_findings: tuple[str, ...] = ()
    expected_graph_edges: tuple[str, ...] = ()
    expected_capabilities: tuple[str, ...] = ()
    expected_root_causes: tuple[str, ...] = ()
    # Detected ids that are known-benign in this scenario (repo-generic
    # warnings like REP001). Extras not covered here count as FP candidates.
    allowed_findings: tuple[str, ...] = ()


@dataclass(frozen=True)
class FindingExpectation:
    """``ID`` or ``ID@file-fragment`` expectation."""

    check_id: str
    file_fragment: str = ""


@dataclass
class CategoryResult:
    """Comparison outcome for one ground-truth category."""

    expected: list[str] = field(default_factory=list)
    actual: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)  # expected but absent
    forbidden_hit: list[str] = field(default_factory=list)  # declared-must-not-exist, present
    extra: list[str] = field(default_factory=list)  # detected, not expected (FP candidates)

    @property
    def ok(self) -> bool:
        return not self.missed and not self.forbidden_hit


@dataclass
class ScenarioReport:
    """Full comparison of engine output vs ground truth for one scenario."""

    scenario: str
    path: Path
    findings: CategoryResult = field(default_factory=CategoryResult)
    graph_edges: CategoryResult = field(default_factory=CategoryResult)
    capabilities: CategoryResult = field(default_factory=CategoryResult)
    root_causes: CategoryResult = field(default_factory=CategoryResult)
    errors: list[str] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return (
            self.findings.ok
            and self.graph_edges.ok
            and self.capabilities.ok
            and self.root_causes.ok
            and not self.errors
        )

    @property
    def categories(self) -> tuple[tuple[str, CategoryResult], ...]:
        return (
            ("findings", self.findings),
            ("graph_edges", self.graph_edges),
            ("capabilities", self.capabilities),
            ("root_causes", self.root_causes),
        )


@dataclass
class LabReport:
    """Aggregate over all scenarios in a lab root."""

    root: Path
    reports: list[ScenarioReport] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.reports if r.passed)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.reports if not r.passed)

    @property
    def ok(self) -> bool:
        return self.failed == 0


def load_ground_truth(path: Path) -> GroundTruth:
    """Parse ``expected.json``; missing file → all-empty truth."""
    if not path.is_file():
        return GroundTruth()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return GroundTruth(name=f"__invalid__({exc})")
    if not isinstance(raw, dict):
        return GroundTruth(name="__invalid__(not an object)")

    def _lst(key: str) -> tuple[str, ...]:
        value = raw.get(key)
        if not isinstance(value, list):
            return ()
        return tuple(sorted(str(v) for v in value))

    return GroundTruth(
        name=str(raw.get("name") or path.parent.name),
        description=str(raw.get("description") or ""),
        expected_findings=_lst("expected_findings"),
        forbidden_findings=_lst("forbidden_findings"),
        expected_graph_edges=_lst("expected_graph_edges"),
        expected_capabilities=_lst("expected_capabilities"),
        expected_root_causes=_lst("expected_root_causes"),
        allowed_findings=_lst("allowed_findings"),
    )


def discover_scenarios(labs_root: Path) -> list[Path]:
    """All directories containing ``expected.json``, sorted for determinism."""
    if not labs_root.is_dir():
        return []
    return sorted(p.parent for p in labs_root.rglob(EXPECTED_FILE) if p.is_file())


def parse_finding_expectation(entry: str) -> FindingExpectation:
    check_id, _, frag = entry.partition("@")
    return FindingExpectation(check_id=check_id.strip(), file_fragment=frag.strip())


def _finding_key(severity: Severity, check_id: str, file: Path | None) -> str:
    loc = file.as_posix() if file else ""
    return f"{check_id}@{loc}" if loc else check_id


def _match_finding(exp: FindingExpectation, actual_keys: set[str]) -> bool:
    for key in actual_keys:
        check_id, _, loc = key.partition("@")
        if check_id != exp.check_id:
            continue
        if not exp.file_fragment or exp.file_fragment in loc:
            return True
    return False


def _run_checks(ctx: ProjectContext) -> list[Any]:
    from forge_doctor.checks import builtin_checks

    results: list[Any] = []
    for check in builtin_checks():
        results.extend(check.run(ctx))
    return results


def _runtime_models(scenario: Path) -> list[Any]:
    """Ingest ``runtime/`` artifacts for root-cause clustering."""
    runtime_dir = scenario / RUNTIME_DIR
    if not runtime_dir.is_dir():
        return []
    from forge_doctor.analyzers.runtime_evidence import ingest_artifact

    models = []
    for artifact in sorted(runtime_dir.rglob("*")):
        if artifact.is_file():
            model = ingest_artifact(artifact)
            if model.source not in {"unknown", "unreadable"}:
                models.append(model)
    return models


def _graph_edge_keys(ctx: ProjectContext) -> set[str]:
    from forge_doctor.analyzers.platform_graph_builder import build_platform_graph

    graph = build_platform_graph(ctx)
    return {f"{rel.kind.value.lower()}|{rel.src}->{rel.dst}" for rel in graph.relationships()}


def _capability_status(ctx: ProjectContext, entry: str) -> tuple[str, str]:
    """``[platform:]CAP_ID[@version][;attr=v;...]=expected`` → (actual, display).

    The expected status is split at the *last* ``=`` so ``;attr=v`` context
    pairs can appear before it.
    """
    from forge_doctor.core.capabilities import capability_registry

    spec, _, _expected = entry.rpartition("=")
    cap_part, _, version = spec.partition("@")
    segments = cap_part.split(";")
    platform, _, cap_id = segments[0].partition(":")
    if not cap_id:  # no platform prefix - derive from a dotted id
        cap_id = platform
        platform = cap_id.split(".", 1)[0]
    kwargs: dict[str, Any] = {"platform": platform.strip()}
    if version.strip():
        kwargs["version"] = version.strip()
    for seg in segments[1:]:
        key, _, val = seg.partition("=")
        if key.strip():
            kwargs[key.strip()] = val.strip()
    result = capability_registry().evaluate(cap_id.strip(), **kwargs)
    return result.status.value, f"{spec.strip()}={result.status.value}"


def run_scenario(scenario: Path, truth: GroundTruth | None = None) -> ScenarioReport:
    """Run the full engine over one scenario and compare to ground truth."""
    from forge_doctor.core.context import ProjectContext, ScanOptions
    from forge_doctor.core.diagnosis import cluster_findings

    truth = truth or load_ground_truth(scenario / EXPECTED_FILE)
    report = ScenarioReport(scenario=truth.name or scenario.name, path=scenario)
    if truth.name.startswith("__invalid__"):
        report.errors.append(f"invalid expected.json: {truth.name}")
        return report
    ctx = ProjectContext(root=scenario.resolve(), options=ScanOptions(hermetic=True))

    results = _run_checks(ctx)
    actual_keys = {
        _finding_key(r.severity, r.check_id, r.file)
        for r in results
        if r.severity in _FINDING_SEVERITIES
    }
    actual_ids = sorted({k.partition("@")[0] for k in actual_keys})

    fcat = report.findings
    fcat.actual = actual_ids
    for entry in truth.expected_findings:
        exp = parse_finding_expectation(entry)
        fcat.expected.append(entry)
        if not _match_finding(exp, actual_keys):
            fcat.missed.append(entry)
    for entry in truth.forbidden_findings:
        exp = parse_finding_expectation(entry)
        if _match_finding(exp, actual_keys):
            fcat.forbidden_hit.append(entry)
    expected_ids = {parse_finding_expectation(e).check_id for e in truth.expected_findings}
    fcat.extra = sorted(cid for cid in actual_ids if cid not in expected_ids)
    # FP candidates: undeclared findings at WARNING+ severity (INFO anchors
    # describe surface, not defects, so they never count as FPs).
    warn_ids = {r.check_id for r in results if r.severity in (Severity.WARNING, Severity.ERROR)}
    allowed = set(truth.allowed_findings)
    unaccounted = [c for c in fcat.extra if c not in allowed]
    report.stats["fp_candidates"] = len([c for c in unaccounted if c in warn_ids])
    report.stats["extra_info"] = len([c for c in unaccounted if c not in warn_ids])
    report.stats["allowed_hits"] = len([c for c in fcat.extra if c in allowed])
    fcat.extra = unaccounted  # report only genuinely unreviewed extras

    edges = _graph_edge_keys(ctx)
    report.stats["graph_edges"] = len(edges)
    gcat = report.graph_edges
    gcat.actual = sorted(edges)
    if truth.expected_graph_edges:
        gcat.expected = list(truth.expected_graph_edges)
        gcat.missed = [e for e in truth.expected_graph_edges if e not in edges]

    if truth.expected_capabilities:
        ccat = report.capabilities
        for entry in truth.expected_capabilities:
            _spec, _, expected_status = entry.rpartition("=")
            ccat.expected.append(entry)
            try:
                actual_status, display = _capability_status(ctx, entry)
            except Exception as exc:  # pack/registry failure → miss, recorded
                ccat.missed.append(f"{entry} (error: {exc})")
                continue
            ccat.actual.append(display)
            if actual_status != expected_status.strip().lower():
                ccat.missed.append(f"{entry} (got {actual_status})")

    # Engine stats for the metrics layer (Phase 2): parser coverage,
    # graph size, runtime correlation surface.
    from forge_doctor.analyzers.index import project_index

    modules = project_index(ctx).modules
    py_files = len(modules)
    report.stats["py_files"] = py_files
    report.stats["py_parsed"] = sum(1 for m in modules.values() if m.tree is not None)

    models = _runtime_models(scenario)
    report.stats["runtime_models"] = len(models)
    if truth.expected_root_causes:
        rcat = report.root_causes
        clusters = cluster_findings(results, models)
        cluster_ids = sorted({c.id for c in clusters})
        rcat.actual = cluster_ids
        rcat.expected = list(truth.expected_root_causes)
        rcat.missed = [
            e
            for e in truth.expected_root_causes
            if not any(cid.startswith(e) for cid in cluster_ids)
        ]

    return report


DEFAULTS_FILE = "_defaults.json"


def _merge_defaults(truth: GroundTruth, labs_root: Path) -> GroundTruth:
    """Layer ``labs/_defaults.json`` allowed findings under the scenario's."""
    defaults = load_ground_truth(labs_root / DEFAULTS_FILE)
    if not defaults.allowed_findings:
        return truth
    return GroundTruth(
        name=truth.name,
        description=truth.description,
        expected_findings=truth.expected_findings,
        forbidden_findings=truth.forbidden_findings,
        expected_graph_edges=truth.expected_graph_edges,
        expected_capabilities=truth.expected_capabilities,
        expected_root_causes=truth.expected_root_causes,
        allowed_findings=tuple(
            sorted(set(defaults.allowed_findings) | set(truth.allowed_findings))
        ),
    )


def run_lab(labs_root: Path, scenario: str | None = None) -> LabReport:
    """Run every discovered scenario (or a named one) under ``labs_root``."""
    report = LabReport(root=labs_root)
    for directory in discover_scenarios(labs_root):
        if scenario and directory.name != scenario:
            continue
        truth = _merge_defaults(load_ground_truth(directory / EXPECTED_FILE), labs_root)
        report.reports.append(run_scenario(directory, truth))
    return report
