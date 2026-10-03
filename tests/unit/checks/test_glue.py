"""Unit tests for the AWS Glue AST checks (GLUE###)."""

from __future__ import annotations

from pathlib import Path

from forge_doctor_data.analyzers.glue_ast import GlueAnalyzer
from forge_doctor_data.checks.glue import (
    CHECKS,
    DynamicFrameMixing,
    EolGlueRuntime,
    GlueUsage,
    JobParameters,
    analyze_project,
)
from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.models import Severity

JOB = """\
import sys

from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext

sc = SparkContext()
glue_context = GlueContext(sc)
job = Job(glue_context)

args = getResolvedOptions(sys.argv, ["JOB_NAME"])
job.init(args["JOB_NAME"], args)

frame = glue_context.create_dynamic_frame.from_catalog(
    database="db", table_name="t"
)
df = frame.toDF()
df.write.parquet("s3://bucket/out")
job.commit()
"""

DEPLOY_EOL = """\
import boto3

client = boto3.client("glue")
client.create_job(
    Name="legacy-etl",
    Role="arn:aws:iam::123456789012:role/GlueServiceRole",
    GlueVersion="2.0",
)
"""

DEPLOY_DEFAULT_ARGS = """\
import boto3

client = boto3.client("glue")
client.create_job(
    Name="legacy-etl",
    Role="arn:aws:iam::123456789012:role/GlueServiceRole",
    DefaultArguments={"--glue-version": "1.0"},
)
"""

DEPLOY_AGING = """\
import boto3

boto3.client("glue").create_job(Name="aging", GlueVersion="3.0")
"""

DEPLOY_CURRENT = """\
import boto3

boto3.client("glue").create_job(Name="modern", GlueVersion="4.0")
"""

BOTO3_ONLY = """\
import boto3

client = boto3.client("glue")
client.start_job_run(JobName="nightly")
"""

NO_PARAMS_JOB = """\
from awsglue.job import Job


def run(job):
    job.init("hardcoded-name", {})
    job.commit()
"""

MIXED_JOB = """\
from awsglue.dynamicframe import DynamicFrame
from pyspark.sql import DataFrame


def widen(frame: DynamicFrame) -> DataFrame:
    return frame.toDF()
"""

PLAIN_PYTHON = """\
items = [1, 2, 3]
result = [item * 2 for item in items]
"""


def make_context(tmp_path: Path, **sources: str) -> ProjectContext:
    for name, source in sources.items():
        filename = name if name.endswith(".py") else f"{name}.py"
        path = tmp_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


def test_analyze_project_buckets(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, job=JOB)
    buckets = analyze_project(ctx)
    assert buckets["get_resolved_options"] == [(Path("job.py"), 12)]
    assert buckets["job_init"] == [(Path("job.py"), 13)]
    assert buckets["toDF"] == [(Path("job.py"), 18)]
    assert buckets["job_commit"] == [(Path("job.py"), 20)]


def test_analyze_source_gate() -> None:
    analyzer = GlueAnalyzer()
    assert analyzer.analyze_source(PLAIN_PYTHON, Path("utils.py")) == []
    findings = analyzer.analyze_source(DEPLOY_EOL, Path("deploy.py"))
    assert [(f.pattern, f.line) for f in findings] == [("glue_version:2.0", 7)]


class TestGlueUsage:
    """GLUE001."""

    def test_counts_glue_files(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, job=JOB, deploy=DEPLOY_EOL, utils=PLAIN_PYTHON)
        (result,) = GlueUsage().run(ctx)
        assert result.severity == Severity.PASS
        assert result.message == "2 file(s) use awsglue/glue client"

    def test_no_glue_info(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, utils=PLAIN_PYTHON)
        (result,) = GlueUsage().run(ctx)
        assert result.severity == Severity.INFO
        assert result.message == "no AWS Glue usage detected"


class TestEolGlueRuntime:
    """GLUE002."""

    def test_eol_kwarg_version_warning(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, deploy=DEPLOY_EOL)
        (result,) = EolGlueRuntime().run(ctx)
        assert result.check_id == "GLUE002"
        assert result.severity == Severity.WARNING
        assert result.file == Path("deploy.py")
        assert result.line == 7
        assert "2.0" in result.message
        assert result.recommendation is not None

    def test_eol_default_arguments_warning(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, deploy=DEPLOY_DEFAULT_ARGS)
        (result,) = EolGlueRuntime().run(ctx)
        assert result.severity == Severity.WARNING
        assert result.file == Path("deploy.py")
        assert result.line == 7
        assert "1.0" in result.message

    def test_aging_version_info(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, deploy=DEPLOY_AGING)
        (result,) = EolGlueRuntime().run(ctx)
        assert result.severity == Severity.INFO
        assert result.line == 3
        assert "3.0" in result.message

    def test_current_version_clean(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, deploy=DEPLOY_CURRENT)
        assert EolGlueRuntime().run(ctx) == []

    def test_no_glue_empty(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, utils=PLAIN_PYTHON)
        assert EolGlueRuntime().run(ctx) == []


class TestJobParameters:
    """GLUE003."""

    def test_resolved_options_pass(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, job=JOB)
        (result,) = JobParameters().run(ctx)
        assert result.severity == Severity.PASS
        assert "getResolvedOptions" in result.message

    def test_missing_params_info(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, job=NO_PARAMS_JOB)
        (result,) = JobParameters().run(ctx)
        assert result.severity == Severity.INFO
        assert "no getResolvedOptions usage" in result.message
        assert result.recommendation is not None

    def test_boto3_glue_without_params_info(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, deploy=BOTO3_ONLY)
        (result,) = JobParameters().run(ctx)
        assert result.severity == Severity.INFO

    def test_no_glue_empty(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, utils=PLAIN_PYTHON)
        assert JobParameters().run(ctx) == []


class TestDynamicFrameMixing:
    """GLUE004."""

    def test_mixed_file_info(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, job=MIXED_JOB)
        (result,) = DynamicFrameMixing().run(ctx)
        assert result.check_id == "GLUE004"
        assert result.severity == Severity.INFO
        assert result.file == Path("job.py")
        assert result.line == 1
        assert result.recommendation is not None

    def test_glue_job_without_dynamicframe_marker_clean(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, job=JOB)
        assert DynamicFrameMixing().run(ctx) == []

    def test_no_glue_empty(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, utils=PLAIN_PYTHON)
        assert DynamicFrameMixing().run(ctx) == []


def test_syntax_error_file_skipped(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, broken="import awsglue\ndef broken(:\n")
    assert EolGlueRuntime().run(ctx) == []
    (usage,) = GlueUsage().run(ctx)
    assert usage.severity == Severity.INFO


def test_checks_metadata() -> None:
    assert {check.id for check in CHECKS} == {
        "GLUE001",
        "GLUE002",
        "GLUE003",
        "GLUE004",
    }
    assert all(check.category == "glue" for check in CHECKS)
