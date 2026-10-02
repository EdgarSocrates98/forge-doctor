"""Finding promotion and root-cause clustering.

Correlates static/config findings with offline runtime evidence:

- :func:`promote_findings` lifts a finding's confidence/severity when runtime
  artifacts contain facts consistent with it. The original finding is never
  mutated - a ``FindingPromotion`` references it by ``base_fingerprint`` so
  baseline correlation keeps working.
- :func:`cluster_findings` evaluates deterministic causal-chain templates
  (e.g. microbatch -> commit amplification -> small files -> consumer
  overhead) and emits a ``FindingCluster`` per chain, graded
  CONFIRMED / STRONGLY_SUPPORTED / POSSIBLE by how much of the chain is
  evidenced and whether runtime facts back it.

Everything here is deterministic and offline: same inputs produce the same
promotions, clusters, and ids.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from forge_doctor.core.models import CheckResult, Confidence, Severity
from forge_doctor.core.runtime_evidence import RuntimeEvidenceModel


class PromotionLevel(Enum):
    """Causal confidence for promotions and clusters, ordered low to high."""

    POSSIBLE = "possible"
    STRONGLY_SUPPORTED = "strongly_supported"
    CONFIRMED = "confirmed"


@dataclass(frozen=True)
class FindingPromotion:
    """Runtime-confirmed view of an existing finding.

    ``base_fingerprint`` preserves correlation with the original finding;
    ``promotion_id`` is a deterministic id derived from the fingerprint and
    the confirming facts, so the same evidence always yields the same id.
    """

    base_fingerprint: str
    promotion_id: str
    check_id: str
    title: str
    confirming_evidence: tuple[str, ...]
    resulting_confidence: Confidence
    resulting_severity: Severity
    level: PromotionLevel
    explanation: str


@dataclass(frozen=True)
class CausalEdge:
    """Directed edge between two nodes of a causal chain."""

    source: str
    target: str
    label: str


@dataclass(frozen=True)
class FindingCluster:
    """A causal chain tying root cause(s) to observed symptoms."""

    id: str
    title: str
    root_causes: tuple[str, ...]
    symptoms: tuple[str, ...]
    related_findings: tuple[str, ...]
    affected_entities: tuple[str, ...]
    evidence: tuple[str, ...]
    confidence: PromotionLevel
    causal_edges: tuple[CausalEdge, ...]


# --- shared helpers ------------------------------------------------------------


def _metric(model: RuntimeEvidenceModel, name: str) -> list[float]:
    return [m.value for m in model.metrics if m.name == name]


def _metric_scoped(model: RuntimeEvidenceModel, name: str) -> list[tuple[str, float]]:
    return [(m.scope, m.value) for m in model.metrics if m.name == name]


def _fact(model: RuntimeEvidenceModel, text: str) -> str:
    return f"[{model.source}] {text}"


def _identity_tokens(model: RuntimeEvidenceModel) -> set[str]:
    """Exact identifiers exported by the artifact - the only join keys allowed."""
    tokens = {v for v in model.identifiers.values() if v}
    tokens.update(ex.id for ex in model.executions)
    tokens.update(m.scope for m in model.metrics if m.scope)
    return {t.lower() for t in tokens if len(t) >= 4}


def _identity_overlap(result: CheckResult, model: RuntimeEvidenceModel) -> bool:
    """True when a runtime identifier appears verbatim in the finding's anchor."""
    haystack = " ".join(
        p
        for p in (
            result.file.as_posix() if result.file else "",
            result.message,
            result.evidence or "",
            result.symbol or "",
        )
        if p
    ).lower()
    return any(token in haystack for token in _identity_tokens(model))


# --- promotion rules -----------------------------------------------------------


@dataclass(frozen=True)
class _PromotionRule:
    id: str
    check_ids: frozenset[str]
    runtime_sources: frozenset[str]
    signal: Callable[[RuntimeEvidenceModel], list[str]]
    confirmed_with_identity: bool
    explanation: str


def _single_task_signal(model: RuntimeEvidenceModel) -> list[str]:
    facts: list[str] = []
    durations = {
        ex.id: ex.duration_ms
        for ex in model.executions
        if ex.kind == "stage" and ex.duration_ms is not None
    }
    for scope, count in _metric_scoped(model, "task_count"):
        if count == 1:
            dur = durations.get(scope)
            facts.append(
                _fact(
                    model,
                    f"{scope} ran a single task" + (f" for {dur:.0f}ms" if dur else ""),
                )
            )
    return facts


def _skew_signal(model: RuntimeEvidenceModel) -> list[str]:
    return [_fact(model, e) for e in model.events if "skew:" in e]


def _spill_signal(model: RuntimeEvidenceModel) -> list[str]:
    facts = []
    for scope, spilled in _metric_scoped(model, "spill_memory_bytes"):
        if spilled > 0:
            facts.append(_fact(model, f"{scope} spilled {spilled:.0f} bytes to memory"))
    for scope, spilled in _metric_scoped(model, "spill_disk_bytes"):
        if spilled > 0:
            facts.append(_fact(model, f"{scope} spilled {spilled:.0f} bytes to disk"))
    return facts


def _skew_or_spill_signal(model: RuntimeEvidenceModel) -> list[str]:
    return _skew_signal(model) + _spill_signal(model)


def _backlog_signal(model: RuntimeEvidenceModel) -> list[str]:
    facts = []
    for t in model.throughput:
        if t.input_rps is not None and t.output_rps is not None and t.output_rps < t.input_rps:
            facts.append(
                _fact(
                    model,
                    f"{t.name}: processing {t.output_rps}/s below input {t.input_rps}/s"
                    " - backlog grows",
                )
            )
    return facts


def _retry_signal(model: RuntimeEvidenceModel) -> list[str]:
    if model.retries > 0:
        return [_fact(model, f"{model.retries} retry attempt(s) observed")]
    return []


def _errors_signal(model: RuntimeEvidenceModel) -> list[str]:
    return [_fact(model, f"{e.code}: {e.message} (x{e.count})") for e in model.errors]


_PROMOTION_RULES: tuple[_PromotionRule, ...] = (
    _PromotionRule(
        id="single_task_bottleneck",
        check_ids=frozenset({"SPARK003", "ICE024", "PARQ010"}),
        runtime_sources=frozenset({"spark_eventlog"}),
        signal=_single_task_signal,
        confirmed_with_identity=True,
        explanation=(
            "static single-partition write + a stage that ran one task - "
            "the serialized bottleneck is confirmed at runtime"
        ),
    ),
    _PromotionRule(
        id="skew_confirmed",
        check_ids=frozenset({"SPARK009", "SPARK010", "PARQ042"}),
        runtime_sources=frozenset({"spark_eventlog", "glue_logs"}),
        signal=_skew_or_spill_signal,
        confirmed_with_identity=True,
        explanation="runtime skew/spill facts are consistent with the static shuffle-risk finding",
    ),
    _PromotionRule(
        id="backlog_observed",
        check_ids=frozenset({"STREAM001", "STREAM020", "STREAM070", "PLAT007"}),
        runtime_sources=frozenset({"spark_ss_progress"}),
        signal=_backlog_signal,
        confirmed_with_identity=True,
        explanation="micro-batch progress shows processing rate below input rate",
    ),
    _PromotionRule(
        id="retries_observed",
        check_ids=frozenset({"PLAT001"}),
        runtime_sources=frozenset(
            {"spark_eventlog", "glue_logs", "spark_ss_progress", "sfn_history"}
        ),
        signal=_retry_signal,
        confirmed_with_identity=False,
        explanation="runtime retries make the non-idempotent-write risk concrete",
    ),
)


# Check-id prefix -> runtime sources in the same domain. Used only for the
# weakest tier (POSSIBLE): shared-domain runtime errors are correlation, not
# confirmation.
_DOMAIN_SOURCES: dict[str, frozenset[str]] = {
    "SPARK": frozenset({"spark_eventlog", "glue_logs"}),
    "GLUE": frozenset({"spark_eventlog", "glue_logs"}),
    "ICE": frozenset({"spark_eventlog", "spark_ss_progress", "glue_logs"}),
    "PARQ": frozenset({"spark_eventlog", "spark_ss_progress", "glue_logs"}),
    "STREAM": frozenset({"spark_ss_progress"}),
    "SFN": frozenset({"sfn_history"}),
    "NEP": frozenset({"neptune_explain"}),
    "PLAT": frozenset(
        {"spark_eventlog", "glue_logs", "spark_ss_progress", "athena_stats", "lambda_report"}
    ),
}


def _domain_of(check_id: str) -> str:
    for prefix in sorted(_DOMAIN_SOURCES, key=len, reverse=True):
        if check_id.startswith(prefix):
            return prefix
    return ""


def _promotion_id(base_fingerprint: str, evidence: tuple[str, ...]) -> str:
    material = "promo|" + base_fingerprint + "|" + "|".join(sorted(evidence))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]


def promote_findings(
    results: list[CheckResult], models: list[RuntimeEvidenceModel]
) -> list[FindingPromotion]:
    """Lift findings whose risk is confirmed by runtime facts.

    Emits at most one promotion per (finding, rule): the strongest level the
    evidence supports. ``CONFIRMED`` additionally requires an exact identity
    join between the runtime artifact and the finding's anchor - domain-only
    correlation caps at ``STRONGLY_SUPPORTED`` for targeted rules and
    ``POSSIBLE`` for shared-domain errors.
    """
    promotions: dict[tuple[str, str], FindingPromotion] = {}
    for result in results:
        if result.fingerprint is None:
            continue
        for rule in _PROMOTION_RULES:
            if result.check_id not in rule.check_ids:
                continue
            for model in models:
                if model.source not in rule.runtime_sources:
                    continue
                evidence = tuple(sorted(set(rule.signal(model))))
                if not evidence:
                    continue
                identity = _identity_overlap(result, model)
                level = (
                    PromotionLevel.CONFIRMED
                    if identity and rule.confirmed_with_identity
                    else PromotionLevel.STRONGLY_SUPPORTED
                )
                key = (result.fingerprint, rule.id)
                candidate = FindingPromotion(
                    base_fingerprint=result.fingerprint,
                    promotion_id=_promotion_id(result.fingerprint, evidence),
                    check_id=result.check_id,
                    title=result.title,
                    confirming_evidence=evidence,
                    resulting_confidence=(
                        Confidence.HIGH if level is PromotionLevel.CONFIRMED else Confidence.MEDIUM
                    ),
                    resulting_severity=(
                        Severity.ERROR
                        if level is PromotionLevel.CONFIRMED and result.severity is Severity.WARNING
                        else result.severity
                    ),
                    level=level,
                    explanation=rule.explanation,
                )
                existing = promotions.get(key)
                if existing is None or _LEVEL_RANK[candidate.level] > _LEVEL_RANK[existing.level]:
                    promotions[key] = candidate

        # Weakest tier: shared-domain runtime errors corroborate, never confirm.
        domain = _domain_of(result.check_id)
        for model in models:
            if domain and model.source in _DOMAIN_SOURCES[domain] and model.errors:
                evidence = tuple(sorted(set(_errors_signal(model))))
                key = (result.fingerprint, "domain_errors")
                if key not in promotions:
                    promotions[key] = FindingPromotion(
                        base_fingerprint=result.fingerprint,
                        promotion_id=_promotion_id(result.fingerprint, evidence),
                        check_id=result.check_id,
                        title=result.title,
                        confirming_evidence=evidence,
                        resulting_confidence=result.confidence or Confidence.LOW,
                        resulting_severity=result.severity,
                        level=PromotionLevel.POSSIBLE,
                        explanation=(
                            f"runtime errors in the {model.source} domain are "
                            "consistent with, but do not prove, this finding"
                        ),
                    )
    return sorted(promotions.values(), key=lambda p: (p.base_fingerprint, p.promotion_id))


_LEVEL_RANK = {
    PromotionLevel.POSSIBLE: 0,
    PromotionLevel.STRONGLY_SUPPORTED: 1,
    PromotionLevel.CONFIRMED: 2,
}


# --- causal chains -------------------------------------------------------------


@dataclass(frozen=True)
class _NodeEvidence:
    facts: tuple[str, ...]
    findings: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    runtime: bool = False


_Probe = Callable[["ChainInput"], _NodeEvidence]


@dataclass(frozen=True)
class _Node:
    id: str
    label: str
    probe: _Probe


@dataclass(frozen=True)
class _Chain:
    id: str
    title: str
    nodes: tuple[_Node, ...]
    edge_labels: tuple[str, ...]
    min_evidenced: int = 2


@dataclass(frozen=True)
class ChainInput:
    """Everything a chain probe may look at: findings + runtime models."""

    results: tuple[CheckResult, ...]
    models: tuple[RuntimeEvidenceModel, ...]

    def findings_where(
        self, check_ids: frozenset[str] | None = None, contains: str | None = None
    ) -> list[CheckResult]:
        out = []
        for r in self.results:
            if check_ids is not None and r.check_id not in check_ids:
                continue
            text = f"{r.title} {r.message} {r.evidence or ''}".lower()
            if contains is not None and contains not in text:
                continue
            out.append(r)
        return out

    def models_where(self, *sources: str) -> list[RuntimeEvidenceModel]:
        return [m for m in self.models if m.source in sources]


def _finding_refs(results: list[CheckResult]) -> tuple[str, ...]:
    return tuple(f"{r.check_id}:{r.fingerprint}" for r in results if r.fingerprint)


def _entities_of(models: list[RuntimeEvidenceModel]) -> tuple[str, ...]:
    out: set[str] = set()
    for m in models:
        out.update(m.identifiers.values())
    return tuple(sorted(v for v in out if v))


# --- chain A: microbatch -> commit amplification -> small files -> read overhead


def _p_microbatch(ci: ChainInput) -> _NodeEvidence:
    facts: list[str] = []
    entities: list[str] = []
    runtime = False
    for m in ci.models_where("spark_ss_progress"):
        batches = [e.id for e in m.executions if e.kind == "batch"]
        if batches:
            runtime = True
        stream = m.identifiers.get("stream") or m.identifiers.get("query_name") or "stream"
        facts.append(_fact(m, f"{len(batches)} micro-batch execution(s) for '{stream}'"))
        entities.append(stream)
    findings = ci.findings_where(check_ids=frozenset({"STREAM001", "STREAM070"}))
    for r in findings:
        loc = r.file.as_posix() if r.file else "?"
        facts.append(f"static: {r.check_id} {r.title} at {loc}")
    return _NodeEvidence(
        tuple(facts), _finding_refs(findings), tuple(sorted(entities)), runtime=runtime
    )


def _p_commit_amplification(ci: ChainInput) -> _NodeEvidence:
    facts: list[str] = []
    entities: list[str] = []
    runtime = False
    for m in ci.models_where("spark_ss_progress"):
        batches = [e for e in m.executions if e.kind == "batch"]
        if len(batches) >= 3:
            runtime = True
            stream = m.identifiers.get("stream", "stream")
            facts.append(
                _fact(
                    m,
                    f"{len(batches)} commits for '{stream}' in the captured window"
                    " - each micro-batch is a table commit",
                )
            )
            entities.append(stream)
        for t in m.throughput:
            if t.duration_ms and t.input_rows is not None and t.input_rows < 1000:
                runtime = True
                facts.append(
                    _fact(
                        m,
                        f"{t.name}: only {t.input_rows:.0f} rows per {t.duration_ms:.0f}ms"
                        " commit - commit overhead dominates",
                    )
                )
    return _NodeEvidence(tuple(facts), entities=tuple(sorted(entities)), runtime=runtime)


def _p_small_files(ci: ChainInput) -> _NodeEvidence:
    facts: list[str] = []
    findings = ci.findings_where(check_ids=frozenset({"PARQ040", "PARQ041", "PARQ010", "ICE024"}))
    for r in findings:
        loc = r.file.as_posix() if r.file else "?"
        facts.append(f"static: {r.check_id} {r.title} at {loc}")
    runtime = False
    for m in ci.models_where("spark_ss_progress"):
        for t in m.throughput:
            if t.input_rows is not None and t.input_rows < 1000:
                runtime = True
                facts.append(
                    _fact(
                        m,
                        f"{t.name}: {t.input_rows:.0f} rows/batch produces small files",
                    )
                )
    return _NodeEvidence(tuple(facts), _finding_refs(findings), runtime=runtime)


def _p_consumer_overhead(ci: ChainInput) -> _NodeEvidence:
    facts: list[str] = []
    entities: list[str] = []
    for m in ci.models_where("athena_stats"):
        timings = {t.phase: t.duration_ms for t in m.timings}
        queue = timings.get("queue", 0)
        planning = timings.get("planning", 0)
        execution = timings.get("execution", 0)
        if execution and queue + planning > execution:
            qid = m.identifiers.get("query_id", "query")
            facts.append(
                _fact(
                    m,
                    f"athena {qid}: queue+planning {queue + planning:.0f}ms exceeds"
                    f" execution {execution:.0f}ms - metadata/scan overhead dominates",
                )
            )
            entities.append(qid)
        scanned = _metric(m, "data_scanned_bytes")
        if scanned and execution and scanned[0] < 10_000_000:
            facts.append(
                _fact(
                    m,
                    f"athena scanned only {scanned[0]:.0f} bytes in {execution:.0f}ms"
                    " - file-open overhead pattern",
                )
            )
    return _NodeEvidence(tuple(facts), entities=tuple(sorted(entities)), runtime=bool(facts))


_STREAMING_COMMIT_CHAIN = _Chain(
    id="RC_STREAM_COMMITS",
    title="micro-batch frequency amplifies commits -> small files -> consumer overhead",
    nodes=(
        _Node("microbatch_writer", "micro-batch writer", _p_microbatch),
        _Node("commit_amplification", "commit amplification", _p_commit_amplification),
        _Node("small_files", "small Parquet/data files", _p_small_files),
        _Node("consumer_overhead", "consumer planning/scan overhead", _p_consumer_overhead),
    ),
    edge_labels=("commits per batch", "write amplification", "scan overhead"),
    min_evidenced=2,
)


# --- chain B: join key -> skew -> spill -> long stage ---------------------------


def _p_join_key(ci: ChainInput) -> _NodeEvidence:
    facts: list[str] = []
    findings = (
        ci.findings_where(
            check_ids=frozenset({"SPARK009", "SPARK010"}),
        )
        + ci.findings_where(contains="join")
        + ci.findings_where(contains="shuffle")
    )
    seen: set[str] = set()
    unique = []
    for r in findings:
        if r.fingerprint and r.fingerprint not in seen:
            seen.add(r.fingerprint)
            unique.append(r)
    for r in unique:
        loc = r.file.as_posix() if r.file else "?"
        facts.append(f"static: {r.check_id} {r.title} at {loc}")
    for m in ci.models_where("spark_eventlog", "glue_logs"):
        shuffled = sum(_metric(m, "shuffle_read_bytes")) + sum(_metric(m, "shuffle_write_bytes"))
        if shuffled > 0:
            facts.append(_fact(m, f"shuffle traffic observed: {shuffled:.0f} bytes"))
    return _NodeEvidence(
        tuple(facts),
        _finding_refs(unique),
        runtime=any(f.startswith("[") for f in facts),
    )


def _p_skew(ci: ChainInput) -> _NodeEvidence:
    facts: list[str] = []
    for m in ci.models_where("spark_eventlog", "glue_logs"):
        facts.extend(_skew_signal(m))
    return _NodeEvidence(tuple(facts), runtime=bool(facts))


def _p_spill(ci: ChainInput) -> _NodeEvidence:
    facts: list[str] = []
    for m in ci.models_where("spark_eventlog", "glue_logs"):
        facts.extend(_spill_signal(m))
    return _NodeEvidence(tuple(facts), runtime=bool(facts))


def _p_long_stage(ci: ChainInput) -> _NodeEvidence:
    facts: list[str] = []
    for m in ci.models_where("spark_eventlog"):
        stages = [
            (ex.id, ex.duration_ms)
            for ex in m.executions
            if ex.kind == "stage" and ex.duration_ms is not None
        ]
        if len(stages) >= 2:
            for sid, dur in stages:
                others = sorted(v for k, v in stages if k != sid)
                median = others[len(others) // 2]
                if median > 0 and dur >= 3 * median:
                    facts.append(_fact(m, f"{sid}: {dur:.0f}ms is >=3x peer median {median:.0f}ms"))
        elif stages:
            sid, dur = stages[0]
            if dur >= 30_000:
                facts.append(_fact(m, f"{sid}: single stage ran {dur:.0f}ms"))
    return _NodeEvidence(tuple(facts), runtime=bool(facts))


_SPARK_SKEW_CHAIN = _Chain(
    id="RC_SPARK_SKEW",
    title="join/shuffle key -> data skew -> spill -> long stage",
    nodes=(
        _Node("join_key", "join/shuffle key", _p_join_key),
        _Node("skew", "partition skew", _p_skew),
        _Node("spill", "memory/disk spill", _p_spill),
        _Node("long_stage", "long-running stage", _p_long_stage),
    ),
    edge_labels=("distributes by key", "overflows executor memory", "dominates job time"),
    min_evidenced=2,
)


_CHAINS: tuple[_Chain, ...] = (_STREAMING_COMMIT_CHAIN, _SPARK_SKEW_CHAIN)


def cluster_findings(
    results: list[CheckResult], models: list[RuntimeEvidenceModel]
) -> list[FindingCluster]:
    """Evaluate causal-chain templates against findings + runtime models.

    Confidence: CONFIRMED when every node is evidenced and at least one is
    backed by runtime facts; STRONGLY_SUPPORTED when all but one node is
    evidenced (or all nodes evidenced without runtime); POSSIBLE at the
    template's ``min_evidenced`` floor.
    """
    ci = ChainInput(results=tuple(results), models=tuple(models))
    clusters: list[FindingCluster] = []
    for chain in _CHAINS:
        evaluated = [(node, node.probe(ci)) for node in chain.nodes]
        evidenced = [(n, ev) for n, ev in evaluated if ev.facts]
        if len(evidenced) < chain.min_evidenced:
            continue
        total = len(chain.nodes)
        runtime_backed = any(ev.runtime for _, ev in evaluated)
        if len(evidenced) == total and runtime_backed:
            level = PromotionLevel.CONFIRMED
        elif len(evidenced) >= total - 1:
            level = PromotionLevel.STRONGLY_SUPPORTED
        else:
            level = PromotionLevel.POSSIBLE

        edges = tuple(
            CausalEdge(
                source=chain.nodes[i].id,
                target=chain.nodes[i + 1].id,
                label=chain.edge_labels[i],
            )
            for i in range(len(chain.nodes) - 1)
        )
        evidence: list[str] = []
        related: set[str] = set()
        entities: set[str] = set()
        for node, ev in evaluated:
            for fact in ev.facts:
                evidence.append(f"{node.id}: {fact}")
            related.update(ev.findings)
            entities.update(ev.entities)
        clusters.append(
            FindingCluster(
                id=f"{chain.id}-{_chain_digest(evidence)}",
                title=chain.title,
                root_causes=tuple(evidence[:2]) if evidence else (),
                symptoms=tuple(f"{n.id}: {f}" for n, ev in evidenced[-1:] for f in ev.facts[:2]),
                related_findings=tuple(sorted(related)),
                affected_entities=tuple(sorted(entities)),
                evidence=tuple(evidence),
                confidence=level,
                causal_edges=edges,
            )
        )
    return sorted(clusters, key=lambda c: c.id)


def _chain_digest(evidence: list[str]) -> str:
    return hashlib.sha256("|".join(sorted(evidence)).encode("utf-8")).hexdigest()[:10]
