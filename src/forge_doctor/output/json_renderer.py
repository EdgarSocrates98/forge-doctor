"""Stable JSON contract - consumed by CI, agents, and future tooling.

Schema v3 separates the tool version from the payload schema and keeps the
absolute project root out of the default output: cross-machine deterministic,
nothing about the local filesystem leaks unless ``show_root`` is passed.
"""

from __future__ import annotations

import json
from typing import Any

from forge_doctor.core.models import CheckResult, ScanReport

JSON_SCHEMA_VERSION = "3.0"


def result_to_dict(result: CheckResult) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "check_id": result.check_id,
        "title": result.title,
        "severity": result.severity.value,
        "category": result.category,
        "message": result.message,
        "fingerprint": result.fingerprint,
        # POSIX separators so the contract is identical on every platform.
        "file": result.file.as_posix() if result.file is not None else None,
        "line": result.line,
        "recommendation": result.recommendation,
    }
    if result.column is not None:
        payload["column"] = result.column
    if result.end_line is not None:
        payload["end_line"] = result.end_line
    if result.end_column is not None:
        payload["end_column"] = result.end_column
    if result.confidence is not None:
        payload["confidence"] = result.confidence.value
    if result.evidence is not None:
        payload["evidence"] = result.evidence
    if result.evidence_kind is not None:
        payload["evidence_kind"] = result.evidence_kind.value
    if result.tags:
        payload["tags"] = list(result.tags)
    if result.docs_uri is not None:
        payload["docs_uri"] = result.docs_uri
    if result.symbol is not None:
        payload["symbol"] = result.symbol
    if result.source is not None:
        payload["source"] = result.source
    if result.fixable is not None:
        payload["fixable"] = result.fixable
    if result.is_new is not None:
        payload["is_new"] = result.is_new
    return payload


def render_json(report: ScanReport, show_root: bool = False) -> str:
    project: dict[str, Any] = {"name": report.project.name}
    if show_root:
        project["root"] = report.project.as_posix()
    payload: dict[str, Any] = {
        "version": report.version,
        "tool": {"name": "forge-doctor", "version": report.version},
        "schema_version": JSON_SCHEMA_VERSION,
        "project": project,
        "summary": {
            "passed": report.summary.passed,
            "info": report.summary.info,
            "warnings": report.summary.warnings,
            "errors": report.summary.errors,
        },
        "results": [result_to_dict(result) for result in report.results],
    }
    if report.suppressions:
        suppressed = sum(r.matched for r in report.suppressions)
        payload["summary"]["suppressed"] = suppressed
        payload["suppressions"] = [
            {
                "rule": r.rule,
                "path": r.path,
                "status": r.status,
                "matched": r.matched,
                "owner": r.owner,
                "expires": r.expires,
                "reason": r.reason,
            }
            for r in report.suppressions
        ]
    if report.baseline is not None:
        payload["baseline"] = {
            "new": report.baseline.new,
            "fixed": report.baseline.fixed,
            "existing": report.baseline.existing,
        }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def render_jsonl(report: ScanReport) -> str:
    """One finding per line - NDJSON for streaming/agent consumers."""
    lines = [
        json.dumps(result_to_dict(result), ensure_ascii=False, separators=(",", ":"))
        for result in report.results
    ]
    return "\n".join(lines)
