"""LSP integration - publishDiagnostics from scan findings.

The diagnostic mapping, URI decoding, and publish-plan logic are pure and
unit-testable without ``pygls``; the server itself only boots when the
optional ``pygls`` extra is installed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from forge_doctor.core.models import CheckResult, Severity

LSP_SEVERITY = {
    Severity.ERROR: 1,  # DiagnosticSeverity.Error
    Severity.WARNING: 2,  # Warning
    Severity.INFO: 3,  # Information
    Severity.PASS: 4,  # Hint (usually filtered out upstream)
}

DEBOUNCE_SECONDS = 0.4


def uri_to_fs_path(uri: str) -> Path | None:
    """Decode a ``file://`` URI to a local path (percent-encoding, drives).

    Returns ``None`` for non-file URIs. Mirrors ``pygls.uris.to_fs_path``
    so the mapping can be tested without the optional dependency.
    """
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    path = unquote(parsed.path)
    if parsed.netloc and parsed.netloc not in ("", "localhost"):
        # UNC path: file://server/share/x -> //server/share/x
        path = f"//{parsed.netloc}{path}"
    # Windows drive URIs arrive as "/C:/..." - strip the leading slash.
    if len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return Path(path)


def finding_to_diagnostic(result: CheckResult) -> dict[str, Any]:
    """Map one CheckResult to an LSP Diagnostic dict."""
    line = max((result.line or 1) - 1, 0)  # LSP is 0-based
    col = max((result.column or 1) - 1, 0)
    end_line = max((result.end_line or result.line or 1) - 1, line)
    end_col = max((result.end_column or (result.column or 1) + 1) - 1, col + 1)
    return {
        "range": {
            "start": {"line": line, "character": col},
            "end": {"line": end_line, "character": end_col},
        },
        "severity": LSP_SEVERITY.get(result.severity, 3),
        "source": "forge-doctor",
        "code": result.check_id,
        "message": result.message
        + (f" - {result.recommendation}" if result.recommendation else ""),
    }


def diagnostics_for_results(results: list[CheckResult]) -> dict[str, list[dict[str, Any]]]:
    """``{file_uri_suffix: [diagnostics]}`` grouped by project-relative file."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        if result.file is None or result.severity is Severity.PASS:
            continue
        grouped.setdefault(result.file.as_posix(), []).append(finding_to_diagnostic(result))
    return grouped


def diagnostics_plan(
    grouped: dict[str, list[dict[str, Any]]], previously_published: set[str]
) -> dict[str, list[dict[str, Any]]]:
    """Publish plan including empty diagnostics for files that went clean.

    LSP clears a file's squiggles only when the server publishes an empty
    set for that URI - dropping the key entirely leaves stale diagnostics.
    """
    plan = dict(grouped)
    for stale in previously_published - set(grouped):
        plan[stale] = []
    return plan


def run_stdio(root: Path | None = None) -> None:
    """Boot the LSP server; clean error when pygls is missing."""
    try:
        from lsprotocol import types as lsp
        from pygls.lsp.server import LanguageServer
    except ImportError:
        raise SystemExit(
            "forge-doctor lsp requires the optional 'lsp' extra: pip install forge-doctor[lsp]"
        ) from None

    try:  # pygls has the battle-tested decoder; fall back to ours.
        from pygls.uris import to_fs_path as _to_fs_path
    except ImportError:
        _to_fs_path = uri_to_fs_path

    import threading

    from forge_doctor.core.service import ScanRequest, ScanService

    server = LanguageServer("forge-doctor", "1")
    state: dict[str, Any] = {
        "root": root.resolve() if root is not None else None,
        "published": set(),  # relative paths with diagnostics last publish
        "open_docs": {},  # uri -> relative path (overlay bookkeeping)
        "timer": None,
    }

    def _workspace_root(fallback: Path | None = None) -> Path:
        """Workspace root: initialize params > --root > file's dir."""
        if server.workspace.root_path is not None:
            return Path(server.workspace.root_path)
        if state["root"] is not None:
            return Path(state["root"])
        return fallback or Path.cwd()

    def _overlay(scan_root: Path) -> dict[str, str]:
        """Unsaved buffers: project-relative path -> current doc text."""
        overlay: dict[str, str] = {}
        for uri in list(state["open_docs"]):
            try:
                doc = server.workspace.get_text_document(uri)
            except Exception:
                continue
            fs_path = _to_fs_path(uri)
            if fs_path is None:
                continue
            try:
                relative = fs_path.resolve().relative_to(scan_root)
            except ValueError:
                continue
            overlay[relative.as_posix()] = doc.source
        return overlay

    def _scan_and_publish(uri_hint: str | None = None) -> None:
        hint = _to_fs_path(uri_hint) if uri_hint else None
        scan_root = _workspace_root(hint.parent if hint is not None and hint.is_file() else hint)
        outcome = ScanService(warn=lambda _msg: None).run(
            ScanRequest(
                path=scan_root,
                overlay=_overlay(scan_root),
                no_plugins=False,
            )
        )
        grouped = diagnostics_for_results(outcome.report.results)
        plan = diagnostics_plan(grouped, state["published"])
        for relative, diagnostics in plan.items():
            server.publish_diagnostics(
                f"file://{scan_root.joinpath(relative).as_posix()}", diagnostics
            )
        state["published"] = set(grouped)

    def _schedule(uri: str, immediate: bool = False) -> None:
        pending: threading.Timer | None = state["timer"]
        if pending is not None:
            pending.cancel()
        if immediate:
            _scan_and_publish(uri)
            return
        timer = threading.Timer(DEBOUNCE_SECONDS, _scan_and_publish, args=(uri,))
        state["timer"] = timer
        timer.daemon = True
        timer.start()

    @server.feature(lsp.INITIALIZE)
    def _initialize(params: lsp.InitializeParams) -> None:
        root_uri = params.root_uri
        if root_uri:
            resolved = _to_fs_path(str(root_uri))
            if resolved is not None:
                state["root"] = resolved.resolve()

    @server.feature(lsp.TEXT_DOCUMENT_DID_OPEN)
    def _opened(params: lsp.DidOpenTextDocumentParams) -> None:
        state["open_docs"][params.text_document.uri] = True
        _schedule(params.text_document.uri, immediate=True)

    @server.feature(lsp.TEXT_DOCUMENT_DID_CLOSE)
    def _closed(params: lsp.DidCloseTextDocumentParams) -> None:
        state["open_docs"].pop(params.text_document.uri, None)

    @server.feature(lsp.TEXT_DOCUMENT_DID_SAVE)
    def _saved(params: lsp.DidSaveTextDocumentParams) -> None:
        _schedule(params.text_document.uri, immediate=True)

    @server.feature(lsp.TEXT_DOCUMENT_DID_CHANGE)
    def _changed(params: lsp.DidChangeTextDocumentParams) -> None:
        _schedule(params.text_document.uri)

    server.start_io()
