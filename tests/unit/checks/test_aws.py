"""Unit tests for the AWS checks (AWS###)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from forge_doctor.checks.aws import (
    CHECKS,
    AwsCli,
    AwsCredentials,
    AwsProfile,
    AwsRegion,
)
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.models import CheckResult, Severity

SECRET = "AKIAFAKESECRET123"

_AWS_ENV_VARS = (
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "AWS_PROFILE",
    "AWS_DEFAULT_PROFILE",
    "AWS_ACCESS_KEY_ID",
)


def _result_text(result: CheckResult) -> str:
    """Every field of a result flattened to text for secret-leak assertions."""
    return " ".join(
        str(part)
        for part in (
            result.check_id,
            result.title,
            result.severity,
            result.category,
            result.message,
            result.file,
            result.line,
            result.recommendation,
        )
        if part is not None
    )


@pytest.fixture(autouse=True)
def clean_aws_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _AWS_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    return fake_home


@pytest.fixture
def ctx(tmp_path: Path) -> ProjectContext:
    return ProjectContext(root=tmp_path)


def write_aws_file(home: Path, name: str, content: str) -> Path:
    aws_dir = home / ".aws"
    aws_dir.mkdir(exist_ok=True)
    path = aws_dir / name
    path.write_text(content, encoding="utf-8")
    return path


class TestAwsCli:
    """AWS001."""

    def test_missing_info(self, ctx: ProjectContext, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)
        (result,) = AwsCli().run(ctx)
        assert result.check_id == "AWS001"
        assert result.severity == Severity.INFO
        assert result.message == "AWS CLI not found"

    def test_detected_pass(self, ctx: ProjectContext, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(shutil, "which", lambda name, *a, **k: f"C:/tools/{name}.exe")

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            # aws-cli v1 emits the version on stderr; v2 on stdout.
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout="",
                stderr="aws-cli/2.15.30 Python/3.11.9 Windows/10 exe/AMD64\n",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)
        (result,) = AwsCli().run(ctx)
        assert result.severity == Severity.PASS
        assert "aws-cli/2.15.30" in result.message


class TestAwsRegion:
    """AWS002."""

    def test_region_from_env(self, ctx: ProjectContext, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        (result,) = AwsRegion().run(ctx)
        assert result.severity == Severity.PASS
        assert "us-east-1" in result.message

    def test_region_from_default_config(self, ctx: ProjectContext, home: Path) -> None:
        write_aws_file(
            home,
            "config",
            f"[default]\nregion = eu-west-1\naws_secret_access_key = {SECRET}\n",
        )
        (result,) = AwsRegion().run(ctx)
        assert result.severity == Severity.PASS
        assert "eu-west-1" in result.message
        assert "profile: default" in result.message
        assert SECRET not in _result_text(result)

    def test_region_from_named_profile(self, ctx: ProjectContext, home: Path) -> None:
        write_aws_file(home, "config", "[profile etl]\nregion = ap-south-1\n")
        (result,) = AwsRegion().run(ctx)
        assert result.severity == Severity.PASS
        assert "profile: etl" in result.message

    def test_no_region_warns(self, ctx: ProjectContext, home: Path) -> None:
        (result,) = AwsRegion().run(ctx)
        assert result.severity == Severity.WARNING
        assert result.message == "no AWS region configured"


class TestAwsCredentials:
    """AWS003."""

    def test_env_credentials_pass(
        self, ctx: ProjectContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", SECRET)
        (result,) = AwsCredentials().run(ctx)
        assert result.severity == Severity.PASS
        assert result.message == "credentials via environment"
        assert SECRET not in _result_text(result)

    def test_credentials_file_pass(self, ctx: ProjectContext, home: Path) -> None:
        write_aws_file(
            home,
            "credentials",
            f"[default]\naws_access_key_id = AKIAFAKE\naws_secret_access_key = {SECRET}\n",
        )
        (result,) = AwsCredentials().run(ctx)
        assert result.severity == Severity.PASS
        assert result.message == "credentials file detected"
        assert SECRET not in _result_text(result)

    def test_no_credentials_info(self, ctx: ProjectContext, home: Path) -> None:
        (result,) = AwsCredentials().run(ctx)
        assert result.severity == Severity.INFO
        assert result.message == "no credentials detected"


class TestAwsProfile:
    """AWS004."""

    def test_profile_from_env(self, ctx: ProjectContext, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AWS_PROFILE", "analytics")
        (result,) = AwsProfile().run(ctx)
        assert result.severity == Severity.PASS
        assert "profile: analytics" in result.message

    def test_profiles_listed_from_config(self, ctx: ProjectContext, home: Path) -> None:
        write_aws_file(
            home,
            "config",
            f"[default]\nregion = us-east-1\n"
            f"[profile etl]\nregion = eu-west-1\n"
            f"aws_secret_access_key = {SECRET}\n",
        )
        (result,) = AwsProfile().run(ctx)
        assert result.severity == Severity.INFO
        assert "default" in result.message
        assert "etl" in result.message
        assert SECRET not in _result_text(result)

    def test_default_resolution_info(self, ctx: ProjectContext, home: Path) -> None:
        (result,) = AwsProfile().run(ctx)
        assert result.severity == Severity.INFO
        assert result.message == "using default resolution"


def test_checks_metadata() -> None:
    assert [check.id for check in CHECKS] == [
        "AWS001",
        "AWS002",
        "AWS003",
        "AWS004",
    ]
    assert all(check.category == "aws" for check in CHECKS)
