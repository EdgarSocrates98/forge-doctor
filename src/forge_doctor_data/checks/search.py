"""Search-platform checks (SRCH###) over the SearchPlatformModel.

Vendor-agnostic rules per the spec's open-question decision: one shared
``SRCH`` prefix, vendor named in the message. Evidence planes: config
(templates/policies/pipelines JSON, Terraform domains) and
observed_metadata (cluster exports).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from forge_doctor_data.analyzers.search_model import search_model
from forge_doctor_data.core.models import EvidenceKind, Severity
from forge_doctor_data.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor_data.core.context import ProjectContext
    from forge_doctor_data.core.models import CheckResult


class _SearchCheck(CheckBase):
    category = "search"


_PROD_PATTERN = re.compile(r"prod|production", re.IGNORECASE)


class SearchSurface(_SearchCheck):
    """SRCH000: anchor census of the search surface."""

    id = "SRCH000"
    title = "Search surface"
    why = "Anchor: sizes the search estate feeding the SRCH checks."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the counts to size the surface."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = search_model(ctx)
        if not model.has_evidence:
            return [self.result(Severity.PASS, "no search-platform evidence detected")]
        vendors = sorted(
            {i.vendor for i in model.indices}
            | {p.vendor for p in model.policies}
            | {d.vendor for d in model.domains}
        )
        return [
            self.result(
                Severity.INFO,
                f"{len(model.indices)} indices/templates, {len(model.policies)} "
                f"lifecycle policies, {len(model.pipelines)} pipelines, "
                f"{len(model.domains)} terraform domains "
                f"({', '.join(vendors)})",
            )
        ]


class ProdIndexNoReplicas(_SearchCheck):
    """SRCH001: prod-looking index template without replica shards."""

    id = "SRCH001"
    title = "Prod index template without replicas"
    why = (
        "A production index pattern with zero replicas (or unset, on a "
        "single-primary layout) loses the shard copies on any node "
        "failure — no failover."
    )
    when_ok = "Prod-pattern templates declare number_of_replicas >= 1."
    fix = "Set index.number_of_replicas >= 1 for prod templates."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        out: list[CheckResult] = []
        for idx in search_model(ctx).indices:
            surface = " ".join((*idx.index_patterns, idx.name))
            if not _PROD_PATTERN.search(surface):
                continue
            if idx.replicas and idx.replicas != "0":
                continue
            out.append(
                self.result(
                    Severity.WARNING,
                    f"{idx.vendor} template '{idx.name}' targets prod patterns "
                    f"({surface.strip()}) with replicas={idx.replicas or 'unset'}",
                    file=idx.file,
                    evidence=f"number_of_replicas={idx.replicas or 'missing'}",
                    evidence_kind=EvidenceKind.CONFIG,
                )
            )
        return out


class WildcardNoLifecycle(_SearchCheck):
    """SRCH002: wildcard/logs-* index pattern with no ISM/ILM coverage."""

    id = "SRCH002"
    title = "Wildcard index pattern without lifecycle policy"
    why = (
        "Time-series index patterns grow forever without an ISM/ILM "
        "policy — unbounded shard count, no rollover, no retention."
    )
    when_ok = "Wildcard/log patterns are covered by an ISM or ILM policy."
    fix = "Attach an ISM/ILM policy with rollover + delete to the pattern."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = search_model(ctx)
        if not model.indices:
            return []
        covered = {pat.rstrip("*").lower() for p in model.policies for pat in p.index_patterns}
        out: list[CheckResult] = []
        for idx in model.indices:
            wild = [
                p
                for p in idx.index_patterns
                if p.endswith("*") or p.lower().startswith(("logs-", "logs_", "metrics-"))
            ]
            if not wild:
                continue
            if any(p.rstrip("*").lower() in covered or p.lower() in covered for p in wild):
                continue
            if any("*" in pat for pat in covered):
                continue
            out.append(
                self.result(
                    Severity.WARNING,
                    f"{idx.vendor} template '{idx.name}' matches "
                    f"{', '.join(wild)} but no ISM/ILM policy covers it "
                    "(no rollover or retention)",
                    file=idx.file,
                    evidence="index pattern without lifecycle coverage",
                    evidence_kind=EvidenceKind.CONFIG,
                )
            )
        return out


class MappingExplosionRisk(_SearchCheck):
    """SRCH003: mapping with unbounded field-explosion risk."""

    id = "SRCH003"
    title = "Mapping field-explosion risk"
    why = (
        "Many nested objects under dynamic mapping multiply field count "
        "per document — mapping explosion degrades cluster state and "
        "search latency."
    )
    when_ok = (
        "Mappings either pin dynamic=false/strict, or keep open nested objects few and shallow."
    )
    fix = "Set dynamic=false/strict or enabled:false on verbose object keys."

    _OBJECT_LIMIT = 5

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        out: list[CheckResult] = []
        for idx in search_model(ctx).indices:
            risky = idx.object_keys
            if len(risky) <= self._OBJECT_LIMIT:
                continue
            if idx.dynamic in ("false", "strict"):
                continue
            out.append(
                self.result(
                    Severity.WARNING,
                    f"{idx.vendor} mapping '{idx.name}' exposes {len(risky)} "
                    "nested object fields with no enabled:false and no "
                    "dynamic=false/strict — mapping explosion risk",
                    file=idx.file,
                    evidence=f"{len(risky)} open objects, dynamic={idx.dynamic or 'default'}",
                    evidence_kind=EvidenceKind.CONFIG,
                )
            )
        return out


class DomainNoEncryption(_SearchCheck):
    """SRCH004: Terraform search domain without encryption/TLS."""

    id = "SRCH004"
    title = "Search domain without encryption at rest / TLS"
    why = (
        "A search domain without encrypt-at-rest and node-to-node TLS "
        "stores and moves queryable data in plaintext."
    )
    when_ok = "Domains enable encryption_at_rest and node-to-node encryption."
    fix = "Set encrypt_at_rest { enabled = true } + node_to_node_encryption."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        out: list[CheckResult] = []
        for d in search_model(ctx).domains:
            missing = []
            if d.encryption_at_rest != "true":
                missing.append("encrypt_at_rest")
            if d.node_to_node != "true":
                missing.append("node_to_node_encryption")
            if not missing:
                continue
            out.append(
                self.result(
                    Severity.WARNING,
                    f"{d.vendor} domain '{d.name}' lacks {' + '.join(missing)}",
                    file=d.file,
                    line=d.line,
                    evidence=f"{d.address}: {' + '.join(missing)} unset/false",
                    evidence_kind=EvidenceKind.CONFIG,
                )
            )
        return out


CHECKS: tuple[Check, ...] = (
    SearchSurface(),
    ProdIndexNoReplicas(),
    WildcardNoLifecycle(),
    MappingExplosionRisk(),
    DomainNoEncryption(),
)
