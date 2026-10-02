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
    "required": ["version", "tool", "schema_version", "project", "summary", "results"],
    "properties": {
        "version": {"type": "string"},
        "tool": {
            "type": "object",
            "required": ["name", "version"],
            "properties": {
                "name": {"const": "forge-doctor"},
                "version": {"type": "string"},
            },
        },
        "schema_version": {"type": "string"},
        "project": {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string"},
                "root": {"type": "string"},
            },
        },
        "summary": {
            "type": "object",
            "required": ["passed", "info", "warnings", "errors"],
            "properties": {
                "passed": {"type": "integer"},
                "info": {"type": "integer"},
                "warnings": {"type": "integer"},
                "errors": {"type": "integer"},
                "suppressed": {"type": "integer"},
            },
        },
        "results": {"type": "array", "items": _FINDING},
        "suppressions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["rule", "status", "matched"],
                "properties": {
                    "rule": {"type": "string"},
                    "path": {"type": ["string", "null"]},
                    "status": {"type": "string"},
                    "matched": {"type": "integer"},
                    "owner": {"type": ["string", "null"]},
                    "expires": {"type": ["string", "null"]},
                    "reason": {"type": ["string", "null"]},
                },
            },
        },
        "baseline": {
            "type": "object",
            "required": ["new", "fixed", "existing"],
            "properties": {
                "new": {"type": "integer"},
                "fixed": {"type": "integer"},
                "existing": {"type": "integer"},
            },
        },
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
    "description": (
        "Each snapshot file under <repo>/expected/ is either a sorted row "
        "array (findings/migrations/remediations/root_causes) or the graph "
        "object (entities + relationships)."
    ),
    "oneOf": [
        {
            "type": "object",
            "required": ["entities", "relationships"],
            "properties": {
                "entities": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["id", "kind", "domain"],
                        "properties": {
                            "id": {"type": "string"},
                            "kind": {"type": "string"},
                            "domain": {"type": "string"},
                            "file": {"type": ["string", "null"]},
                            "line": {"type": ["integer", "null"]},
                        },
                    },
                },
                "relationships": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["kind", "src", "dst"],
                        "properties": {
                            "kind": {"type": "string"},
                            "src": {"type": "string"},
                            "dst": {"type": "string"},
                        },
                    },
                },
            },
        },
        {"type": "array", "items": {"type": "object"}},
    ],
}

SCHEMAS: dict[str, dict[str, Any]] = {
    "scan-report": SCAN_REPORT,
    "policy-pack": POLICY_PACK,
    "lab-expected": LAB_EXPECTED,
    "golden-snapshot": GOLDEN_SNAPSHOT,
}
