"""Control-M project model - workflows-as-code evidence, static and offline.

Follows V11: one model per scan; every CTM### check and the ``controlm``
command group query it - no per-check parsing. Sources:

- ``*.json`` Automation API definitions (folders, jobs, events, calendars,
  site standards, defaults) - parsed only when the file carries a
  Control-M ``"Type"`` marker, so arbitrary project JSON is untouched.
- ``ctm`` CLI invocations and ``automation-api`` URLs in scripts, CI
  workflows and code.
- Deploy descriptors (``*deploy*.json``).

The model never runs ``ctm`` and never calls the Automation API (V9).
Credential-shaped properties are recorded by NAME only - values are never
captured anywhere in the model (V3).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from forge_doctor_data.core.context import ProjectContext

_CACHE_ATTR = "_forge_doctor_data_controlm_model"

# A JSON file only gets parsed when it carries a Control-M-ish "Type" value.
_DEF_MARKER_RE = re.compile(
    r'"(?:Job|Folder|SmartFolder|SubFolder|SimpleFolder|SiteStandard|Defaults)"'
)
_CLI_RE = re.compile(
    r"\bctm\s+(?:build|deploy|run|config|provision|session|reporting|package|environment)",
    re.IGNORECASE,
)
_API_RE = re.compile(r"automation-api", re.IGNORECASE)
_CRED_RE = re.compile(r"password|secret|token|credential|api[_-]?key", re.IGNORECASE)
# Keys that hold calendar names anywhere inside a job/folder object.
_CAL_KEY_RE = re.compile(
    r"^(?:rulebasedcalendars?|rbc|conf?cal|calendar|holidaycalendar|excludecalendars?)$",
    re.IGNORECASE,
)
# Keys whose presence means "this object is scheduled".
_SCHED_KEY_RE = re.compile(
    r"^(?:when|weekdays|months|monthdays|daysinterval|fromtime|totime|cyclic|"
    r"schedule|scheduling|activefrom|activeuntil|rulebasedcalendars?|rbc|calendar)$",
    re.IGNORECASE,
)
# Fields that may carry a consumed event/condition name.
_WAIT_FIELDS = {
    "eventstowaitfor",
    "waitforevents",
    "incondition",
    "inconditions",
    "prereqconditions",
    "preconditions",
}
# Fields that may carry a produced event/condition name (searched at any
# depth so `OnDo` -> `Do` -> `AddEvents` blocks count as producers).
_ADD_FIELDS = {"eventstoadd", "addevents", "outcondition", "outconditions"}
# Keys inside an event/condition object that hold the name itself.
_EVENT_NAME_KEYS = {"event", "name", "condition", "prereqname", "conditionname", "calname"}
# Scalar keys that look like names but are metadata, never event names.
_EVENT_SKIP_KEYS = {"orderdate", "odate", "sign", "andor", "type", "code", "status"}

_SCAN_SUFFIXES = {
    ".sh",
    ".bash",
    ".ps1",
    ".bat",
    ".cmd",
    ".yml",
    ".yaml",
    ".py",
    ".toml",
    ".cfg",
    ".ini",
    ".json",
}

_CONTAINER_TYPES = {"folder", "smartfolder", "subfolder", "simplefolder"}
_CALENDAR_TYPES = {
    "rbc",
    "rulebasedcalendar",
    "simplecalendar",
    "calendar",
    "weeklycalendar",
    "monthlycalendar",
    "periodicalcalendar",
}


def _objects_pack() -> dict[str, Any]:
    from forge_doctor_data.core.knowledge import load_pack

    return load_pack("controlm", "objects")


def pack_list(key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """String list from the objects pack, falling back to ``default``."""
    value = _objects_pack().get(key)
    if isinstance(value, list):
        return tuple(str(v) for v in value)
    return default


def required_metadata() -> tuple[str, ...]:
    return pack_list("required_metadata", ("Application", "SubApplication", "Owner", "RunAs"))


@dataclass(frozen=True)
class CtmJob:
    """One Control-M job flattened to its effective (defaults-merged) props."""

    name: str
    folder: str
    job_type: str
    file: Path
    line: int
    application: str
    subapplication: str
    owner: str
    runas: str
    host: str
    hostgroup: str
    wait_events: tuple[str, ...]
    add_events: tuple[str, ...]
    calendars: tuple[str, ...]
    has_schedule: bool
    cyclic: bool


@dataclass(frozen=True)
class CtmFolder:
    name: str
    file: Path
    line: int


@dataclass
class ControlMModel:
    """Everything the project evidences about Control-M, in sorted order."""

    files: list[Path] = field(default_factory=list)
    folders: list[CtmFolder] = field(default_factory=list)
    jobs: list[CtmJob] = field(default_factory=list)
    calendars: dict[str, tuple[Path, int]] = field(default_factory=dict)
    site_standards: list[tuple[str, Path, int]] = field(default_factory=list)
    deploy_descriptors: list[Path] = field(default_factory=list)
    cli_refs: list[tuple[Path, int]] = field(default_factory=list)
    api_refs: list[tuple[Path, int]] = field(default_factory=list)
    # Credential-shaped property NAME -> first location; values never stored.
    credential_props: list[tuple[str, Path, int]] = field(default_factory=list)

    @property
    def has_controlm(self) -> bool:
        return bool(
            self.jobs or self.folders or self.cli_refs or self.api_refs or self.site_standards
        )

    @property
    def events_produced(self) -> dict[str, tuple[Path, int]]:
        found: dict[str, tuple[Path, int]] = {}
        for job in self.jobs:
            for name in job.add_events:
                found.setdefault(name, (job.file, job.line))
        return found

    @property
    def events_consumed(self) -> dict[str, tuple[Path, int]]:
        found: dict[str, tuple[Path, int]] = {}
        for job in self.jobs:
            for name in job.wait_events:
                found.setdefault(name, (job.file, job.line))
        return found

    @property
    def calendar_refs(self) -> dict[str, tuple[Path, int]]:
        found: dict[str, tuple[Path, int]] = {}
        for job in self.jobs:
            for name in job.calendars:
                found.setdefault(name, (job.file, job.line))
        return found

    @property
    def job_names(self) -> dict[str, list[CtmJob]]:
        """job name -> definitions (len > 1 means duplicated)."""
        found: dict[str, list[CtmJob]] = {}
        for job in self.jobs:
            found.setdefault(job.name, []).append(job)
        return found


def _line_of(text: str, name: str) -> int:
    idx = text.find(f'"{name}"')
    return text.count("\n", 0, idx) + 1 if idx >= 0 else 0


def _prop(props: dict[str, Any], *names: str) -> str:
    """First non-empty scalar prop among ``names``, case-insensitive."""
    lowered = {n.lower() for n in names}
    for key, value in props.items():
        if key.lower() in lowered and isinstance(value, str | int | float):
            return str(value)
    return ""


def _names_in(value: Any) -> list[str]:
    """Every event/calendar name inside a loosely-shaped value."""
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [n for item in value for n in _names_in(item)]
    if isinstance(value, dict):
        out: list[str] = []
        for key, inner in value.items():
            lk = key.lower()
            if isinstance(inner, str) and lk in _EVENT_NAME_KEYS:
                out.append(inner)
            elif isinstance(inner, list | dict):
                out.extend(_names_in(inner))
            elif isinstance(inner, str) and lk not in _EVENT_SKIP_KEYS and len(value) == 1:
                out.append(inner)
        return out
    return []


def _dict_keyed_names(value: Any) -> list[str]:
    """Condition maps keyed BY name: ``{"JOB_OK": {"ODATE": "..."}}``."""
    if not isinstance(value, dict):
        return []
    out = []
    for key, inner in value.items():
        if key.lower() in _EVENT_SKIP_KEYS | {"events"}:
            continue
        if isinstance(inner, dict) or (
            isinstance(inner, str) and key.lower() not in _EVENT_NAME_KEYS
        ):
            out.append(key)
    return out


def _deep_events(obj: Any, wanted: set[str]) -> list[str]:
    """Find fields in ``wanted`` at any depth; collect their event names."""
    hits: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key.lower() in wanted:
                hits.extend(_names_in(value))
                hits.extend(_dict_keyed_names(value))
            else:
                hits.extend(_deep_events(value, wanted))
    elif isinstance(obj, list):
        for item in obj:
            hits.extend(_deep_events(item, wanted))
    return hits


def _cal_refs(obj: Any) -> list[str]:
    hits: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if _CAL_KEY_RE.match(key):
                hits.extend(_names_in(value))
                hits.extend(_dict_keyed_names(value))
            else:
                hits.extend(_cal_refs(value))
    elif isinstance(obj, list):
        for item in obj:
            hits.extend(_cal_refs(item))
    return hits


def _has_sched_key(obj: dict[str, Any]) -> bool:
    return any(_SCHED_KEY_RE.match(key) for key in obj)


def _merge_defaults(base: dict[str, Any], layer: Any) -> dict[str, Any]:
    """``Defaults`` blocks: flat scalars plus a nested ``Job`` mapping."""
    merged = dict(base)
    if not isinstance(layer, dict):
        return merged
    for key, value in layer.items():
        if key.lower() == "job" and isinstance(value, dict):
            merged.update(value)
        elif not isinstance(value, dict | list):
            merged[key] = value
    return merged


def _is_variable_ref(value: str) -> bool:
    v = value.strip()
    return (
        not v or v.startswith("%%") or v.startswith("${") or v.startswith("$(") or set(v) <= {"*"}
    )


def _credential_names(obj: Any, out: list[str]) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if _CRED_RE.search(key):
                if isinstance(value, str | int | float) and not _is_variable_ref(str(value)):
                    out.append(key)
            else:
                _credential_names(value, out)
    elif isinstance(obj, list):
        for item in obj:
            _credential_names(item, out)


def _walk_defs(
    obj: dict[str, Any],
    file: Path,
    text: str,
    model: ControlMModel,
    folder: str = "",
    defaults: dict[str, Any] | None = None,
    inherited_sched: bool = False,
) -> None:
    # Defaults apply regardless of declaration order - collect them first.
    merged_defaults = dict(defaults) if defaults else {}
    for key, value in obj.items():
        if key.lower() == "defaults" and isinstance(value, dict):
            merged_defaults = _merge_defaults(merged_defaults, value)
    defaults = merged_defaults
    for key in sorted(obj):
        value = obj[key]
        if not isinstance(value, dict) or key.lower() == "defaults":
            continue
        raw_type = value.get("Type")
        kind = raw_type.lower() if isinstance(raw_type, str) else ""
        if kind in _CONTAINER_TYPES:
            model.folders.append(CtmFolder(key, file, _line_of(text, key)))
            _walk_defs(
                value,
                file,
                text,
                model,
                folder=key,
                defaults=defaults,
                inherited_sched=inherited_sched or _has_sched_key(value),
            )
        elif kind == "job" or kind.startswith("job:"):
            # Job props override defaults (defaults first, then the job).
            effective = {**defaults, **value}
            wait = tuple(sorted(set(_deep_events(value, _WAIT_FIELDS))))
            add = tuple(sorted(set(_deep_events(value, _ADD_FIELDS))))
            cals = tuple(sorted(set(_cal_refs(value))))
            model.jobs.append(
                CtmJob(
                    name=key,
                    folder=folder,
                    job_type=str(raw_type),
                    file=file,
                    line=_line_of(text, key),
                    application=_prop(effective, "application"),
                    subapplication=_prop(effective, "subapplication"),
                    owner=_prop(effective, "owner"),
                    runas=_prop(effective, "runas", "run_as", "runasuser"),
                    host=_prop(effective, "host", "nodeid", "agent"),
                    hostgroup=_prop(effective, "hostgroup", "hostgroupname"),
                    wait_events=wait,
                    add_events=add,
                    calendars=cals,
                    has_schedule=inherited_sched or _has_sched_key(value),
                    cyclic="cyclic" in {k.lower() for k in value},
                )
            )
        elif kind in _CALENDAR_TYPES:
            model.calendars.setdefault(key, (file, _line_of(text, key)))
        elif kind == "sitestandard":
            model.site_standards.append((key, file, _line_of(text, key)))
        else:
            _walk_defs(
                value,
                file,
                text,
                model,
                folder=folder,
                defaults=defaults,
                inherited_sched=inherited_sched or _has_sched_key(value),
            )


def _scan_refs(text: str, file: Path, model: ControlMModel) -> None:
    for line_no, raw in enumerate(text.splitlines(), 1):
        if _CLI_RE.search(raw):
            model.cli_refs.append((file, line_no))
        if _API_RE.search(raw):
            model.api_refs.append((file, line_no))


def controlm_model(ctx: ProjectContext) -> ControlMModel:
    """Build (once, memoized on ctx) the project's Control-M model."""
    cached = getattr(ctx, _CACHE_ATTR, None)
    if cached is not None:
        return cast(ControlMModel, cached)

    model = ControlMModel()
    for relative in sorted(ctx.files, key=lambda p: p.as_posix()):
        text = ctx.read_text(relative)
        if text is None:
            continue
        suffix = relative.suffix.lower()
        if suffix == ".json":
            if "deploy" in relative.name.lower():
                model.deploy_descriptors.append(relative)
            if _DEF_MARKER_RE.search(text):
                try:
                    data: Any = json.loads(text)
                except json.JSONDecodeError:
                    data = None
                if isinstance(data, dict):
                    creds: list[str] = []
                    _credential_names(data, creds)
                    for name in sorted(set(creds)):
                        model.credential_props.append((name, relative, _line_of(text, name)))
                    _walk_defs(data, relative, text, model)
                    if model.jobs or model.folders or model.site_standards:
                        model.files.append(relative)
        if suffix in _SCAN_SUFFIXES:
            _scan_refs(text, relative, model)

    model.jobs.sort(key=lambda j: (j.file.as_posix(), j.line, j.name))
    model.folders.sort(key=lambda f: (f.file.as_posix(), f.line, f.name))
    model.files.sort(key=lambda p: p.as_posix())
    model.credential_props.sort(key=lambda c: (c[1].as_posix(), c[2], c[0]))
    model.cli_refs.sort(key=lambda r: (r[0].as_posix(), r[1]))
    model.api_refs.sort(key=lambda r: (r[0].as_posix(), r[1]))
    model.deploy_descriptors.sort(key=lambda p: p.as_posix())
    setattr(ctx, _CACHE_ATTR, model)
    return model
