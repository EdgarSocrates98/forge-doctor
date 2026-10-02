"""Application service shared by CLI, MCP, and LSP frontends.

`ScanService` owns the whole analysis pipeline - context, plugin
resolution, check execution, profile, policy, baseline, cache - so every
frontend consumes identical results. Frontends keep only transport
concerns (argv parsing, JSON-RPC framing, textDocument/* handling).
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from forge_doctor.checks import builtin_checks
from forge_doctor.core.baseline import apply_baseline, save_baseline
from forge_doctor.core.cache import scan_cache
from forge_doctor.core.config import ForgeDoctorConfig
from forge_doctor.core.context import ProjectContext, ScanOptions
from forge_doctor.core.models import ScanReport
from forge_doctor.core.policy import apply_policy
from forge_doctor.core.profiles import PROFILES, apply_profile
from forge_doctor.core.registry import CheckRegistry
from forge_doctor.core.runner import CheckRunner
from forge_doctor.plugins.discovery import load_plugin_checks, split_allow
from forge_doctor.plugins.protocol import Check

if TYPE_CHECKING:
    from forge_doctor.core.cache import ScanCache

PLUGINS_ENV_VAR = "FORGE_DOCTOR_NO_PLUGINS"


class ScanRequestError(ValueError):
    """Invalid scan request (unknown category, profile, ...)."""


@dataclass(frozen=True)
class ScanRequest:
    """Everything a frontend must supply to run a scan."""

    path: Path
    categories: tuple[str, ...] = ()
    ignore: tuple[str, ...] = ()
    fail_on: str = "error"
    verbose: bool = False
    # Tri-state: None = auto (on locally, off in CI); True/False explicit.
    cache: bool | None = None
    no_plugins: bool = False
    # None -> config policy.extends -> "default".
    profile: str | None = None
    files: tuple[str, ...] | None = None
    baseline: Path | None = None
    save_baseline: Path | None = None
    new_only: bool = False
    # Unsaved-buffer overlay for LSP: project-relative posix -> content.
    overlay: Mapping[str, str] | None = None


@dataclass
class ScanOutcome:
    """Scan result plus the internals frontends may want for stats."""

    report: ScanReport
    ctx: ProjectContext
    runner: CheckRunner
    selected: int
    plugin_errors: list[str] = field(default_factory=list)

    @property
    def cache(self) -> ScanCache:
        return scan_cache(self.ctx)


def _warn_default(msg: str) -> None:
    from rich.console import Console

    Console(stderr=True).print(msg)


class ScanService:
    """Frontend-agnostic scan orchestration.

    ``warn`` receives human-readable notices (plugin skips, unknown
    categories). Defaults to stderr; MCP/LSP route it to their log channel.
    """

    def __init__(
        self,
        *,
        env: Mapping[str, str] | None = None,
        warn: Callable[[str], None] | None = None,
    ) -> None:
        self._env = dict(env) if env is not None else dict(os.environ)
        self._warn = warn or _warn_default

    def plugins_enabled(self, no_plugins: bool) -> bool:
        """CLI flag wins; ``FORGE_DOCTOR_NO_PLUGINS`` is a kill-switch."""
        if no_plugins:
            return False
        flag = self._env.get(PLUGINS_ENV_VAR, "").strip().lower()
        return flag not in ("1", "true", "yes", "on")

    def build_registry(
        self,
        no_plugins: bool = False,
        config: ForgeDoctorConfig | None = None,
    ) -> tuple[CheckRegistry, list[str]]:
        """Built-ins + gated plugin discovery + post-load check filters."""
        registry = CheckRegistry()
        registry.register_all(builtin_checks())
        if not self.plugins_enabled(no_plugins):
            return registry, []
        rules = config.plugins if config is not None else None
        plugin_checks, plugin_errors = load_plugin_checks(
            trusted=rules.trusted if rules else (),
            allow=rules.allow if rules else (),
            strict=bool(rules and rules.mode == "strict"),
        )
        allow = rules.allow if rules else ()
        _, allow_check_ids = split_allow(allow)
        for check in plugin_checks:
            reason = self._check_rejection(check, rules, allow, allow_check_ids)
            if reason is not None:
                plugin_errors.append(reason)
                continue
            try:
                registry.register(check)
            except ValueError as exc:
                plugin_errors.append(str(exc))
        return registry, plugin_errors

    @staticmethod
    def _check_rejection(
        check: Check,
        rules: object,
        allow: tuple[str, ...],
        allow_check_ids: frozenset[str],
    ) -> str | None:
        """Post-load gates that can only be evaluated once code exists."""
        if (
            allow_check_ids
            and check.id not in allow_check_ids
            and not (_plugin_identity_allowed(check, allow))
        ):
            identity = getattr(check, "__fd_identity__", None)
            dist = getattr(identity, "distribution", None) or check.id
            return f"{check.id}: plugin '{dist}' not in [tool.forge-doctor.plugins] allow"
        enabled = getattr(rules, "checks_enabled", ())
        if enabled and check.id not in enabled:
            return f"{check.id}: not in plugins.checks.enabled"
        if check.id in getattr(rules, "checks_disabled", ()):
            return f"{check.id}: disabled via plugins.checks.disabled"
        return None

    def run(self, request: ScanRequest) -> ScanOutcome:
        """THE scan pipeline; raises ScanRequestError on bad input."""
        options = ScanOptions(
            categories=request.categories,
            ignore=request.ignore,
            fail_on=request.fail_on,
            verbose=request.verbose,
            use_cache=request.cache,
            overlay=request.overlay,
        )
        ctx = ProjectContext(root=request.path, options=options)
        registry, plugin_errors = self.build_registry(request.no_plugins, ctx.config)

        selected = registry.select(
            categories=request.categories,
            ignore=(*ctx.config.ignore, *request.ignore),
        )
        unknown = sorted(set(request.categories) - set(registry.categories()))
        if unknown:
            valid = ", ".join(registry.categories())
            if not selected:
                raise ScanRequestError(
                    f"Unknown categor{'y' if len(unknown) == 1 else 'ies'}: "
                    f"{', '.join(unknown)} (valid: {valid})"
                )
            self._warn(f"[yellow]Ignoring unknown categories:[/yellow] {', '.join(unknown)}")

        runner = CheckRunner(registry)
        report = runner.run(ctx)
        # policy.extends picks the profile unless the caller overrode it.
        profile = request.profile or "default"
        if profile == "default" and ctx.config.policy.extends:
            extends = ctx.config.policy.extends
            profile = extends if extends in PROFILES else profile
        if profile not in PROFILES:
            raise ScanRequestError(f"Unknown profile: {profile} ({'|'.join(PROFILES)})")
        report = apply_profile(report, profile)
        report = apply_policy(report, ctx.config)
        if request.baseline is not None:
            report = apply_baseline(report, request.baseline)
        if request.save_baseline is not None:
            save_baseline(report, request.save_baseline)
        if request.files:
            wanted = {Path(f).as_posix().removeprefix("./") for f in request.files}
            report = dataclasses.replace(
                report,
                results=[
                    r for r in report.results if r.file is None or r.file.as_posix() in wanted
                ],
            )
        if request.new_only:
            report = dataclasses.replace(report, results=report.new_results)
        cache = scan_cache(ctx)
        cache.prune({f.as_posix() for f in ctx.files})
        cache.save()
        return ScanOutcome(
            report=report,
            ctx=ctx,
            runner=runner,
            selected=len(selected),
            plugin_errors=plugin_errors,
        )


def _plugin_identity_allowed(check: Check, allow: tuple[str, ...]) -> bool:
    """Post-load filter: allow entries may be check ids, dists, or EPs."""
    if check.id in allow:
        return True
    identity = getattr(check, "__fd_identity__", None)
    if identity is None:
        return False
    return identity.distribution in allow or (
        identity.entry_point in allow if identity.entry_point else False
    )
