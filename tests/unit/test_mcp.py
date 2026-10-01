import json

from forge_doctor.integrations.mcp_server import handle


def _call(method: str, params: dict | None = None, req_id: int = 1) -> dict:
    out = handle({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})
    assert out is not None
    return out


def test_initialize():
    out = _call("initialize")
    assert out["result"]["serverInfo"]["name"] == "forge-doctor"
    assert "capabilities" in out["result"]


def test_initialize_negotiates_protocol():
    out = _call("initialize", {"protocolVersion": "2024-11-05"})
    assert out["result"]["protocolVersion"] == "2024-11-05"
    out = _call("initialize", {"protocolVersion": "2025-03-26"})
    assert out["result"]["protocolVersion"] == "2025-03-26"
    # Unknown client version: respond with our newest supported.
    out = _call("initialize", {"protocolVersion": "1999-01-01"})
    assert out["result"]["protocolVersion"] in ("2024-11-05", "2025-03-26", "2025-06-18")


def test_server_discover_alias():
    out = _call("server/discover", {"protocolVersion": "2024-11-05"})
    assert out["result"]["serverInfo"]["name"] == "forge-doctor"


def test_root_sandbox_rejects_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    sandbox = tmp_path / "project"
    sandbox.mkdir()
    out = handle(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {
                "name": "scan_project",
                "arguments": {"path": "../outside"},
            },
        },
        root=sandbox,
    )
    assert out["error"]["code"] == -32602
    assert "sandbox" in out["error"]["message"]


def test_root_sandbox_allows_inside(tmp_path):
    sandbox = tmp_path / "project"
    sandbox.mkdir()
    (sandbox / "pyproject.toml").write_text("[project]\nname='x'\n")
    out = handle(
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {"name": "scan_project", "arguments": {"path": "."}},
        },
        root=sandbox,
    )
    assert "result" in out


def test_scan_project_applies_suppressions(tmp_path):
    """ScanService policy runs inside the MCP scan path."""
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='x'\n\n"
        "[[tool.forge-doctor.suppressions]]\n"
        'rule = "REP002"\nreason = "docs live elsewhere"\nowner = "team"\n'
    )
    out = _call(
        "tools/call",
        {"name": "scan_project", "arguments": {"path": str(tmp_path)}},
    )
    findings = out["result"]["structuredContent"]["findings"]
    assert all(f["check_id"] != "REP002" for f in findings)


def test_ping_and_notifications():
    assert _call("ping")["result"] == {}
    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    # A request without an id is a notification - never a response.
    assert handle({"jsonrpc": "2.0", "method": "initialized"}) is None
    assert handle({"jsonrpc": "2.0", "method": "tools/list"}) is None


def test_tools_list():
    tools = _call("tools/list")["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {
        "scan_project",
        "explain_rule",
        "check_compatibility",
        "get_lineage",
        "diagnose_log",
        "diff_findings",
    } <= names


def test_tools_call_explain():
    out = _call("tools/call", {"name": "explain_rule", "arguments": {"check_id": "SPARK001"}})
    content = out["result"]["structuredContent"]
    assert content["id"] == "SPARK001"
    assert content["category"] == "spark"


def test_tools_call_unknown():
    out = _call("tools/call", {"name": "nope"})
    assert "error" in out


def test_tools_call_diagnose():
    out = _call(
        "tools/call",
        {"name": "diagnose_log", "arguments": {"text": "OutOfMemoryError x\n"}},
    )
    assert "structuredContent" in out["result"]


def test_resources_list_and_read():
    resources = _call("resources/list")["result"]["resources"]
    uris = {r["uri"] for r in resources}
    assert any(u.startswith("forge-doctor://rules/") for u in uris)
    assert any(u.startswith("forge-doctor://knowledge/") for u in uris)

    rule_uri = next(u for u in uris if u == "forge-doctor://rules/SPARK001")
    out = _call("resources/read", {"uri": rule_uri})
    contents = out["result"]["contents"]
    assert contents[0]["mimeType"] == "application/json"
    payload = json.loads(contents[0]["text"])
    assert payload["id"] == "SPARK001"


def test_resources_read_bad_uri():
    out = _call("resources/read", {"uri": "forge-doctor://bogus/x"})
    assert "error" in out


def test_unknown_method():
    out = _call("nope/method")
    assert out["error"]["code"] == -32601
