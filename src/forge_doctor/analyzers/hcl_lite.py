"""Deterministic Terraform/HCL-lite and CloudFormation extraction.

No ``hcl2`` dependency: resource blocks are found with a brace-aware scan and
flat ``key = value`` attributes are captured as Python scalars. CloudFormation
JSON parses natively; YAML templates are mined with a resource-block regex.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class IaCResource:
    """A Terraform resource block or CloudFormation resource."""

    source: str  # "terraform" | "cloudformation"
    type: str  # aws_glue_job | AWS::Glue::Job
    name: str
    attrs: dict[str, Any] = field(default_factory=dict)
    file: str = ""
    line: int = 0


_RESOURCE_HEAD = re.compile(r'resource\s+"([a-z0-9_]+)"\s+"([a-zA-Z0-9_.-]+)"\s*\{', re.IGNORECASE)
_ATTR_RE = re.compile(r"^\s*([a-zA-Z_][\w.-]*)\s*=\s*(.+?)\s*(?:#.*|//.*)?$")
_CFN_RESOURCE_RE = re.compile(
    r"^\s{0,10}([A-Za-z0-9]+):\s*\n(?:\s+[^\n]*\n)*?\s+Type:\s*[\"']?(AWS::[A-Za-z0-9:]+|Custom::[\w-]+)[\"']?\s*$",
    re.MULTILINE,
)


def _hcl_scalar(raw: str) -> Any:
    raw = raw.strip().rstrip(",")
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1]
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [_hcl_scalar(p) for p in inner.split(",") if p.strip()]
    lowered = raw.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none"}:
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw  # unquoted identifier / expression (var.x, module.y)


def _brace_block(text: str, start: int) -> tuple[str, int]:
    """Body inside the brace at ``start`` (position of ``{``) + end pos."""
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i], i
    return text[start:], len(text)


def _flat_attrs(body: str, base_line: int) -> dict[str, Any]:
    """Top-level ``key = value`` pairs; nested blocks are skipped."""
    attrs: dict[str, Any] = {}
    depth = 0
    for line in body.splitlines():
        stripped = line.strip()
        if depth == 0:
            match = _ATTR_RE.match(line)
            if match and not stripped.startswith(("#", "//", "/*")):
                attrs[match.group(1)] = _hcl_scalar(match.group(2))
        depth += stripped.count("{") - stripped.count("}")
        depth = max(depth, 0)
    return attrs


def parse_terraform(text: str, file: str = "") -> list[IaCResource]:
    """All ``resource "type" "name" {...}`` blocks with flat attrs."""
    resources: list[IaCResource] = []
    for match in _RESOURCE_HEAD.finditer(text):
        body, _end = _brace_block(text, match.end() - 1)
        line = text.count("\n", 0, match.start()) + 1
        resources.append(
            IaCResource(
                source="terraform",
                type=match.group(1),
                name=match.group(2),
                attrs=_flat_attrs(body, line),
                file=file,
                line=line,
            )
        )
    return resources


def parse_cloudformation(text: str, file: str = "") -> list[IaCResource]:
    """CFN JSON parsed natively; YAML mined with a resource-block regex."""
    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return []
        resources = []
        for name, body in (payload.get("Resources") or {}).items():
            if isinstance(body, dict):
                resources.append(
                    IaCResource(
                        source="cloudformation",
                        type=str(body.get("Type", "")),
                        name=str(name),
                        attrs=body.get("Properties") or {},
                        file=file,
                    )
                )
        return resources

    resources = []
    for match in _CFN_RESOURCE_RE.finditer(text):
        name, rtype = match.group(1), match.group(2)
        # Capture the Properties block following the Type line.
        tail = text[match.end() :]
        props: dict[str, Any] = {}
        prop_match = re.search(r"^\s+Properties:\s*\n((?:\s{6,}.*\n?)*)", tail, re.MULTILINE)
        if prop_match:
            for prop_line in prop_match.group(1).splitlines():
                kv = re.match(r"^\s+([A-Za-z0-9]+):\s*(.+?)\s*$", prop_line)
                if kv:
                    props[kv.group(1)] = kv.group(2).strip("\"'")
        resources.append(
            IaCResource(
                source="cloudformation",
                type=rtype,
                name=name,
                attrs=props,
                file=file,
                line=text.count("\n", 0, match.start()) + 1,
            )
        )
    return resources


def project_iac(ctx_files: Iterable[Path], root: Path) -> list[IaCResource]:
    """All IaC resources under a project root (tf + cfn)."""

    resources: list[IaCResource] = []
    for relative in sorted(ctx_files):
        suffix = relative.suffix.lower()
        target = root / relative
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        posix = relative.as_posix()
        if suffix == ".tf":
            resources.extend(parse_terraform(text, posix))
        elif suffix in {".json", ".yml", ".yaml"} and ("AWS::" in text or '"Resources"' in text):
            resources.extend(parse_cloudformation(text, posix))
    return resources
