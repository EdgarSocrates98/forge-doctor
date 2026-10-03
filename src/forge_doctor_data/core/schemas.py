"""Machine-readable contracts for Forge Doctor Data's public artifacts.

Each entry is a JSON Schema (draft 2020-12) describing one stable
output: what a ``--format json`` consumer, a policy-pack author, or a
snapshot reviewer can rely on. Schemas follow ``SCHEMA_VERSION`` -
additive fields bump MINOR, removed/renamed fields bump MAJOR.
"""

from __future__ import annotations

from typing import Any

from forge_doctor_data.core.models import EvidenceKind
from forge_doctor_data.core.platform_graph import EntityKind, RelKind

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
    "$id": "https://forge-doctor-data.dev/schemas/scan-report.json",
    "title": "Forge Doctor Data scan report",
    "type": "object",
    "required": ["version", "tool", "schema_version", "project", "summary", "results"],
    "properties": {
        "version": {"type": "string"},
        "tool": {
            "type": "object",
            "required": ["name", "version"],
            "properties": {
                "name": {"const": "forge-doctor-data"},
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
    "$id": "https://forge-doctor-data.dev/schemas/policy-pack.json",
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
    "$id": "https://forge-doctor-data.dev/schemas/lab-expected.json",
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
    "$id": "https://forge-doctor-data.dev/schemas/golden-snapshot.json",
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

# ---------------------------------------------------------------------------
# Forge ecosystem contracts (spec 211): portable artifacts downstream
# Forge tools consume. All describe what the engine already emits.
# ---------------------------------------------------------------------------

FINDING: dict[str, Any] = {
    "$schema": SCHEMA_DIALECT,
    "$id": "https://forge-doctor-data.dev/schemas/finding.json",
    "title": "Single diagnostic finding (one CheckResult)",
    **_FINDING,
}

EVIDENCE: dict[str, Any] = {
    "$schema": SCHEMA_DIALECT,
    "$id": "https://forge-doctor-data.dev/schemas/evidence.json",
    "title": "Normalized runtime evidence model (runtime inspect --json)",
    "type": "object",
    "required": ["source"],
    "properties": {
        "source": {"type": "string"},
        "identifiers": {"type": "array", "items": {"type": "string"}},
        "executions": {"type": "array", "items": {"type": "object"}},
        "metrics": {"type": "array", "items": {"type": "object"}},
        "errors": {"type": "array", "items": {"type": "object"}},
        "timings": {"type": "array", "items": {"type": "object"}},
        "throughput": {"type": "array", "items": {"type": "object"}},
        "lag": {"type": "array", "items": {"type": "object"}},
        "retries": {"type": "integer"},
        "resource_usage": {"type": "array", "items": {"type": "object"}},
        "state": {"type": ["object", "array", "string", "null"]},
        "events": {"type": "array", "items": {"type": "object"}},
    },
    "additionalProperties": True,
}

_PLATFORM_ENTITY = {
    "type": "object",
    "required": ["id", "kind", "domain"],
    "properties": {
        "id": {"type": "string"},
        "kind": {
            "type": "string",
            "description": "Ontology-bound entity kind (forge-doctor-data ontology).",
            "enum": [k.value for k in EntityKind],
        },
        "domain": {"type": "string"},
        "identifier": {"type": "string"},
        "name": {"type": "string"},
        "file": {"type": "string"},
        "line": {"type": "integer"},
        "attrs": {"type": "object"},
    },
}

_PLATFORM_EDGE = {
    "type": "object",
    "required": ["src", "dst", "kind"],
    "properties": {
        "src": {"type": "string"},
        "dst": {"type": "string"},
        "kind": {
            "type": "string",
            "description": "Ontology-bound relationship kind (forge-doctor-data ontology).",
            "enum": [k.value for k in RelKind],
        },
        "evidence_kind": {
            "type": "string",
            "description": "Ontology-bound evidence plane.",
            "enum": [k.value for k in EvidenceKind],
        },
        "attrs": {"type": "object"},
    },
}

PLATFORM_GRAPH: dict[str, Any] = {
    "$schema": SCHEMA_DIALECT,
    "$id": "https://forge-doctor-data.dev/schemas/platform-graph.json",
    "title": "DataPlatformGraph serialization (entities + relationships)",
    "type": "object",
    "required": ["entities", "relationships"],
    "properties": {
        "entities": {"type": "array", "items": _PLATFORM_ENTITY},
        "relationships": {"type": "array", "items": _PLATFORM_EDGE},
    },
    "additionalProperties": True,
}

CAPABILITY_REPORT: dict[str, Any] = {
    "$schema": SCHEMA_DIALECT,
    "$id": "https://forge-doctor-data.dev/schemas/capability-report.json",
    "title": "Headline capability statuses per platform (capabilities list --json)",
    "type": "object",
    "additionalProperties": {
        "type": "object",
        "additionalProperties": {"enum": ["supported", "unsupported", "conditional", "unknown"]},
    },
}

REMEDIATION_PLAN: dict[str, Any] = {
    "$schema": SCHEMA_DIALECT,
    "$id": "https://forge-doctor-data.dev/schemas/remediation-plan.json",
    "title": "Deterministic remediation plan (remediate --json entry)",
    "type": "object",
    "required": ["id", "problem", "check_id", "actions"],
    "properties": {
        "id": {"type": "string"},
        "problem": {"type": "string"},
        "check_id": {"type": "string"},
        "targets": {"type": "array", "items": {"type": "string"}},
        "prerequisites": {"type": "array", "items": {"type": "string"}},
        "dependencies": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "validation_steps": {"type": "array", "items": {"type": "string"}},
        "rollback_notes": {"type": "array", "items": {"type": "string"}},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "description"],
                "properties": {
                    "id": {"type": "string"},
                    "description": {"type": "string"},
                    "target_entity": {"type": ["string", "null"]},
                    "rationale": {"type": "string"},
                    "expected_effect": {"type": "string"},
                    "validation": {"type": "string"},
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
    "additionalProperties": True,
}

HANDOFF_BUNDLE: dict[str, Any] = {
    "$schema": SCHEMA_DIALECT,
    "$id": "https://forge-doctor-data.dev/schemas/handoff-bundle.json",
    "title": "Forge handoff bundle (export --format handoff)",
    "description": (
        "Portable package of a scan's durable outputs for downstream Forge "
        "tools. No timestamps: identical project state produces identical "
        "bundles."
    ),
    "type": "object",
    "required": [
        "contract",
        "contract_version",
        "schema_version",
        "tool",
        "project",
        "summary",
        "results",
        "graph",
        "capabilities",
        "plans",
    ],
    "properties": {
        "contract": {"const": "handoff-bundle"},
        "contract_version": {"type": "integer"},
        "schema_version": {"type": "string"},
        "tool": {
            "type": "object",
            "required": ["name", "version"],
            "properties": {
                "name": {"const": "forge-doctor-data"},
                "version": {"type": "string"},
            },
        },
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
            },
        },
        "results": {"type": "array", "items": _FINDING},
        "graph": {
            "type": "object",
            "required": ["entities", "relationships"],
            "properties": {
                "entities": {"type": "array", "items": _PLATFORM_ENTITY},
                "relationships": {"type": "array", "items": _PLATFORM_EDGE},
            },
        },
        "capabilities": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "additionalProperties": {
                    "enum": ["supported", "unsupported", "conditional", "unknown"]
                },
            },
        },
        "plans": {"type": "array", "items": REMEDIATION_PLAN},
    },
    "additionalProperties": True,
}

SCHEMAS: dict[str, dict[str, Any]] = {
    "scan-report": SCAN_REPORT,
    "policy-pack": POLICY_PACK,
    "lab-expected": LAB_EXPECTED,
    "golden-snapshot": GOLDEN_SNAPSHOT,
    "finding": FINDING,
    "evidence": EVIDENCE,
    "platform-graph": PLATFORM_GRAPH,
    "capability-report": CAPABILITY_REPORT,
    "remediation-plan": REMEDIATION_PLAN,
    "handoff-bundle": HANDOFF_BUNDLE,
}
