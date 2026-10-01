"""Docker checks (DOCKER*): line-based Dockerfile hygiene.

Text parsing only - images are never built. Secret-looking variable *names*
may be reported; their values never enter a result field.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from forge_doctor.core.models import EvidenceKind, Severity
from forge_doctor.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.models import CheckResult

CATEGORY = "docker"

_FROM_RE = re.compile(r"^\s*FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?", re.IGNORECASE)
_USER_RE = re.compile(r"^\s*USER\s+\S", re.IGNORECASE)
_ENV_ARG_RE = re.compile(r"^\s*(ENV|ARG)\s+(.+?)\s*$", re.IGNORECASE)
_SECRET_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "apikey",
    "api_key",
    "access_key",
    "private_key",
)


def _dockerfiles(ctx: ProjectContext) -> list[Path]:
    """Relative paths to ``Dockerfile``/``Dockerfile.<suffix>`` at any depth."""
    return sorted(
        path
        for path in ctx.files
        if path.name.lower() == "dockerfile" or path.name.lower().startswith("dockerfile.")
    )


def _lines(ctx: ProjectContext, path: Path) -> list[str]:
    text = ctx.read_text(path)
    return text.splitlines() if text else []


def _floating_base(image: str) -> str | None:
    """Why a FROM ref floats; ``None`` when pinned or statically unresolvable."""
    if "$" in image or "@" in image:
        return None  # ARG-driven ref or digest-pinned.
    colon = image.rfind(":")
    if colon <= image.rfind("/"):  # a colon before the last slash is a registry port.
        return "no tag"
    if image[colon + 1 :].lower() == "latest":
        return "the 'latest' tag"
    return None


def _env_arg_names(argument: str) -> list[str]:
    """Variable names declared by one ENV/ARG instruction - never values."""
    tokens = argument.split()
    if not tokens:
        return []
    if "=" in tokens[0]:
        return [token.split("=", 1)[0] for token in tokens if "=" in token]
    return [tokens[0]]  # legacy `ENV NAME value` form


def _looks_secret(name: str) -> bool:
    lowered = name.lower()
    return any(part in lowered for part in _SECRET_PARTS)


class DockerfilePresent(CheckBase):
    """DOCKER001 - anchor check: does the project ship a Dockerfile?"""

    id = "DOCKER001"
    title = "Dockerfile present"
    category = CATEGORY
    evidence_kind = EvidenceKind.CONFIG
    why = "Container builds start from a Dockerfile the scanner can find."
    when_ok = "Projects that do not ship a container image."
    fix = "Add a Dockerfile at the project root, or Dockerfile.<variant> per environment."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        files = _dockerfiles(ctx)
        if not files:
            return [
                self.result(
                    Severity.INFO,
                    "No Dockerfile found.",
                    recommendation="Add a Dockerfile if this project ships a container image.",
                )
            ]
        noun = "Dockerfile" if len(files) == 1 else "Dockerfiles"
        return [self.result(Severity.PASS, f"{len(files)} {noun} found.")]


class BaseImageTag(CheckBase):
    """DOCKER002 - FROM lines must pin a version tag or digest."""

    id = "DOCKER002"
    title = "Base image tag"
    category = CATEGORY
    evidence_kind = EvidenceKind.CONFIG
    why = "Floating base images make builds non-reproducible and silently pull breaking changes."
    when_ok = "FROM scratch and references to earlier build stages need no tag."
    fix = "Pin the image to a version tag (python:3.12-slim) or an @sha256: digest."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        files = _dockerfiles(ctx)
        results: list[CheckResult] = []
        for path in files:
            stages: set[str] = set()
            for lineno, line in enumerate(_lines(ctx, path), start=1):
                match = _FROM_RE.match(line)
                if match is None:
                    continue
                image, alias = match.group(1), match.group(2)
                if image.lower() != "scratch" and image.lower() not in stages:
                    reason = _floating_base(image)
                    if reason is not None:
                        results.append(
                            self.result(
                                Severity.WARNING,
                                f"FROM {image}: base image has {reason}.",
                                file=path,
                                line=lineno,
                                recommendation=(
                                    "Pin the base image to a version tag or @sha256: digest."
                                ),
                            )
                        )
                if alias:
                    stages.add(alias.lower())
        if results or not files:
            return results
        return [self.result(Severity.PASS, "All base images are pinned.")]


class NonRootUser(CheckBase):
    """DOCKER003 - images should drop root privileges via USER."""

    id = "DOCKER003"
    title = "Non-root user"
    category = CATEGORY
    evidence_kind = EvidenceKind.CONFIG
    why = "A container running as root widens the blast radius of a compromised process."
    when_ok = "Single-stage build images and dev containers where root is expected."
    fix = "Create a user and add `USER <name>` near the end of the Dockerfile."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        results: list[CheckResult] = []
        for path in _dockerfiles(ctx):
            if any(_USER_RE.match(line) for line in _lines(ctx, path)):
                results.append(self.result(Severity.PASS, "USER instruction found.", file=path))
            else:
                results.append(
                    self.result(
                        Severity.INFO,
                        "No USER instruction; the container runs as root.",
                        file=path,
                        recommendation="Add `USER <non-root>` after installing dependencies.",
                    )
                )
        return results


class SecretEnvNames(CheckBase):
    """DOCKER004 - ENV/ARG names that look like secrets. Names only, never values."""

    id = "DOCKER004"
    title = "Secret-looking env name"
    category = CATEGORY
    evidence_kind = EvidenceKind.CONFIG
    why = "ENV/ARG values are baked into image layers, readable by anyone holding the image."
    when_ok = "Non-secret toggles that merely share a keyword (e.g. TOKENIZER)."
    fix = "Pass secrets at runtime (-e, --secret) or through a secrets manager instead."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        files = _dockerfiles(ctx)
        results: list[CheckResult] = []
        for path in files:
            for lineno, line in enumerate(_lines(ctx, path), start=1):
                match = _ENV_ARG_RE.match(line)
                if match is None:
                    continue
                instruction = match.group(1).upper()
                for name in _env_arg_names(match.group(2)):
                    if _looks_secret(name):
                        results.append(
                            self.result(
                                Severity.WARNING,
                                f"{instruction} variable name looks like a secret: {name}.",
                                file=path,
                                line=lineno,
                                recommendation=(
                                    "Remove it from the Dockerfile; pass secrets at runtime "
                                    "or via a secrets manager."
                                ),
                            )
                        )
        if results or not files:
            return results
        return [self.result(Severity.PASS, "No secret-looking ENV/ARG names.")]


CHECKS: list[Check] = [
    DockerfilePresent(),
    BaseImageTag(),
    NonRootUser(),
    SecretEnvNames(),
]
