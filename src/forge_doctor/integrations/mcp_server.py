"""Zero-dependency MCP (JSON-RPC 2.0 over stdio) server.

No ``mcp`` package required: newline-delimited JSON-RPC on stdin/stdout,
implementing the tools/resources subset needed for agentic consumers. The
handler is a pure ``handle(dict) -> dict | None`` for testability.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, TextIO

from forge_doctor import __version__

# Versions this server speaks; ``initialize`` echoes the client's choice
# when supported, otherwise responds with our newest - the client decides
# whether it can still talk to us.
_SUPPORTED_PROTOCOLS = ("2024-11-05", "2025-03-26", "2025-06-18")
_PROTOCOL_VERSION = _SUPPORTED_PROTOCOLS[0]

# Tool arguments that are filesystem paths - confined to --root when set.
_PATH_ARGS = {"path", "old", "new"}


class _SandboxError(ValueError):
    """Raised when a tool argument escapes the configured --root."""


def _confine(value: Any, root: Path) -> str:
    """Resolve ``value`` under ``root``; reject escapes."""
    raw = Path(str(value)).expanduser()
    candidate = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if candidate != root and root not in candidate.parents:
        raise _SandboxError(f"path escapes sandbox root {root}: {value}")
    return str(candidate)


def _sandbox_arguments(arguments: dict[str, Any], root: Path | None) -> dict[str, Any]:
    if root is None:
        return arguments
    confined = dict(arguments)
    for key in _PATH_ARGS & confined.keys():
        confined[key] = _confine(confined[key], root)
    return confined


_TOOL_DEFS = [
    {
        "name": "scan_project",
        "description": "Run forge-doctor checks on a project; returns findings.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "categories": {"type": "array", "items": {"type": "string"}},
                "profile": {"type": "string"},
            },
        },
    },
    {
        "name": "explain_rule",
        "description": "Explain a check id: why, when OK, how to fix.",
        "inputSchema": {
            "type": "object",
            "properties": {"check_id": {"type": "string"}},
            "required": ["check_id"],
        },
    },
    {
        "name": "check_compatibility",
        "description": "Detect runtimes and list Glue migration risks.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "from": {"type": "string"},
                "to": {"type": "string"},
            },
        },
    },
    {
        "name": "get_lineage",
        "description": "Static dataset lineage graph for the project.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
        },
    },
    {
        "name": "diagnose_log",
        "description": "Fingerprint a log string against known error signatures.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "diff_findings",
        "description": "Diff two saved report JSON files (added/fixed findings).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "old": {"type": "string"},
                "new": {"type": "string"},
            },
            "required": ["old", "new"],
        },
    },
]


def _scan(arguments: dict[str, Any]) -> dict[str, Any]:
    from forge_doctor.core.service import ScanRequest, ScanService
    from forge_doctor.output.json_renderer import result_to_dict

    # Plugins are off by default over MCP - the agent host decides.
    no_plugins = not bool(arguments.get("plugins"))
    outcome = ScanService(warn=lambda _msg: None).run(
        ScanRequest(
            path=Path(str(arguments.get("path") or ".")),
            categories=tuple(str(c) for c in arguments.get("categories", ())),
            profile=str(arguments.get("profile") or "default"),
            no_plugins=no_plugins,
        )
    )
    report = outcome.report
    return {
        "findings": [result_to_dict(r) for r in report.results if r.severity.value != "pass"],
        "summary": {
            "passed": report.summary.passed,
            "warnings": report.summary.warnings,
            "errors": report.summary.errors,
        },
    }


def _explain(arguments: dict[str, Any]) -> dict[str, Any]:
    from forge_doctor.checks import builtin_checks
    from forge_doctor.core.registry import CheckRegistry

    registry = CheckRegistry()
    registry.register_all(builtin_checks())
    check = registry.get(str(arguments.get("check_id", "")).upper())
    if check is None:
        raise ValueError(f"unknown check id {arguments.get('check_id')!r}")
    return {
        "id": check.id,
        "title": check.title,
        "category": check.category,
        "why": getattr(check, "why", ""),
        "when_ok": getattr(check, "when_ok", ""),
        "fix": getattr(check, "fix", ""),
        "docs_uri": getattr(check, "docs_uri", None),
    }


def _compatibility(arguments: dict[str, Any]) -> dict[str, Any]:
    from forge_doctor.core.compat import detect_environment, migration_risks
    from forge_doctor.core.context import ProjectContext

    path = Path(str(arguments.get("path") or "."))
    env = detect_environment(ProjectContext(root=path))
    src, dst, changes = migration_risks(arguments.get("from") or env.glue, arguments.get("to"))
    return {
        "environment": {
            "glue": env.glue,
            "spark": env.spark,
            "python": env.python,
            "java": env.java,
            "iceberg": env.iceberg,
        },
        "from": src,
        "to": dst,
        "changes": changes,
    }


def _lineage(arguments: dict[str, Any]) -> dict[str, Any]:
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.lineage import build_lineage

    path = Path(str(arguments.get("path") or "."))
    return build_lineage(ProjectContext(root=path)).to_dict()


def _diagnose(arguments: dict[str, Any]) -> dict[str, Any]:
    from forge_doctor.core.diagnose import diagnose_text

    return {
        "findings": [
            {
                "id": d.signature.id,
                "title": d.signature.title,
                "severity": d.signature.severity,
                "domain": d.signature.domain,
                "count": d.count,
                "causes": list(d.signature.causes),
                "related": list(d.signature.related),
            }
            for d in diagnose_text(str(arguments.get("text", "")))
        ]
    }


def _diff(arguments: dict[str, Any]) -> dict[str, Any]:
    from forge_doctor.core.baseline import load_baseline_items

    old = load_baseline_items(Path(str(arguments["old"])))
    new = load_baseline_items(Path(str(arguments["new"])))
    added = sorted(new.keys() - old.keys())
    removed = sorted(old.keys() - new.keys())
    return {
        "added": [new[f] for f in added],
        "fixed": [old[f] for f in removed],
        "counts": {"new": len(added), "fixed": len(removed)},
    }


_TOOL_HANDLERS = {
    "scan_project": _scan,
    "explain_rule": _explain,
    "check_compatibility": _compatibility,
    "get_lineage": _lineage,
    "diagnose_log": _diagnose,
    "diff_findings": _diff,
}


def _rules_resource(check_id: str) -> dict[str, Any]:
    return {"uri": f"forge-doctor://rules/{check_id}", "text": _explain({"check_id": check_id})}


def _knowledge_resource(domain: str, name: str) -> dict[str, Any]:
    from forge_doctor.core.knowledge import load_pack

    return {
        "uri": f"forge-doctor://knowledge/{domain}/{name}",
        "text": load_pack(domain, name),
    }


def _resources_list() -> list[dict[str, Any]]:
    from forge_doctor.checks import builtin_checks
    from forge_doctor.core.knowledge import list_packs
    from forge_doctor.core.registry import CheckRegistry

    registry = CheckRegistry()
    registry.register_all(builtin_checks())
    resources = [
        {
            "uri": f"forge-doctor://rules/{check.id}",
            "name": f"rule {check.id}",
            "mimeType": "application/json",
        }
        for check in registry.all()
    ]
    for domain, name, _pack in list_packs():
        resources.append(
            {
                "uri": f"forge-doctor://knowledge/{domain}/{name}",
                "name": f"knowledge {domain}/{name}",
                "mimeType": "application/json",
            }
        )
    return resources


def _resources_read(uri: str) -> dict[str, Any]:
    parts = uri.replace("forge-doctor://", "").split("/")
    if parts[0] == "rules" and len(parts) == 2:
        return _rules_resource(parts[1])
    if parts[0] == "knowledge" and len(parts) == 3:
        return _knowledge_resource(parts[1], parts[2])
    raise ValueError(f"unknown resource {uri!r}")


def _result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle(request: dict[str, Any], root: Path | None = None) -> dict[str, Any] | None:
    """Handle one JSON-RPC request; ``None`` for notifications.

    ``root`` confines every path argument to that directory tree - the
    sandbox for hosted/agent use. A request without ``id`` is a
    notification per JSON-RPC and never produces a response.
    """
    if not isinstance(request, dict) or "method" not in request:
        return None
    method = str(request.get("method", ""))
    request_id = request.get("id")
    params = request.get("params") or {}

    if method.startswith("notifications/") or request_id is None:
        return None
    if method in {"initialize", "server/discover"}:
        client_version = str(params.get("protocolVersion", ""))
        negotiated = (
            client_version if client_version in _SUPPORTED_PROTOCOLS else _SUPPORTED_PROTOCOLS[-1]
        )
        return _result(
            request_id,
            {
                "protocolVersion": negotiated,
                "capabilities": {"tools": {}, "resources": {}},
                "serverInfo": {"name": "forge-doctor", "version": __version__},
            },
        )
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": _TOOL_DEFS})
    if method == "tools/call":
        name = str(params.get("name", ""))
        handler = _TOOL_HANDLERS.get(name)
        if handler is None:
            return _error(request_id, -32602, f"unknown tool {name!r}")
        try:
            arguments = _sandbox_arguments(params.get("arguments") or {}, root)
            output = handler(arguments)
        except _SandboxError as exc:
            return _error(request_id, -32602, str(exc))
        except Exception as exc:
            return _result(
                request_id,
                {
                    "content": [{"type": "text", "text": f"error: {exc}"}],
                    "isError": True,
                },
            )
        return _result(
            request_id,
            {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(output, indent=2, ensure_ascii=False),
                    }
                ],
                "structuredContent": output,
            },
        )
    if method == "resources/list":
        return _result(request_id, {"resources": _resources_list()})
    if method == "resources/read":
        try:
            resource = _resources_read(str(params.get("uri", "")))
        except ValueError as exc:
            return _error(request_id, -32602, str(exc))
        return _result(
            request_id,
            {
                "contents": [
                    {
                        "uri": resource["uri"],
                        "mimeType": "application/json",
                        "text": json.dumps(resource["text"], ensure_ascii=False),
                    }
                ]
            },
        )
    return _error(request_id, -32601, f"method not found: {method}")


def serve(
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    root: Path | None = None,
) -> None:
    """Newline-delimited JSON-RPC loop (MCP stdio transport).

    ``root`` sandboxes all tool path arguments to that directory tree.
    """
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    resolved_root = root.resolve() if root is not None else None
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            stdout.write(json.dumps(_error(None, -32700, "parse error")) + "\n")
            stdout.flush()
            continue
        response = handle(request, root=resolved_root)
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()
