"""Redshift checks (RS###) over the vendor RedshiftProjectModel.

Grounded in ``redshift_model`` only — vendor-neutral findings belong to
WARE###. Findings derived from STL_*/SVV_* exports mark
``evidence_kind=OBSERVED_METADATA`` (spec 215 constraint); authored-SQL
evidence stays ``STATIC`` with lower confidence. No live AWS calls ever.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from forge_doctor.analyzers.redshift_model import (
    RedshiftProjectModel,
    redshift_model,
)
from forge_doctor.core.models import EvidenceKind, Severity
from forge_doctor.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.models import CheckResult


def _model(ctx: ProjectContext) -> RedshiftProjectModel:
    return redshift_model(ctx)


class _RedshiftCheck(CheckBase):
    category = "redshift"


def _falsy(value: str) -> bool:
    return value.lower() in {"", "0", "false", "off", "none", "never"}


def _tail(name: str) -> str:
    """Last dotted segment — `db.public.t` and `public.t` tail to `t`."""
    return name.strip('"`').rpartition(".")[2].lower() or name.lower()


def _name_matches(candidate: str, names: set[str]) -> bool:
    tail = _tail(candidate)
    for name in names:
        if (
            candidate == name
            or tail == _tail(name)
            or candidate.endswith(f".{name}")
            or name.endswith(f".{candidate}")
        ):
            return True
    return False


class RedshiftUsage(_RedshiftCheck):
    """RS000: anchor census of the Redshift surface."""

    id = "RS000"
    title = "Redshift surface"
    why = "Anchor: sizes the Redshift estate feeding the RS checks."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the counts to size the surface."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.has_evidence:
            return [self.result(Severity.PASS, "no Redshift evidence detected")]
        return [
            self.result(
                Severity.INFO,
                f"{len(model.compute)} compute, {len(model.namespaces)} namespaces, "
                f"{len(model.tables)} tables, {len(model.views)} views, "
                f"{len(model.objects)} vendor objects, {len(model.queries)} queries, "
                f"{len(model.observed)} observed rows",
            )
        ]


class BadDistributionStyle(_RedshiftCheck):
    """RS001: large observed table with DISTSTYLE EVEN/ALL + joins."""

    id = "RS001"
    title = "Large table with broadcast/even distribution under joins"
    why = (
        "DISTSTYLE ALL replicates the table to every node; EVEN round-"
        "robins it — both force data movement on every join of a large "
        "table. A KEY distribution on the join column avoids it."
    )
    when_ok = "Large joined tables use DISTSTYLE KEY on the join column."
    fix = "Set DISTSTYLE KEY + DISTKEY(<join column>), or enable ATO."

    _LARGE_ROWS = 10_000_000  # row-count proxy — static, documented
    _LARGE_SIZE = 1024  # SVV_TABLE_INFO size is in MB (1 GB proxy)

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        results = []
        for row in model.observed_tables():
            dist = row.get("diststyle", "dist_style").lower()
            if dist not in {"even", "all"}:
                continue
            name = row.get("table", "tablename", "table_name", "name")
            try:
                size = float(row.get("size", "size_mb") or 0)
                rows_n = float(row.get("tbl_rows", "row_count") or 0)
            except ValueError:
                size = rows_n = 0.0
            if size < self._LARGE_SIZE and rows_n < self._LARGE_ROWS:
                continue
            # Join evidence: authored SQL or observed query text.
            joined = [
                q
                for q in model.queries
                if "join" in q.text.lower() and _name_matches(name, set(q.tables_read))
            ]
            if not joined:
                # Tables_read may be empty for observed STL_QUERY rows -
                # fall back to a name substring check on the query text.
                joined = [
                    q
                    for q in model.queries
                    if "join" in q.text.lower() and _tail(name) in q.text.lower()
                ]
            if not joined:
                continue
            observed_join = any(q.source == "job" for q in joined)
            results.append(
                self.result(
                    Severity.WARNING,
                    f"table '{name}' ({size:,.0f} MB / {rows_n:,.0f} rows observed) "
                    f"uses DISTSTYLE {dist.upper()} and is joined - "
                    "distribution skew risk",
                    file=row.file,
                    evidence=(
                        f"SVV/STL export row + {len(joined)} join query(ies) "
                        f"({'observed' if observed_join else 'authored'})"
                    ),
                    evidence_kind=EvidenceKind.OBSERVED_METADATA,
                )
            )
        return results


class UnsortedRangeFilteredTable(_RedshiftCheck):
    """RS002: table without SORTKEY read through range predicates."""

    id = "RS002"
    title = "Unsorted table scanned by range predicates"
    why = (
        "Zone maps only prune blocks on sortkey columns; a table with no "
        "SORTKEY read through range filters scans every block."
    )
    when_ok = "Range-filtered tables declare SORTKEY on the filter column."
    fix = "Add SORTKEY(<range column>) or enable ATO to let Redshift choose."

    _RANGE = re.compile(r"(?:\bbetween\b|>=|<=|<>|<|>)", re.IGNORECASE)

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        unsorted = model.unsorted_tables()
        if not unsorted:
            return []
        results = []
        seen: set[str] = set()
        for q in model.queries:
            where = re.search(r"\bwhere\b(.*)", q.text, re.IGNORECASE | re.DOTALL)
            if not where or not self._RANGE.search(where.group(1)):
                continue
            for name in unsorted:
                key = f"{name}:{q.file}:{q.line}"
                if key in seen or not _name_matches(name, set(q.tables_read)):
                    continue
                seen.add(key)
                results.append(
                    self.result(
                        Severity.WARNING,
                        f"table '{name}' has no SORTKEY but queries filter "
                        "ranges on it - zone maps cannot help",
                        file=q.file,
                        line=q.line,
                        evidence=(
                            f"range predicate in {q.source} query"
                            if q.source == "job"
                            else self.evidence_at(ctx, q.file, q.line)
                        ),
                        evidence_kind=(
                            EvidenceKind.OBSERVED_METADATA
                            if q.source == "job"
                            else EvidenceKind.STATIC
                        ),
                    )
                )
        return results


class AtoDisabledWithSkew(_RedshiftCheck):
    """RS003: ATO off on provisioned cluster while tables skew."""

    id = "RS003"
    title = "automatic_table_optimization off with skewed tables"
    why = (
        "Skewed distributions waste slots on straggler nodes; ATO "
        "self-corrects diststyle/sortkey — turning it off forfeits that."
    )
    when_ok = "auto_analyze/ATO stay enabled on provisioned clusters."
    fix = "Remove the disabling parameter or set auto_analyze=true."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        skewed = [
            r
            for r in model.observed_tables()
            if _pct(r.get("skew_rows")) > 0
            or _pct(r.get("pct_skew")) > 0
            or _pct(r.get("unsorted")) > 0
        ]
        if not skewed:
            return []
        results = []
        for grp in model.by_kind("parameter_group"):
            off = {
                k: v
                for k, v in grp.attrs
                if (k.endswith("auto_analyze") or "automatic_table_optimization" in k) and _falsy(v)
            }
            if off:
                results.append(
                    self.result(
                        Severity.WARNING,
                        f"parameter group '{grp.name}' disables "
                        f"{', '.join(sorted(off))} while {len(skewed)} observed "
                        "table(s) carry skew",
                        file=grp.file,
                        line=grp.line,
                        evidence=(
                            self.evidence_at(ctx, grp.file, grp.line)
                            if grp.file is not None
                            else None
                        ),
                        evidence_kind=EvidenceKind.CONFIG,
                    )
                )
        for c in model.compute:
            if _falsy(c.attr("automatic_table_optimization")) and skewed:
                results.append(
                    self.result(
                        Severity.WARNING,
                        f"cluster '{c.name}' sets automatic_table_optimization "
                        f"off while {len(skewed)} observed table(s) carry skew",
                        file=c.file,
                        line=c.line,
                        evidence_kind=EvidenceKind.CONFIG,
                    )
                )
        return results


def _pct(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class InsecureClusterFlags(_RedshiftCheck):
    """RS004: publicly_accessible / unencrypted Terraform cluster."""

    id = "RS004"
    title = "Public or unencrypted cluster"
    why = (
        "publicly_accessible=true exposes the endpoint; encrypted=false "
        "leaves data unprotected at rest — both are declarative IaC facts."
    )
    when_ok = "Clusters are private and encrypted."
    fix = "Set publicly_accessible=false and encrypted=true (or KMS)."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        results = []
        for c in _model(ctx).compute:
            public = c.attr("publicly_accessible").lower() == "true"
            unenc = c.attr("encrypted").lower() in {"false", "0"}
            if not (public or unenc):
                continue
            why_bits = []
            if public:
                why_bits.append("publicly_accessible")
            if unenc:
                why_bits.append("encrypted=false")
            results.append(
                self.result(
                    Severity.ERROR if public else Severity.WARNING,
                    f"cluster '{c.name}' is {' and '.join(why_bits)}",
                    file=c.file,
                    line=c.line,
                    evidence=self.evidence_at(ctx, c.file, c.line) if c.file is not None else None,
                    evidence_kind=EvidenceKind.CONFIG,
                )
            )
        return results


class ManualMaintenanceWhenAto(_RedshiftCheck):
    """RS005: manual VACUUM/ANALYZE while ATO is available."""

    id = "RS005"
    title = "Manual VACUUM/ANALYZE scripts with ATO available"
    why = (
        "ATO (auto sort/vacuum/analyze/mv) is eligible on ra3+/dc2+/"
        "serverless; hand-rolled maintenance scripts drift and double-run."
    )
    when_ok = "Maintenance statements come from ATO, not authored scripts."
    fix = "Prefer automatic_table_optimization / auto_analyze; drop the script."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.ato_available():
            return []
        results = []
        for q in model.queries:
            if q.source != "file" or q.kind not in {"vacuum", "analyze"}:
                continue
            results.append(
                self.result(
                    Severity.INFO,
                    f"manual '{q.kind.upper()}' script while ATO-eligible compute exists",
                    file=q.file,
                    line=q.line,
                    evidence=self.evidence_at(ctx, q.file, q.line),
                )
            )
        return results


CHECKS: tuple[Check, ...] = (
    RedshiftUsage(),
    BadDistributionStyle(),
    UnsortedRangeFilteredTable(),
    AtoDisabledWithSkew(),
    InsecureClusterFlags(),
    ManualMaintenanceWhenAto(),
)
