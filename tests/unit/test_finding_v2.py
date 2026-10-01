import json
from pathlib import Path

from forge_doctor.core.models import (
    CheckResult,
    Confidence,
    ScanReport,
    Severity,
    default_fingerprint,
)
from forge_doctor.output.agent_renderer import render_agent
from forge_doctor.output.json_renderer import render_json
from forge_doctor.output.sarif_renderer import render_sarif


def _result(**kwargs: object) -> CheckResult:
    fields: dict[str, object] = {
        "check_id": "X001",
        "title": "t",
        "severity": Severity.WARNING,
        "category": "spark",
        "message": "m",
        "file": Path("a.py"),
        "line": 3,
    }
    fields.update(kwargs)
    return CheckResult(**fields)  # type: ignore[arg-type]


def test_fingerprint_auto_derived_and_stable():
    a = _result()
    b = _result()
    assert a.fingerprint == b.fingerprint
    assert len(a.fingerprint or "") == 16


def test_fingerprint_v3_semantic_identity():
    base = _result()
    # File and check_id are part of identity.
    assert _result(file=Path("b.py")).fingerprint != base.fingerprint
    assert _result(check_id="X002").fingerprint != base.fingerprint
    # v3: line moves do NOT change identity.
    assert _result(line=9).fingerprint == base.fingerprint
    # Model level has no AST context - the message is the fallback anchor for
    # file-scoped results, so it still contributes here. The runner re-anchors
    # on the AST statement, which is what makes scanned results message-stable.
    assert _result(message="other").fingerprint != base.fingerprint
    # Evidence and symbol are part of identity.
    assert _result(evidence="df.collect()").fingerprint != base.fingerprint
    assert _result(symbol="job.main").fingerprint != base.fingerprint
    # Same identity fields -> same fingerprint (idempotent).
    assert _result(severity=Severity.ERROR).fingerprint == base.fingerprint


def test_json_emits_finding_v2_fields():
    result = _result(
        column=4,
        confidence=Confidence.MEDIUM,
        evidence="df.collect()",
        tags=("performance",),
        docs_uri="https://example.com/x",
    )
    payload = json.loads(
        render_json(ScanReport(version="0.2.0", project=Path("/x"), results=[result]))
    )
    item = payload["results"][0]
    assert item["fingerprint"] == result.fingerprint
    assert item["confidence"] == "medium"
    assert item["evidence"] == "df.collect()"
    assert item["tags"] == ["performance"]
    assert item["column"] == 4
    assert item["docs_uri"] == "https://example.com/x"


def test_sarif_shape():
    report = ScanReport(
        version="0.2.0",
        project=Path("/x"),
        results=[_result(), _result(severity=Severity.PASS, check_id="X000")],
    )
    sarif = json.loads(render_sarif(report))
    assert sarif["version"] == "2.1.0"
    run = sarif["runs"][0]
    assert run["tool"]["driver"]["name"] == "forge-doctor"
    # PASS results are not emitted.
    assert len(run["results"]) == 1
    entry = run["results"][0]
    assert entry["ruleId"] == "X001"
    assert entry["level"] == "warning"
    assert entry["partialFingerprints"]["forge-doctor/fingerprint"]
    loc = entry["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"]["uri"] == "a.py"
    assert loc["region"]["startLine"] == 3


def test_agent_bundle_is_compact():
    report = ScanReport(
        version="0.2.0",
        project=Path("/x"),
        results=[
            _result(),
            _result(severity=Severity.PASS, check_id="X000"),
            _result(check_id="X002", file=None, line=None),
        ],
    )
    payload = json.loads(render_agent(report))
    assert payload["tool"] == "forge-doctor"
    assert len(payload["findings"]) == 2  # PASS excluded
    first = payload["findings"][0]
    assert first == {
        "id": "X001",
        "sev": "warning",
        "loc": "a.py:3",
        "fp": first["fp"],
    }
    assert payload["findings"][1]["loc"] is None
    # Compact separators - no pretty whitespace.
    assert render_agent(report).count("\n") == 0


def test_default_fingerprint_helper():
    result = _result()
    assert default_fingerprint(result) == result.fingerprint


def test_source_field_serialized_only_when_set():
    builtin = _result()
    plugin = _result(source="forge-doctor-example")
    payload = json.loads(
        render_json(ScanReport(version="0.2.0", project=Path("/x"), results=[builtin, plugin]))
    )
    assert "source" not in payload["results"][0]
    assert payload["results"][1]["source"] == "forge-doctor-example"
