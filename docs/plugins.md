# Plugins

Forge Doctor discovers external checks through the `forge_doctor.checks`
entry-point group. Any installed distribution can contribute checks — they
flow through the same registry, runner, filters and renderers as built-ins.

## Contract

An entry point resolves to a `Check` — an object with:

```python
id: str  # stable public identifier, e.g. "DATABRICKS001"
title: str  # short human title
category: str  # groups output; enables --check <category>
why: str  # optional: why the rule exists   → forge-doctor explain
when_ok: str  # optional: when a hit is fine    → forge-doctor explain
fix: str  # optional: how to resolve        → forge-doctor explain


def run(self, ctx: ProjectContext) -> list[CheckResult]: ...
```

Subclassing `forge_doctor.plugins.protocol.CheckBase` gives you the
`result()` helper and defaults. Checks **return** `CheckResult`s — they
never print, never raise for user-facing outcomes, and never import or
execute code from the analyzed project.

`ProjectContext` provides: `root`, `files` (relative paths, traversal
already pruned), `dirs`, `pyproject` (parsed TOML or `None`), `pyproject_path`,
`env` (allowlisted env vars only), `git`, `python_version`, `read_text()`,
`has_file()`, `has_dir()`.

## Rules for plugin authors

- **Stable ids** — pick a unique prefix (`DBX001`, `GLUE010`...). Never
  derive ids from message text. Duplicate ids are rejected at registration.
- **Honest severity** — weak evidence is `INFO`, never `ERROR`.
- **No secrets** — report names/paths, never values (invariant V3).
- **No side effects** — no network calls, no writing into the target project.
- **Deterministic** — same project + same versions → same results.

## Minimal example

See `examples/plugin/`. The whole package is:

```toml
# pyproject.toml
[project.entry-points."forge_doctor.checks"]
example = "forge_doctor_example:ExampleTodoCheck"
```

```python
class ExampleTodoCheck(CheckBase):
    id = "EXAMPLE001"
    title = "TODO comments"
    category = "example"

    def run(self, ctx): ...  # return list[CheckResult]
```

## Installing during development

```bash
pipx inject forge-doctor examples/plugin
forge-doctor plugins list       # lists 'example' with identity + API version
forge-doctor plugins validate   # per-plugin contract check
forge-doctor plugins doctor     # load health diagnostics
forge-doctor scan .             # EXAMPLE001 runs alongside built-ins
```

A broken plugin degrades to a warning on stderr — a scan never crashes
because an entry point fails to import or instantiate.

## Plugin SDK v2 — identity and descriptor

Every loaded check is stamped with `__fd_identity__` — a `PluginIdentity`
record of `distribution`, `version`, `api_version`, and `entry_point`. Scan
output attributes each plugin finding to its distribution via `source`.

A plugin distribution may expose a `PluginDescriptor` (via a
`forge_doctor.plugin` entry point or module attribute) declaring:

```python
PluginDescriptor(
    name="forge-doctor-example",
    version="1.2.0",
    api_version="2",
    requires_forge_doctor=">=0.5",
    capabilities=("checks",),
)
```

`plugins validate` reports incompatible `api_version`/`requires_forge_doctor`
declarations before they reach a scan.

## Trust boundary & allowlisting

Plugins execute in-process with full CLI privileges, so the trust decision
happens **before any plugin code runs**. `[tool.forge-doctor.plugins]`
in the scanned project's pyproject carries three controls:

```toml
[tool.forge-doctor.plugins]
# Pre-load gate: only these distributions / entry points ever load.
# Untrusted plugins appear in `plugins list` as "untrusted" and never
# execute a line of code.
trusted = ["forge-doctor-databricks"]

# Legacy list: identity entries (distribution/entry-point) also gate
# pre-load; check-id entries filter post-load.
allow = ["forge-doctor-databricks", "DBX001"]

# Post-load per-check filters - a check id is only knowable once the
# plugin is loaded, so these can never be a load barrier.
[tool.forge-doctor.plugins.checks]
enabled = ["DBX001"]     # allowlist of check ids (optional)
disabled = ["DBX009"]    # denylist of check ids (optional)
```

`--no-plugins` or `FORGE_DOCTOR_NO_PLUGINS=1` disables loading entirely —
the env var is a kill-switch that even the config cannot re-enable.
