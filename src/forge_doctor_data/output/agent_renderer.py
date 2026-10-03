"""Agent bundle: minimal-token output for downstream agentic consumers.

Emits ids, locations, severities and fingerprints only - agents fetch full
explanations on demand via ``forge-doctor-data explain <ID> --json``.
"""

from __future__ import annotations

import json
from typing import Any

from forge_doctor_data.core.models import ScanReport, Severity


def render_agent(report: ScanReport) -> str:
    payload: dict[str, Any] = {
        "tool": "forge-doctor-data",
        "version": report.version,
        "schema_version": "3.0",
        "project": {"name": report.project.name},
        "summary": {
            "passed": report.summary.passed,
            "info": report.summary.info,
            "warnings": report.summary.warnings,
            "errors": report.summary.errors,
        },
        "findings": [
            {
                "id": r.check_id,
                "sev": r.severity.value,
                "loc": _location(r.file, r.line),
                "fp": r.fingerprint,
            }
            for r in report.results
            if r.severity is not Severity.PASS
        ],
    }
    if report.baseline is not None:
        payload["baseline"] = {
            "new": report.baseline.new,
            "fixed": report.baseline.fixed,
            "existing": report.baseline.existing,
        }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def _location(file: object, line: int | None) -> str | None:
    if file is None:
        return None
    from pathlib import Path

    path = file.as_posix() if isinstance(file, Path) else str(file)
    return f"{path}:{line}" if line else path
