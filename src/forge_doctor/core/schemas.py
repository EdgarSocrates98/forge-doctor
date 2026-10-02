"""Machine-readable contracts for Forge Doctor's public artifacts.

Each entry is a JSON Schema (draft 2020-12) describing one stable
output: what a ``--format json`` consumer, a policy-pack author, or a
snapshot reviewer can rely on. Schemas follow ``SCHEMA_VERSION`` -
additive fields bump MINOR, removed/renamed fields bump MAJOR.
"""

from __future__ import annotations

from typing import Any

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

_FINDING = {
    "type": "object",
    "required": [
        "check_id",
        "title",
        "severity",
        "category",
        "message",
        "fingerprint",
        "file",
        "line",
        "recommendation",
    ],
    "properties": {
        "check_id": {"type": "string"},
        "title": {"type": "string"},
        "severity": {"enum": ["error", "warning", "info", "pass"]},
        "category": {"type": "string"},
        "message": {"type": "string"},
        "fingerprint": {"type": ["string", "null"]},
        "file": {"type": ["string", "null"]},
        "line": {"type": ["integer", "null"]},
        "column": {"type": "integer"},
        "end_line": {"type": "integer"},
        "end_column": {"type": "integer"},
        "recommendation": {"type": ["string", "null"]},
        "confidence": {"type": "string"},
        "evidence": {"type": "string"},
        "evidence_kind": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "docs_uri": {"type": "string"},
        "symbol": {"type": ["string", "null"]},
        "is_new": {"type": ["boolean", "null"]},
    },
    "additionalProperties": True,
}

SCAN_REPORT: dict[str, Any] = {
    "$schema": SCHEMA_DIALECT,
    "$id": "https://forge-doctor.dev/schemas/scan-report.json",
    "title": "Forge Doctor scan report",
    "type": "object",
    "required": ["tool", "schema_version", "results"],
    "properties": {
        "tool": {"const": "forge-doctor"},
        "schema_version": {"type": "string"},
        "version": {"type": "string"},
        "project": {"type": "string"},
        "results": {"type": "array", "items": _FINDING},
        "summary": {"type": "object"},
        "suppressions": {"type": "array"},
    },
    "additionalProperties": True,
}

POLICY_PACK: dict[str, Any] = {
    "$schema": SCHEMA_DIALECT,
    "$id": "https://forge-doctor.dev/schemas/policy-pack.json",
    "title": "Organization policy pack",
    "type": "object",
    "required": ["pack", "rules"],
    "properties": {
        "pack": {"type": "string"},
        "version": {"type": "string"},
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "message"],
                "properties": {
                    "id": {"type": "string", "pattern": "^[A-Z0-9_-]+$"},
                    "severity": {"enum": ["error", "warning", "info"]},
                    "message": {"type": "string"},
                    "recommendation": {"type": "string"},
                    "forbid": {
                        "type": "object",
                        "properties": {
                            "file_glob": {"type": "string"},
                            "pattern": {"type": "string"},
                            "terraform": {
                                "type": "object",
                                "properties": {
                                    "resource_type": {"type": "string"},
                                    "attr": {"type": "string"},
                                    "op": {"enum": ["equals", "matches", "present"]},
                                    "value": {"type": "string"},
                                },
                            },
                        },
                    },
                    "require": {
                        "type": "object",
                        "properties": {
                            "file": {"type": "string"},
                            "file_glob": {"type": "string"},
                            "contains": {"type": "string"},
                            "terraform": {
                                "type": "object",
                                "properties": {
                                    "resource_type": {"type": "string"},
                                    "attr": {"type": "string"},
                                    "op": {"enum": ["equals", "matches", "present"]},
                                    "value": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            },
        },
    },
    "additionalProperties": True,
}

LAB_EXPECTED: dict[str, Any] = {
    "$schema": SCHEMA_DIALECT,
    "$id": "https://forge-doctor.dev/schemas/lab-expected.json",
    "title": "Forge Lab ground truth (expected.json)",
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "findings": {"type": "array", "items": {"type": "string"}},
        "allowed_findings": {"type": "array", "items": {"type": "string"}},
        "absent_findings": {"type": "array", "items": {"type": "string"}},
        "entities": {"type": "array", "items": {"type": "string"}},
        "capabilities": {"type": "array", "items": {"type": "string"}},
        "root_causes": {"type": "array", "items": {"type": "string"}},
        "noise_budget": {"type": ["integer", "null"]},
    },
    "additionalProperties": True,
}

GOLDEN_SNAPSHOT: dict[str, Any] = {
    "$schema": SCHEMA_DIALECT,
    "$id": "https://forge-doctor.dev/schemas/golden-snapshot.json",
    "title": "Golden repository snapshot file",
    "type": "object",
    "properties": {
        "tool": {"const": "forge-doctor"},
        "schema_version": {"type": "string"},
        "items": {"type": "array", "items": {"type": "string"}},
        "findings": {"type": "array"},
        "entities": {"type": "array"},
        "relationships": {"type": "array"},
        "plans": {"type": "array"},
        "chains": {"type": "array"},
    },
    "additionalProperties": True,
}

SCHEMAS: dict[str, dict[str, Any]] = {
    "scan-report": SCAN_REPORT,
    "policy-pack": POLICY_PACK,
    "lab-expected": LAB_EXPECTED,
    "golden-snapshot": GOLDEN_SNAPSHOT,
}
