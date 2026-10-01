"""SARIF 2.1.0 output - consumed by GitHub Code Scanning and other tools.

PASS results are engine-internal signals and are not emitted; INFO maps to
``note``, WARNING to ``warning``, ERROR to ``error``.
"""

from __future__ import annotations

import json
from typing import Any

from forge_doctor import __version__
from forge_doctor.core.models import CheckResult, ScanReport, Severity

_LEVELS = {
    Severity.INFO: "note",
    Severity.WARNING: "warning",
    Severity.ERROR: "error",
}
_RANKS = {
    Severity.INFO: 35.0,
    Severity.WARNING: 65.0,
    Severity.ERROR: 95.0,
}


def render_sarif(report: ScanReport) -> str:
    results = [r for r in report.results if r.severity is not Severity.PASS]
    rule_ids = sorted({r.check_id for r in results})
    rules = [
        {
            "id": check_id,
            "name": _title_for(results, check_id),
            "properties": {
                "tags": sorted({tag for r in results if r.check_id == check_id for tag in r.tags})
                or [next(r.category for r in results if r.check_id == check_id)]
            },
        }
        for check_id in rule_ids
    ]
    payload: dict[str, Any] = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "forge-doctor",
                        "version": __version__,
                        "informationUri": "https://github.com/EdgarSocrates98/forge-doctor",
                        "rules": rules,
                    }
                },
                "results": [_result_to_sarif(r) for r in results],
            }
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _title_for(results: list[CheckResult], check_id: str) -> str:
    for result in results:
        if result.check_id == check_id:
            return result.title
    return check_id


def _result_to_sarif(result: CheckResult) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "ruleId": result.check_id,
        "level": _LEVELS[result.severity],
        "message": {"text": result.message},
        "rank": _RANKS[result.severity],
        "partialFingerprints": {
            "forge-doctor/fingerprint": result.fingerprint or "",
        },
    }
    if result.recommendation:
        entry["fixes"] = [{"description": {"text": result.recommendation}}]
    if result.file is not None:
        region: dict[str, Any] = {"startLine": result.line or 1}
        if result.column is not None:
            region["startColumn"] = result.column
        if result.end_line is not None:
            region["endLine"] = result.end_line
        if result.end_column is not None:
            region["endColumn"] = result.end_column
        if result.evidence:
            region["snippet"] = {"text": result.evidence}
        entry["locations"] = [
            {
                "physicalLocation": {
                    "artifactLocation": {
                        "uri": result.file.as_posix(),
                        "uriBaseId": "SRCROOT",
                    },
                    "region": region,
                }
            }
        ]
    if result.docs_uri:
        entry.setdefault("rule", {})["helpUri"] = result.docs_uri
    return entry
