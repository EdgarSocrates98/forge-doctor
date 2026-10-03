from pathlib import Path

from forge_doctor_data.core.models import CheckResult, ScanReport, Severity
from forge_doctor_data.output.html_renderer import render_html


def _report() -> ScanReport:
    return ScanReport(
        version="0.1.0",
        project=Path("/tmp/my-proj"),
        results=[
            CheckResult(
                check_id="X001",
                title="bad <thing>",
                severity=Severity.WARNING,
                category="python",
                message="m <script>",
                file=Path("a.py"),
            )
        ],
    )


def test_render_html_is_self_contained():
    html = render_html(_report(), check_count=47)
    assert html.lstrip().lower().startswith("<!doctype html")
    assert "<html" in html
    assert "X001" in html
    assert "my-proj" in html
    # Rich export escapes markup in messages.
    assert "<script>" not in html
    assert "bad &lt;thing&gt;" in html or "bad <thing>" not in html


def test_render_html_empty_report():
    html = render_html(ScanReport(version="0.1.0", project=Path("/tmp/x")))
    assert "<html" in html
    assert "No problems found" in html
