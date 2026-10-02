"""AWS environment checks (AWS###) - local-only, never reads secret values.

Existence checks only for credentials: environment variable names and file
paths may be reported, secret values never enter a result field.
"""

from __future__ import annotations

import configparser
import contextlib
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from forge_doctor.core.models import EvidenceKind, Severity
from forge_doctor.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.models import CheckResult

_AWS_ENV_PROFILE_KEYS = ("AWS_PROFILE", "AWS_DEFAULT_PROFILE")
_AWS_ENV_REGION_KEYS = ("AWS_REGION", "AWS_DEFAULT_REGION")


def _aws_dir(ctx: ProjectContext) -> Path:
    return ctx.home / ".aws"


def _load_config(ctx: ProjectContext) -> configparser.RawConfigParser:
    """Parse ``~/.aws/config`` read-only; empty parser on missing/invalid files."""
    parser = configparser.RawConfigParser()
    with contextlib.suppress(configparser.Error, UnicodeDecodeError):
        parser.read(_aws_dir(ctx) / "config", encoding="utf-8")
    return parser


def _profile_name(section: str) -> str:
    """``profile dev`` -> ``dev``; ``default`` stays ``default``."""
    if section == "default":
        return "default"
    stripped = section.removeprefix("profile ").strip()
    return stripped or section


class AwsCli(CheckBase):
    """AWS001: AWS CLI on PATH (needed for SSO helpers and profiles)."""

    id = "AWS001"
    title = "AWS CLI"
    category = "aws"
    evidence_kind = EvidenceKind.CONFIG
    why = "SSO helpers and credential providers assume the CLI."
    when_ok = "SDK-only environments that never touch the CLI."
    fix = "Install AWS CLI v2."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        binary = ctx.which("aws")
        if binary is None:
            return [
                self.result(
                    Severity.INFO,
                    "AWS CLI not found",
                    recommendation="Install AWS CLI v2 for SSO and credential helpers.",
                )
            ]
        try:
            completed = subprocess.run(
                [binary, "--version"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
                errors="replace",
            )
        except (OSError, subprocess.TimeoutExpired):
            return [self.result(Severity.INFO, "AWS CLI found but `aws --version` failed")]
        if completed.returncode != 0:
            return [self.result(Severity.INFO, "AWS CLI found but `aws --version` failed")]
        # aws-cli v1 prints the version to stderr, v2 to stdout - check both.
        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        version = next(
            (
                token
                for line in output.splitlines()
                for token in line.split()
                if token.startswith("aws-cli/")
            ),
            None,
        )
        message = f"{version} detected" if version else "AWS CLI detected"
        return [self.result(Severity.PASS, message)]


class AwsRegion(CheckBase):
    """AWS002: a region must resolve from env or ~/.aws/config."""

    id = "AWS002"
    title = "AWS region"
    category = "aws"
    evidence_kind = EvidenceKind.CONFIG
    why = "Calls without a region fail or land in an unexpected partition."
    when_ok = "Tools that always receive --region explicitly."
    fix = "Set AWS_REGION or add `region` to ~/.aws/config."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        for key in _AWS_ENV_REGION_KEYS:
            region = ctx.env.get(key)
            if region:
                return [
                    self.result(
                        Severity.PASS,
                        f"region {region} configured via {key}",
                    )
                ]
        parser = _load_config(ctx)
        for section in parser.sections():
            region = parser.get(section, "region", fallback=None)
            if region:
                return [
                    self.result(
                        Severity.PASS,
                        f"region {region} configured (profile: {_profile_name(section)})",
                    )
                ]
        return [
            self.result(
                Severity.WARNING,
                "no AWS region configured",
                recommendation=(
                    "Set AWS_REGION/AWS_DEFAULT_REGION or add `region` to ~/.aws/config."
                ),
            )
        ]


class AwsCredentials(CheckBase):
    """AWS003: presence of credentials - never reads their values."""

    id = "AWS003"
    title = "AWS credentials"
    category = "aws"
    evidence_kind = EvidenceKind.CONFIG
    why = "Detects whether any credential source exists - values never read."
    when_ok = "Anchor check - existence only."
    fix = "Environment variables, ~/.aws/credentials, or SSO."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        if "AWS_ACCESS_KEY_ID" in ctx.env:
            return [self.result(Severity.PASS, "credentials via environment")]
        if (_aws_dir(ctx) / "credentials").is_file():
            return [self.result(Severity.PASS, "credentials file detected")]
        return [
            self.result(
                Severity.INFO,
                "no credentials detected",
                recommendation=(
                    "Configure credentials via environment, ~/.aws/credentials, or SSO."
                ),
            )
        ]


class AwsProfile(CheckBase):
    """AWS004: which AWS profile resolution will pick up."""

    id = "AWS004"
    title = "AWS profile"
    category = "aws"
    evidence_kind = EvidenceKind.CONFIG
    why = "Shows which profile credential resolution will pick."
    when_ok = "Anchor check - always reports."
    fix = "Set AWS_PROFILE or name profiles in ~/.aws/config."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        for key in _AWS_ENV_PROFILE_KEYS:
            profile = ctx.env.get(key)
            if profile:
                return [self.result(Severity.PASS, f"profile: {profile} ({key})")]
        names = sorted({_profile_name(section) for section in _load_config(ctx).sections()})
        if names:
            return [
                self.result(
                    Severity.INFO,
                    "profiles in ~/.aws/config: " + ", ".join(names),
                )
            ]
        return [self.result(Severity.INFO, "using default resolution")]


CHECKS: list[Check] = [
    AwsCli(),
    AwsRegion(),
    AwsCredentials(),
    AwsProfile(),
]
