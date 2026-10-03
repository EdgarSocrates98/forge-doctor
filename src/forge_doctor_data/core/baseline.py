"""Baseline support: save a scan, compare later scans against it.

The baseline file is plain JSON (``.forge-doctor-data-baseline.json`` by
convention) so it can live next to the project and travel through CI.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forge_doctor_data.core.models import BaselineDiff, CheckResult, ScanReport

# Format 2: findings carry semantic fingerprints (fingerprint_version=3).
BASELINE_FORMAT = 2
FINGERPRINT_VERSION = 3


class BaselineError(Exception):
    """Raised when a baseline file cannot be trusted.

    A baseline that fails validation must never silently degrade to an
    empty set - that would flip every finding to NEW and fail CI for the
    wrong reason.
    """


def _read_payload(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise BaselineError(f"Baseline file not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BaselineError(f"Baseline is not valid JSON: {path} ({exc})") from exc
    except OSError as exc:
        raise BaselineError(f"Baseline unreadable: {path} ({exc})") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise BaselineError(
            f"Not a forge-doctor-data baseline/report: {path} (missing 'results' list)"
        )
    return payload


def _validate_baseline(payload: dict[str, Any], path: Path) -> None:
    """Reject baselines whose fingerprint version can't match this engine."""
    fp_version = payload.get("fingerprint_version")
    if fp_version != FINGERPRINT_VERSION:
        raise BaselineError(
            f"Baseline {path} uses fingerprint version "
            f"{fp_version or 'none'}; this engine writes version "
            f"{FINGERPRINT_VERSION}. Re-create it: "
            "forge-doctor-data scan . --save-baseline " + str(path)
        )


def result_key(result: CheckResult) -> str:
    """Stable identity of a finding across scans (its fingerprint)."""
    assert result.fingerprint is not None  # auto-derived in __post_init__
    return result.fingerprint


def save_baseline(report: ScanReport, path: Path) -> None:
    payload = {
        "format": BASELINE_FORMAT,
        "fingerprint_version": 3,
        "version": report.version,
        "project": report.project.name,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "results": [
            {
                "check_id": r.check_id,
                "fingerprint": r.fingerprint,
                "file": r.file.as_posix() if r.file is not None else None,
                "line": r.line,
                "message": r.message,
            }
            for r in report.results
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_baseline(path: Path) -> set[str]:
    """Load baseline fingerprints; raises :class:`BaselineError` on problems.

    The file must be a format-2 baseline whose ``fingerprint_version``
    matches this engine - older baselines fail loudly with a re-baseline
    hint rather than silently marking every finding NEW.
    """
    payload = _read_payload(path)
    _validate_baseline(payload, path)
    keys: set[str] = set()
    for item in payload["results"]:
        if not isinstance(item, dict):
            continue
        fingerprint = item.get("fingerprint")
        if isinstance(fingerprint, str):
            keys.add(fingerprint)
    return keys


def load_baseline_items(path: Path) -> dict[str, dict[str, Any]]:
    """fingerprint -> stored item map, for `forge-doctor-data diff` rendering.

    Accepts baseline files AND saved JSON reports (schema_version 3.x);
    raises :class:`BaselineError` when the file can't be interpreted.
    """
    payload = _read_payload(path)
    items: dict[str, dict[str, Any]] = {}
    for item in payload["results"]:
        if not isinstance(item, dict):
            continue
        fingerprint = item.get("fingerprint")
        if isinstance(fingerprint, str):
            items[fingerprint] = item
    return items


def apply_baseline(report: ScanReport, path: Path) -> ScanReport:
    """Tag each result ``is_new`` and attach a :class:`BaselineDiff`."""
    baseline_keys = load_baseline(path)
    current_keys = {result_key(r) for r in report.results}

    tagged = [
        dataclasses.replace(r, is_new=result_key(r) not in baseline_keys) for r in report.results
    ]
    diff = BaselineDiff(
        new=sum(1 for r in tagged if r.is_new),
        fixed=len(baseline_keys - current_keys),
        existing=sum(1 for r in tagged if r.is_new is False),
    )
    return dataclasses.replace(report, results=tagged, baseline=diff)
