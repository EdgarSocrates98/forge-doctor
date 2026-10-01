"""Terraform semantic model - extends hcl_lite with block types, references.

Dependency-free: every ``.tf`` file is scanned with the same brace-aware
machinery as ``hcl_lite``, covering ``terraform``/``provider``/``module``/
``resource``/``data``/``variable``/``output``/``locals``/``moved``/``import``/
``removed``/``check`` blocks plus a reference graph between addresses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from forge_doctor.analyzers.hcl_lite import _brace_block, _flat_attrs

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext

_CACHE_ATTR = "_forge_doctor_terraform_model"

_BLOCK_HEAD = re.compile(
    r"^\s*(terraform|provider|resource|data|module|variable|output|locals|"
    r"moved|import|check|removed|precondition|postcondition)"
    r"((?:\s+\"[^\"]*\"){0,2})\s*\{",
    re.MULTILINE,
)
_LABEL_RE = re.compile(r'"([^"]*)"')
_NESTED_HEAD = re.compile(r'(\w+)\s*(?:"([^"]*)")?\s*\{')
_REF_RE = re.compile(r"(?<![\w/.-])([a-z_][\w]*(?:\.[a-zA-Z0-9_-]+)+)")
_REF_PREFIXES = {"var", "local", "module", "data", "each", "count", "self", "path", "terraform"}
_CONSTRAINT_RE = re.compile(r"^(>=|~>|>|<=|<|=|!=)?\s*(.+)$")


@dataclass(frozen=True)
class TfBlock:
    """A top-level HCL block."""

    # terraform|provider|resource|data|module|variable|output|locals|
    # moved|import|check|removed
    kind: str
    labels: tuple[str, ...]
    address: str  # "aws_glue_job.orders", "module.vpc", "terraform", ...
    attrs: dict[str, Any]
    body: str
    file: Path
    line: int


@dataclass
class TerraformProjectModel:
    """Semantic summary of all ``*.tf`` sources in a project."""

    blocks: list[TfBlock] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)  # (from_addr, to_addr)
    tf_files: int = 0

    def by_kind(self, kind: str) -> list[TfBlock]:
        return [b for b in self.blocks if b.kind == kind]

    @property
    def has_terraform(self) -> bool:
        return bool(self.blocks)

    @property
    def resources(self) -> list[TfBlock]:
        return self.by_kind("resource")

    @property
    def modules(self) -> list[TfBlock]:
        return self.by_kind("module")

    @property
    def providers(self) -> list[TfBlock]:
        return self.by_kind("provider")

    @property
    def data_sources(self) -> list[TfBlock]:
        return self.by_kind("data")

    @property
    def addresses(self) -> set[str]:
        return {b.address for b in self.blocks}

    @property
    def required_version(self) -> str:
        for b in self.by_kind("terraform"):
            value = b.attrs.get("required_version")
            if isinstance(value, str) and value:
                return value
        return ""

    @property
    def required_providers(self) -> dict[str, TfBlock]:
        """name -> the nested provider-requirement pseudo-block."""
        out: dict[str, TfBlock] = {}
        for b in self.by_kind("terraform"):
            for name, block in b.attrs.get("_required_providers", {}).items():
                out.setdefault(name, block)
        return out

    @property
    def backend(self) -> str:
        for b in self.by_kind("terraform"):
            backend = b.attrs.get("_backend", "")
            if backend:
                return str(backend)
        return ""


def _top_blocks(text: str, file: Path) -> list[TfBlock]:
    blocks: list[TfBlock] = []
    for m in _BLOCK_HEAD.finditer(text):
        kind = m.group(1)
        labels = tuple(_LABEL_RE.findall(m.group(2)))
        body, _ = _brace_block(text, m.end() - 1)
        line = text.count("\n", 0, m.start()) + 1
        attrs = _flat_attrs(body, line)
        if kind == "terraform":
            _terraform_internals(body, file, line, attrs)
        address = _address(kind, labels)
        blocks.append(
            TfBlock(
                kind=kind,
                labels=labels,
                address=address,
                attrs=attrs,
                body=body,
                file=file,
                line=line,
            )
        )
    return blocks


def _address(kind: str, labels: tuple[str, ...]) -> str:
    if kind in {"resource", "data"} and len(labels) == 2:
        prefix = "data." if kind == "data" else ""
        return f"{prefix}{labels[0]}.{labels[1]}"
    if labels:
        return f"{kind}.{labels[0]}"
    return kind


def _terraform_internals(body: str, file: Path, line: int, attrs: dict[str, Any]) -> None:
    """Mine ``backend "x"`` and ``required_providers`` nested blocks."""
    for nm in _NESTED_HEAD.finditer(body):
        nested_kind, label = nm.group(1), nm.group(2)
        nested_body, _ = _brace_block(body, nm.end() - 1)
        if nested_kind == "backend" and label:
            attrs["_backend"] = label
        elif nested_kind == "required_providers":
            reqs: dict[str, TfBlock] = {}
            for name, raw in _flat_attrs(nested_body, line).items():
                value: dict[str, Any] = {}
                if isinstance(raw, str):
                    value["version"] = raw
                reqs[name] = TfBlock(
                    kind="required_provider",
                    labels=(name,),
                    address=name,
                    attrs=value if value else {"version": ""},
                    body="",
                    file=file,
                    line=line,
                )
            # required_providers entries may also be nested blocks: aws = {..}
            for m2 in re.finditer(r"(\w+)\s*=\s*\{", nested_body):
                nb, _ = _brace_block(nested_body, m2.end() - 1)
                reqs[m2.group(1)] = TfBlock(
                    kind="required_provider",
                    labels=(m2.group(1),),
                    address=m2.group(1),
                    attrs=_flat_attrs(nb, line),
                    body=nb,
                    file=file,
                    line=line,
                )
            attrs["_required_providers"] = reqs


def _references(text: str) -> set[str]:
    """``aws_iam_role.glue``, ``module.vpc``, ``var.env`` style references."""
    refs: set[str] = set()
    for m in _REF_RE.finditer(text):
        chain = m.group(1).split(".")
        head = chain[0]
        if head == "terraform":
            if len(chain) > 1 and chain[1] == "workspace":
                refs.add("terraform.workspace")
        elif head in {"data", "module"} and len(chain) >= 2:
            refs.add(f"{head}.{chain[1]}")
        elif head in _REF_PREFIXES:
            refs.add(f"{head}.{chain[1]}" if len(chain) > 1 else head)
        else:
            # plausible resource-type reference: keep first two segments.
            refs.add(".".join(chain[:2]))
    return refs


def terraform_model(ctx: ProjectContext) -> TerraformProjectModel:
    """Build (once, memoized on ctx) the Terraform semantic model."""
    cached = getattr(ctx, _CACHE_ATTR, None)
    if cached is not None:
        return cast(TerraformProjectModel, cached)

    blocks: list[TfBlock] = []
    tf_files = 0
    for relative in sorted(ctx.files):
        if relative.suffix.lower() != ".tf":
            continue
        text = ctx.read_text(relative)
        if text is None:
            continue
        tf_files += 1
        blocks.extend(_top_blocks(text, relative))

    known = {b.address for b in blocks}
    edges: set[tuple[str, str]] = set()
    for b in blocks:
        src = b.address if b.kind != "terraform" else f"terraform@{b.file.as_posix()}"
        for ref in _references(b.body):
            if ref != src and (ref in known or ref.split(".")[0] in _REF_PREFIXES):
                edges.add((src, ref))

    model = TerraformProjectModel(blocks=blocks, edges=sorted(edges), tf_files=tf_files)
    setattr(ctx, _CACHE_ATTR, model)
    return model
