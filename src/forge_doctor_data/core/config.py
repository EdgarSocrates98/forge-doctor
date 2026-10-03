"""Optional project configuration from ``[tool.forge-doctor-data]``."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RuleOverride:
    """Per-rule policy tuning: severity floor and/or kill switch."""

    severity: str | None = None
    enabled: bool = True


@dataclass(frozen=True)
class Suppression:
    """A governed exception: scoped, owned, expiring - never silent.

    ``path`` is a POSIX glob matched against the finding's project-relative
    file; ``line`` pins the suppression to one occurrence when needed.
    """

    rule: str
    path: str | None = None
    line: int | None = None
    reason: str = ""
    owner: str = ""
    expires: str | None = None  # ISO date; past date reactivates the rule
    # Governance: who signed off. Packs with ``require_approval`` flag
    # suppressions missing this.
    approved_by: str = ""


@dataclass(frozen=True)
class Policy:
    """Custom severity policy layered over a built-in profile."""

    extends: str | None = None
    rules: dict[str, RuleOverride] = field(default_factory=dict)


@dataclass(frozen=True)
class PluginRules:
    """Plugin trust model.

    ``trusted`` gates BEFORE any plugin code loads (distribution or
    entry-point names). ``checks_enabled``/``checks_disabled`` filter
    individual check ids post-load - a check id is only knowable once the
    plugin is loaded, so it can never be a load barrier.
    """

    trusted: tuple[str, ...] = ()
    checks_enabled: tuple[str, ...] = ()
    checks_disabled: tuple[str, ...] = ()
    # Legacy ``allow`` list: identity entries gate loading, check-id
    # entries filter post-load (kept for backward compatibility).
    allow: tuple[str, ...] = ()
    # ``strict`` = default-deny: only ``trusted`` plugins may load, and
    # identity entries in ``allow`` no longer grant load permission.
    mode: str = "open"


@dataclass(frozen=True)
class ForgeDoctorDataConfig:
    """User config merged with CLI options by the CLI layer."""

    exclude: tuple[str, ...] = ()
    ignore: tuple[str, ...] = ()
    plugins_allow: tuple[str, ...] = ()
    plugins: PluginRules = field(default_factory=PluginRules)
    policy: Policy = field(default_factory=Policy)
    policy_packs: tuple[str, ...] = ()
    suppressions: tuple[Suppression, ...] = ()
    history_retention: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_pyproject(cls, pyproject: dict[str, Any]) -> ForgeDoctorDataConfig:
        section = pyproject.get("tool", {}).get("forge-doctor-data", {})
        if not isinstance(section, dict):
            return cls()
        exclude = _str_list(section.get("exclude"))
        ignore = _str_list(section.get("ignore"))
        plugins_section = section.get("plugins", {})
        if not isinstance(plugins_section, dict):
            plugins_section = {}
        plugins_allow = _str_list(plugins_section.get("allow"))
        checks_section = plugins_section.get("checks", {})
        if not isinstance(checks_section, dict):
            checks_section = {}
        plugins = PluginRules(
            trusted=_str_list(plugins_section.get("trusted")),
            checks_enabled=_str_list(checks_section.get("enabled")),
            checks_disabled=_str_list(checks_section.get("disabled")),
            allow=plugins_allow,
            mode="strict" if plugins_section.get("mode") == "strict" else "open",
        )
        # Per-category ignores: [tool.forge-doctor-data.spark] ignore = [...]
        for value in section.values():
            if isinstance(value, dict):
                ignore += _str_list(value.get("ignore"))
        return cls(
            exclude=exclude,
            ignore=ignore,
            plugins_allow=plugins_allow,
            plugins=plugins,
            policy=_parse_policy(section.get("policy")),
            policy_packs=_str_list(section.get("policy_packs")),
            suppressions=_parse_suppressions(section.get("suppressions")),
            history_retention=_parse_history(section.get("history")),
        )


def _parse_history(value: Any) -> dict[str, int]:
    """``[tool.forge-doctor-data.history]`` retention knobs (spec 241 §40)."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, int] = {}
    for key in ("keep_days", "keep_samples", "compact_after"):
        raw = value.get(key)
        if raw is None or isinstance(raw, bool):
            continue
        try:
            out[key] = int(raw)
        except (TypeError, ValueError):
            continue
    return out


def _parse_policy(value: Any) -> Policy:
    if not isinstance(value, dict):
        return Policy()
    extends = value.get("extends")
    rules: dict[str, RuleOverride] = {}
    raw_rules = value.get("rules")
    if isinstance(raw_rules, dict):
        for rule_id, spec in raw_rules.items():
            if not isinstance(spec, dict):
                continue
            severity = spec.get("severity")
            rules[str(rule_id).upper()] = RuleOverride(
                severity=str(severity).lower() if isinstance(severity, str) else None,
                enabled=spec.get("enabled") is not False,
            )
    return Policy(
        extends=str(extends) if isinstance(extends, str) else None,
        rules=rules,
    )


def _parse_suppressions(value: Any) -> tuple[Suppression, ...]:
    if not isinstance(value, list):
        return ()
    suppressions: list[Suppression] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        rule = item.get("rule")
        if not isinstance(rule, str) or not rule.strip():
            continue
        line = item.get("line")
        raw_expires = item.get("expires")
        if isinstance(raw_expires, str):
            expires: str | None = raw_expires
        elif raw_expires is not None and hasattr(raw_expires, "isoformat"):
            # TOML parses bare dates (expires = 2026-12-31) to datetime.date.
            expires = raw_expires.isoformat()
        else:
            expires = None
        suppressions.append(
            Suppression(
                rule=rule.strip().upper(),
                path=item.get("path") if isinstance(item.get("path"), str) else None,
                line=line if isinstance(line, int) else None,
                reason=str(item.get("reason") or ""),
                owner=str(item.get("owner") or ""),
                expires=expires,
                approved_by=str(item.get("approved_by") or ""),
            )
        )
    return tuple(suppressions)


def _str_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


# Backwards-compatible alias; removed in 1.0.
DataDoctorConfig = ForgeDoctorDataConfig
