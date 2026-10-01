"""Spark runtime doctor - event logs, physical plans, and log fingerprints.

Pure stdlib: Spark event logs are JSONL, physical plans are indented text, and
logs are fingerprinted with the same error packs ``diagnose`` uses. Nothing
here needs pyspark or a running cluster.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from forge_doctor.core.diagnose import diagnose_text
from forge_doctor.core.models import CheckResult, Confidence, Severity

RUNTIME_CATEGORY = "runtime"


def _finding(
    check_id: str,
    title: str,
    severity: Severity,
    message: str,
    recommendation: str | None = None,
    evidence: str | None = None,
    confidence: Confidence = Confidence.MEDIUM,
) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        title=title,
        severity=severity,
        category=RUNTIME_CATEGORY,
        message=message,
        recommendation=recommendation,
        evidence=evidence,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Event log analysis
# ---------------------------------------------------------------------------


def _iter_events(text: str) -> Iterable[dict[str, Any]]:
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and "Event" in event:
            yield event


def analyze_eventlog_text(text: str) -> list[CheckResult]:
    """Detect executor loss, skew, spill, GC pressure, retries, scheduler delay."""
    events = list(_iter_events(text))
    if not events:
        return [
            _finding(
                "RT000",
                "Empty event log",
                Severity.INFO,
                "No Spark events parsed - is this an event log?",
                confidence=Confidence.LOW,
            )
        ]

    removed = [e for e in events if e.get("Event") == "SparkListenerExecutorRemoved"]
    stage_infos = [e for e in events if e.get("Event") == "SparkListenerStageCompleted"]
    task_ends = [
        e
        for e in events
        if e.get("Event") == "SparkListenerTaskEnd" and isinstance(e.get("Task Metrics"), dict)
    ]

    findings: list[CheckResult] = []

    if removed:
        findings.append(
            _finding(
                "RT001",
                "Executors lost",
                Severity.ERROR,
                f"{len(removed)} executor(s) removed during the run.",
                "Check executor memory and preemptible-instance churn.",
                evidence=removed[0].get("Removed Reason", "") or None,
                confidence=Confidence.HIGH,
            )
        )

    # Task skew + retries + scheduler delay + spill + GC pressure.
    skewed = 0
    retries = 0
    total_delay = 0
    delay_tasks = 0
    spilled = 0
    gc_overloaded = 0
    per_stage: dict[int, list[int]] = {}
    for event in task_ends:
        metrics = event["Task Metrics"]
        info = event.get("Task Info", {})
        stage_id = int(info.get("Stage ID", -1)) if isinstance(info, dict) else -1
        duration = (
            int(info.get("Finish Time", 0) - info.get("Launch Time", 0))
            if isinstance(info, dict)
            else 0
        )
        per_stage.setdefault(stage_id, []).append(duration)
        if info.get("Failed") is True or int(info.get("Attempt", 0) or 0) > 0:
            retries += 1
        delay = int(metrics.get("Scheduler Delay", 0) or 0)
        total_delay += delay
        delay_tasks += 1
        shuffle_write = metrics.get("Shuffle Write Metrics", {}) or {}
        spill = int(metrics.get("Memory Bytes Spilled", 0) or 0) + int(
            metrics.get("Disk Bytes Spilled", 0) or 0
        )
        spill += int(shuffle_write.get("Bytes Written", 0) or 0) * 0  # spill-only
        if spill > 0:
            spilled += 1
        run_ms = int(metrics.get("Executor Run Time", 0) or 0)
        gc_ms = int(metrics.get("JVM GC Time", 0) or 0)
        if run_ms > 0 and gc_ms / max(run_ms, 1) > 0.20:
            gc_overloaded += 1

    for _stage_id, durations in per_stage.items():
        if len(durations) < 4:
            continue
        ordered = sorted(durations)
        median = ordered[len(ordered) // 2] or 1
        if max(durations) > median * 4 and max(durations) > 5000:
            skewed += 1

    if skewed:
        findings.append(
            _finding(
                "RT002",
                "Task skew",
                Severity.WARNING,
                f"{skewed} stage(s) show max/median task time > 4x.",
                "Repartition or salt the skewed key; check join keys.",
                confidence=Confidence.MEDIUM,
            )
        )
    if spilled:
        findings.append(
            _finding(
                "RT003",
                "Shuffle spill",
                Severity.WARNING,
                f"{spilled} task(s) spilled memory to disk.",
                "Raise executor memory or increase partition count.",
                confidence=Confidence.MEDIUM,
            )
        )
    if gc_overloaded:
        findings.append(
            _finding(
                "RT004",
                "GC pressure",
                Severity.WARNING,
                f"{gc_overloaded} task(s) spent >20% of runtime in GC.",
                "Increase executor memory; reduce object churn in UDFs.",
                confidence=Confidence.MEDIUM,
            )
        )
    single_task = sum(
        1 for e in stage_infos if int(e.get("Stage Info", {}).get("Number of Tasks", 0) or 0) == 1
    )
    if single_task:
        findings.append(
            _finding(
                "RT005",
                "Single-task stages",
                Severity.WARNING,
                f"{single_task} stage(s) ran with exactly one task.",
                "Likely repartition(1)/coalesce(1) or a tiny input; check SPARK002/SPARK003.",
            )
        )
    if retries:
        findings.append(
            _finding(
                "RT006",
                "Task retries",
                Severity.WARNING,
                f"{retries} task(s) were retried or failed.",
                "Investigate executor stability and speculative execution settings.",
            )
        )
    if delay_tasks and total_delay / delay_tasks > 5000:
        findings.append(
            _finding(
                "RT007",
                "Scheduler delay",
                Severity.INFO,
                f"Average scheduler delay {int(total_delay / delay_tasks)}ms per task.",
                "Cluster is contended; consider more executors or fair scheduling.",
                confidence=Confidence.LOW,
            )
        )

    if not findings:
        findings.append(
            _finding(
                "RT000",
                "Event log clean",
                Severity.PASS,
                f"{len(events)} events; no executor loss, skew, spill, or retry storms.",
            )
        )
    return findings


def analyze_eventlog(path: Path) -> list[CheckResult]:
    """Event log file or directory (event logs land in per-app dirs)."""
    if path.is_dir():
        files = sorted(p for p in path.rglob("*") if p.is_file())
        text = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in files[:20])
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    return analyze_eventlog_text(text)


# ---------------------------------------------------------------------------
# Physical plan analysis
# ---------------------------------------------------------------------------

_PLAN_PATTERNS: tuple[tuple[str, str, str, str], ...] = (
    (
        "CartesianProduct",
        "RT010",
        "Cartesian product join",
        "Every row pair is compared - usually a missing join condition.",
    ),
    (
        "BroadcastNestedLoopJoin",
        "RT011",
        "Broadcast nested-loop join",
        "Nested-loop join on the driver; add equi-join keys if unintended.",
    ),
    (
        "SinglePartition",
        "RT012",
        "Single-partition exchange",
        "Exchange rangepartitioning RoundRobinPartitioning(1)/SinglePartition "
        "collapses parallelism - repartition(1) or a global sort upstream.",
    ),
    (
        "Sort ",
        "RT013",
        "Global sort",
        "A sort without per-partition mode forces a shuffle; confirm the ordering is required.",
    ),
)


def analyze_plan_text(text: str) -> list[CheckResult]:
    """Scan a physical plan for pathological operators."""
    findings: list[CheckResult] = []
    strategies = re.findall(r"(\w+Join)\b", text)
    strategy_mix = sorted(set(strategies))
    seen: set[str] = set()
    for marker, check_id, title, detail in _PLAN_PATTERNS:
        count = text.count(marker)
        if count and check_id not in seen:
            seen.add(check_id)
            findings.append(
                _finding(
                    check_id,
                    title,
                    Severity.WARNING,
                    f"{title}: {count} occurrence(s). {detail}",
                    "Rewrite the query or check join keys.",
                )
            )
    if strategy_mix:
        findings.append(
            _finding(
                "RT014",
                "Join strategy mix",
                Severity.INFO,
                f"Join operators present: {', '.join(strategy_mix)}.",
                confidence=Confidence.HIGH,
            )
        )
    if not findings:
        findings.append(
            _finding(
                "RT000",
                "Plan clean",
                Severity.PASS,
                "No cartesian products, nested-loop joins, or single-partition exchanges.",
            )
        )
    return findings


def analyze_plan(path: Path) -> list[CheckResult]:
    return analyze_plan_text(path.read_text(encoding="utf-8", errors="replace"))


# ---------------------------------------------------------------------------
# Log fingerprinting (reuses error packs + runtime signatures)
# ---------------------------------------------------------------------------

_RUNTIME_PATTERNS = (
    ("Lost executor", "Executor lost in driver/executor log"),
    ("Stage retry", "Stage retried after task failures"),
    ("OutOfMemoryError", "OOM in driver/executor log"),
)


def analyze_log(path: Path) -> list[CheckResult]:
    """Knowledge-pack signatures + Spark runtime patterns over a log file."""
    text = path.read_text(encoding="utf-8", errors="replace")
    findings: list[CheckResult] = []
    for diagnosis in diagnose_text(text):
        findings.append(
            _finding(
                diagnosis.signature.id,
                diagnosis.signature.title,
                Severity.ERROR if diagnosis.signature.severity == "error" else Severity.WARNING,
                f"{diagnosis.count} occurrence(s): {diagnosis.signature.title}",
                "; ".join(diagnosis.signature.causes[:2]) or None,
                evidence=diagnosis.samples[0] if diagnosis.samples else None,
                confidence=Confidence.HIGH,
            )
        )
    for needle, title in _RUNTIME_PATTERNS:
        if needle in text and not any(f.message.startswith(title.split()[0]) for f in findings):
            count = text.count(needle)
            findings.append(
                _finding(
                    "RT020",
                    title,
                    Severity.WARNING,
                    f"{count}x `{needle}` in log.",
                    confidence=Confidence.MEDIUM,
                )
            )
    if not findings:
        findings.append(
            _finding("RT000", "Log clean", Severity.PASS, "No known signatures matched.")
        )
    return findings
