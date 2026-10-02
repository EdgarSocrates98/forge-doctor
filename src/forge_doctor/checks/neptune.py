"""NEP### checks over NeptuneProjectModel - queries, ingest, infra, globals.

Query checks (NEP020-024) apply per language where the shape is
expressible. Capacity/topology claims stay honest: when asymmetry is not
observable the check reports INFO or stays silent. Capability-derived
findings (NEP010, NEPGT*) resolve through the phase-1 registry and carry
DERIVED evidence. No check recommends "use Neptune" or runs queries.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, cast

from forge_doctor.analyzers.neptune_model import (
    NEPTUNE_ANALYTICS,
    NeptuneProjectModel,
    neptune_model,
)
from forge_doctor.analyzers.neptune_queries import neptune_queries
from forge_doctor.core.capabilities import CapabilityStatus
from forge_doctor.core.models import Confidence, EvidenceKind, Severity
from forge_doctor.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor.core.capabilities import CapabilityResult
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.models import CheckResult


def _model(ctx: ProjectContext) -> NeptuneProjectModel:
    return neptune_model(ctx)


class _NepCheck(CheckBase):
    category = "neptune"


def _capability(ctx: ProjectContext, capability: str, **attrs: str) -> CapabilityResult | None:
    caps = cast(Any, getattr(ctx, "capabilities", None))
    if caps is None:
        return None
    return cast(
        "CapabilityResult | None",
        caps.evaluate(capability, platform="neptune", **attrs),
    )


class NeptuneUsage(_NepCheck):
    """NEP001 anchor: how much of the project touches Neptune."""

    id = "NEP001"
    title = "Neptune workload detected"
    why = "Anchor: sizes the Neptune surface feeding the other NEP checks."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the counts to size the surface."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.has_neptune:
            return [self.result(Severity.PASS, "no neptune workloads detected")]
        return [
            self.result(
                Severity.INFO,
                f"product={model.product} "
                f"{len(model.clusters)} clusters, "
                f"{len(model.instances)} instances, "
                f"{len(model.endpoints)} endpoints, "
                f"{len(model.bulk_loads)} bulk loads, "
                f"languages={sorted(model.query_languages) or '-'}, "
                f"queries={model.read_traversals}r/{model.write_traversals}w",
            )
        ]


class LanguageParadigmMismatch(_NepCheck):
    """NEP010: query language vs loaded graph model, via the registry."""

    id = "NEP010"
    title = "Query language incompatible with graph paradigm"
    why = "Gremlin/openCypher only see property-graph data; SPARQL only "
    "sees RDF. A workload loaded under one paradigm cannot be queried by "
    "a language bound to the other."
    when_ok = "Queries and load formats share one paradigm."
    fix = "Align the query language with the loaded graph model."
    confidence = Confidence.MEDIUM
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        from forge_doctor.analyzers.graph_model import graph_model

        model = _model(ctx)
        paradigms = graph_model(ctx).paradigms
        out: list[CheckResult] = []
        cap_by_lang = {
            "opencypher": "NEPTUNE_OPENCYPHER",
            "gremlin": "NEPTUNE_GREMLIN",
            "sparql": "NEPTUNE_SPARQL",
        }
        for lang in sorted(model.query_languages):
            cap_id = cap_by_lang.get(lang)
            if cap_id is None:
                continue
            for paradigm in sorted(paradigms):
                cap = _capability(ctx, cap_id, graph_model=paradigm)
                if cap is None or cap.status is not CapabilityStatus.UNSUPPORTED:
                    continue
                out.append(
                    self.result(
                        Severity.WARNING,
                        f"{lang} queries cannot read {paradigm}-loaded data "
                        f"- {cap.reason} [source: {cap.source}]",
                        evidence_kind=EvidenceKind.DERIVED,
                    )
                )
        return out


class WeakSelectivity(_NepCheck):
    """NEP020: traversal/MATCH without a selective starting predicate."""

    id = "NEP020"
    title = "Traversal without selective start"
    why = "Starting from all vertices/edges and filtering later puts the "
    "whole graph in the working set - a static selectivity risk; runtime "
    "cost is unknown without explain/profile evidence."
    when_ok = "Full-graph traversals are intentional (exports, analytics)."
    fix = "Bind the start with a label/property/id predicate."
    confidence = Confidence.MEDIUM

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.WARNING,
                f"{q.language} traversal starts unselective ({q.start or 'unresolved start'})",
                file=q.file,
                line=q.line,
                evidence=q.raw or None,
            )
            for q in neptune_queries(ctx).queries
            if q.parsed and not q.start_selective and q.hop_count > 0
        ]


class UnboundedVariableLength(_NepCheck):
    """NEP021: ``[*]``/``repeat()`` without an upper bound."""

    id = "NEP021"
    title = "Unbounded variable-length traversal"
    why = "Variable-length patterns with no upper bound expand until the "
    "graph exhausts them - unbounded intermediate results."
    when_ok = "The graph is provably shallow or the walk is bounded by "
    "until()/LIMIT elsewhere."
    fix = "Add an upper bound (``*1..n``, ``times(n)``, ``until``)."
    confidence = Confidence.MEDIUM

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.WARNING,
                f"{q.language} variable-length traversal has no upper "
                "bound - result size is not statically bounded",
                file=q.file,
                line=q.line,
                evidence=q.raw or None,
            )
            for q in neptune_queries(ctx).queries
            if q.parsed and q.has_variable_length and q.hop_bounds == "unbounded"
        ]


class CartesianPattern(_NepCheck):
    """NEP022: disconnected MATCH clauses without a relationship."""

    id = "NEP022"
    title = "Cartesian graph pattern"
    why = "Separate MATCH patterns with no joining relationship multiply "
    "the intermediate result set."
    when_ok = "Independent patterns are intentional and small."
    fix = "Connect the patterns or split the query."
    confidence = Confidence.LOW
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        out: list[CheckResult] = []
        for q in neptune_queries(ctx).queries:
            if q.language != "opencypher" or not q.parsed:
                continue
            matches = len(re.findall(r"\bMATCH\b", q.raw, re.I))
            disconnected = (
                (len(q.vertex_labels) >= 2 or matches >= 2)
                and not q.edge_labels
                and not q.edge_endpoints
            )
            if disconnected:
                out.append(
                    self.result(
                        Severity.WARNING,
                        "MATCH patterns with no connecting relationship - cartesian product risk",
                        file=q.file,
                        line=q.line,
                        evidence=q.raw or None,
                    )
                )
        return out


class LargeProjection(_NepCheck):
    """NEP023: ``RETURN *``/wide projection on an unbounded result."""

    id = "NEP023"
    title = "Large unbounded result projection"
    why = "Projecting everything over an unbounded pattern returns the "
    "largest possible row shape."
    when_ok = "The caller materializes the full result deliberately."
    fix = "Project the needed properties and add a LIMIT."
    confidence = Confidence.MEDIUM

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.INFO,
                f"{q.language} query projects everything without a LIMIT",
                file=q.file,
                line=q.line,
                evidence=q.raw or None,
            )
            for q in neptune_queries(ctx).queries
            if q.parsed and q.projection_star and not q.result_bounded
        ]


class LateFilter(_NepCheck):
    """NEP024: property filter applied after traversal expansion."""

    id = "NEP024"
    title = "Property filter applied post-traversal"
    why = "Filtering after hop expansion materializes every intermediate "
    "vertex instead of pruning early."
    when_ok = "The predicate genuinely applies to a later step."
    fix = "Move the filter ahead of the traversal steps."
    confidence = Confidence.MEDIUM

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.WARNING,
                f"{q.language} traversal applies its first filter at step "
                f"{q.first_filter_step} after {q.hop_count} hop(s) - "
                "late-filtering risk",
                file=q.file,
                line=q.line,
                evidence=q.raw or None,
            )
            for q in neptune_queries(ctx).queries
            if q.parsed
            and q.first_filter_step is not None
            and q.hop_count >= 1
            and q.first_filter_step > 2
            and not q.start_selective
        ]


class RowByRowIngestion(_NepCheck):
    """NEP030: write-per-item loops over the graph API."""

    id = "NEP030"
    title = "Row-by-row ingestion pattern"
    why = "Repeated addV/addE/property calls one item at a time is the "
    "slow path for large ingestions - the bulk loader exists for that "
    "shape."
    when_ok = "Writes are interactive mutations, not a bulk ingest."
    fix = "Batch the writes or evaluate the Neptune bulk loader."
    confidence = Confidence.MEDIUM
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        writes = [q for q in neptune_queries(ctx).queries if q.writes]
        if len(writes) < 3 or model.bulk_loads:
            return []
        by_file: dict[str, int] = {}
        for q in writes:
            key = q.file.as_posix()
            by_file[key] = by_file.get(key, 0) + 1
        if max(by_file.values()) < 3:
            return []
        first = writes[0]
        return [
            self.result(
                Severity.WARNING,
                f"{len(writes)} graph write operations look like "
                "row-by-row ingestion - no bulk-loader evidence found",
                file=first.file,
                line=first.line,
                evidence_kind=EvidenceKind.DERIVED,
            )
        ]


class BulkLoaderCandidate(_NepCheck):
    """NEP031: substantial write workload with no loader usage."""

    id = "NEP031"
    title = "Bulk-loader candidate"
    why = "The bulk loader is the optimized ingestion path from S3; a "
    "write-heavy workload with no loader evidence may leave it unused."
    when_ok = "Writes are transactional mutations, not ingestion."
    fix = "Evaluate the bulk loader for large ingestion jobs."
    confidence = Confidence.LOW
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if model.bulk_loads or model.write_traversals < 2:
            return []
        return [
            self.result(
                Severity.INFO,
                f"{model.write_traversals} write traversals detected with "
                "no bulk-loader usage - a candidate worth evaluating",
            )
        ]


class BulkLoadIam(_NepCheck):
    """NEP032: loader call without a complete IAM/S3 relationship."""

    id = "NEP032"
    title = "Bulk-load IAM/S3 relationship incomplete"
    why = "The loader needs a role Neptune can assume with S3 read "
    "access; a call without iamRoleArn cannot load."
    when_ok = "The role is provisioned outside the visible config."
    fix = "Pass iamRoleArn for a role with S3 read on the source bucket."
    confidence = Confidence.MEDIUM

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        out: list[CheckResult] = []
        for load in _model(ctx).bulk_loads:
            if load.origin == "file":
                continue
            if load.origin == "api" and not load.iam_role:
                out.append(
                    self.result(
                        Severity.WARNING,
                        "start_loader_job call has no iamRoleArn - the "
                        "loader cannot assume a role to read S3",
                        file=load.file,
                        line=load.line,
                        evidence=load.raw or None,
                    )
                )
            if load.origin == "api" and load.iam_role:
                from forge_doctor.analyzers.terraform_model import (
                    terraform_model,
                )

                tf = terraform_model(ctx)
                s3_policy = any(
                    "s3" in b.body.lower()
                    for b in tf.resources
                    if b.labels and b.labels[0] in {"aws_iam_role_policy", "aws_iam_policy"}
                )
                if tf.resources and not s3_policy:
                    out.append(
                        self.result(
                            Severity.INFO,
                            "loader iamRoleArn set, but no S3 permission "
                            "policy is visible in the Terraform - the "
                            "role's S3 access is unverifiable here",
                            file=load.file,
                            line=load.line,
                            confidence=Confidence.LOW,
                            evidence_kind=EvidenceKind.DERIVED,
                        )
                    )
        return out


class MalformedInput(_NepCheck):
    """NEP033: query/graph input that could not be parsed."""

    id = "NEP033"
    title = "Malformed graph input risk"
    why = "Inputs that fail static parsing would fail at the service - "
    "or carry a shape this analyzer cannot attest."
    when_ok = "The string is assembled dynamically elsewhere."
    fix = "Fix the syntax or mark the source of the dynamic query."
    confidence = Confidence.MEDIUM

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.INFO,
                f"{q.language} input did not parse - shape unresolved",
                file=q.file,
                line=q.line,
                evidence=q.raw or None,
            )
            for q in neptune_queries(ctx).unparsed
        ]


class ReadReplicaCoverage(_NepCheck):
    """NEP040: read-heavy workload with no replica evidence (INFO-gated)."""

    id = "NEP040"
    title = "Read-heavy workload, no replica evidence"
    why = "Read-heavy clusters scale reads via replicas; when read "
    "asymmetry is observable and no replica exists, the cluster may be "
    "under-provisioned for reads - a static observation, not a capacity "
    "claim."
    when_ok = "Replicas exist outside the repo, or reads are cheap."
    fix = "Add replica instances if the read load needs them."
    confidence = Confidence.LOW
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not (model.read_traversals >= 3 and model.read_traversals >= 2 * model.write_traversals):
            return []
        return [
            self.result(
                Severity.INFO,
                f"cluster {c.name!r} has {c.replica_count} replicas while "
                f"{model.read_traversals} read vs {model.write_traversals} "
                "write traversals are observed",
                file=c.file,
                line=c.line,
            )
            for c in model.clusters
            if c.replica_count == 0
        ]


class WeakBackup(_NepCheck):
    """NEP041: no retention/PITR posture on the cluster."""

    id = "NEP041"
    title = "Weak backup/PITR posture"
    why = "A cluster without backup retention (or skipping the final "
    "snapshot) has no recovery window if deleted."
    when_ok = "Backups are handled outside Terraform (snapshots, org "
    "policy)."
    fix = "Set backup_retention_period and keep a final snapshot."
    confidence = Confidence.MEDIUM

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        out: list[CheckResult] = []
        for c in _model(ctx).clusters:
            if c.backup_retention is not None and c.backup_retention >= 1:
                continue
            reason = (
                "no backup_retention_period configured"
                if c.backup_retention in (None, 0)
                else f"backup_retention_period={c.backup_retention}d"
            )
            if c.skip_final_snapshot:
                reason += "; skip_final_snapshot=true"
            out.append(
                self.result(
                    Severity.WARNING if c.skip_final_snapshot else Severity.INFO,
                    f"cluster {c.name!r}: {reason}",
                    file=c.file,
                    line=c.line,
                )
            )
        return out


class PublicAccess(_NepCheck):
    """NEP042: instance exposed publicly."""

    id = "NEP042"
    title = "Public-access assumption"
    why = "publicly_accessible instances accept connections outside the "
    "VPC - a deliberate choice that must be confirmed, not assumed."
    when_ok = "Public access is intentional and guarded elsewhere."
    fix = "Disable publicly_accessible or front with a controlled "
    "endpoint."
    confidence = Confidence.MEDIUM

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.WARNING,
                f"instance {i.name!r} sets publicly_accessible=true",
                file=i.file,
                line=i.line,
            )
            for c in _model(ctx).clusters
            for i in c.instances
            if i.publicly_accessible
        ]


class IamAuthMismatch(_NepCheck):
    """NEP043: IAM-auth flag and client signing disagree."""

    id = "NEP043"
    title = "IAM-auth configuration mismatch"
    why = "A cluster enforcing IAM auth fails unsigned clients; a client "
    "signing against a non-IAM cluster is dead config."
    when_ok = "Clients and cluster agree on IAM auth."
    fix = "Align iam_database_authentication_enabled with client signing."
    confidence = Confidence.LOW
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        out: list[CheckResult] = []
        for c in model.clusters:
            if c.iam_auth is True and model.endpoints and not model.iam_in_code:
                out.append(
                    self.result(
                        Severity.INFO,
                        f"cluster {c.name!r} enforces IAM auth but no "
                        "SigV4/signing evidence appears in code",
                        file=c.file,
                        line=c.line,
                    )
                )
            if c.iam_auth is False and model.iam_in_code:
                out.append(
                    self.result(
                        Severity.INFO,
                        f"cluster {c.name!r} disables IAM auth while code "
                        "carries SigV4/signing hints",
                        file=c.file,
                        line=c.line,
                    )
                )
        return out


class SecurityGroupTopology(_NepCheck):
    """NEP044: cluster networking cannot be shown to admit Neptune."""

    id = "NEP044"
    title = "Security-group topology risk"
    why = "A cluster without attached security groups, or whose groups "
    "never open the Neptune port, rejects its own clients."
    when_ok = "SG rules live outside the scanned Terraform."
    fix = "Attach an SG with ingress on the cluster port (8182)."
    confidence = Confidence.LOW
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        from forge_doctor.analyzers.terraform_model import terraform_model

        tf = terraform_model(ctx)
        sg_text = " ".join(
            b.body for b in tf.resources if b.labels and b.labels[0] == "aws_security_group"
        )
        port_seen = "8182" in sg_text or "0" in sg_text
        out: list[CheckResult] = []
        for c in model.clusters:
            if not c.security_groups:
                out.append(
                    self.result(
                        Severity.INFO,
                        f"cluster {c.name!r} declares no "
                        "vpc_security_group_ids - ingress is unmanaged "
                        "here",
                        file=c.file,
                        line=c.line,
                    )
                )
            elif tf.resources and not port_seen:
                out.append(
                    self.result(
                        Severity.INFO,
                        f"cluster {c.name!r} attaches security groups but "
                        "no SG in the project opens port 8182",
                        file=c.file,
                        line=c.line,
                    )
                )
        return out


class ClusterInstanceMismatch(_NepCheck):
    """NEP045: cluster topology/instance config that cannot hold."""

    id = "NEP045"
    title = "Cluster/instance configuration mismatch"
    why = "Serverless clusters require db.serverless instances; a "
    "cluster with no instances serves nothing."
    when_ok = "Instances are provisioned out-of-band."
    fix = "Align instance classes/topology with the cluster mode."
    confidence = Confidence.MEDIUM

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        out: list[CheckResult] = []
        for c in _model(ctx).clusters:
            if c.serverless:
                for i in c.instances:
                    if i.instance_class and i.instance_class != "db.serverless":
                        out.append(
                            self.result(
                                Severity.WARNING,
                                f"instance {i.name!r} uses "
                                f"{i.instance_class!r} on a serverless "
                                "cluster - serverless requires "
                                "db.serverless",
                                file=i.file,
                                line=i.line,
                            )
                        )
            if c.source in {"terraform", "cloudformation"} and not c.instances:
                out.append(
                    self.result(
                        Severity.INFO,
                        f"cluster {c.name!r} declares no instances - topology unresolved",
                        file=c.file,
                        line=c.line,
                    )
                )
        return out


class GlobalSecondaryWrite(_NepCheck):
    """NEPGT001: write traffic aimed at a secondary region."""

    id = "NEPGT001"
    title = "Write expectation in a secondary region"
    why = "Neptune Global Database has one primary write region; "
    "secondaries are read-only - writes aimed there fail."
    when_ok = "Writes are routed to the primary region."
    fix = "Route writes to the primary region endpoint."
    confidence = Confidence.LOW
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.global_clusters or model.write_traversals == 0:
            return []
        regions = {
            m.group(1)
            for e in model.endpoints
            if (m := re.search(r"\.([a-z]{2}-[a-z]+-\d)\.", e.value))
        }
        primary = model.clusters[0].region if model.clusters else ""
        out: list[CheckResult] = []
        for e in model.endpoints:
            m = re.search(r"\.([a-z]{2}-[a-z]+-\d)\.", e.value)
            if m and primary and m.group(1) != primary and regions != {primary}:
                out.append(
                    self.result(
                        Severity.WARNING,
                        f"endpoint {e.value!r} targets region "
                        f"{m.group(1)} while writes exist - secondary "
                        "regions are read-only on a global database",
                        file=e.file,
                        line=e.line,
                    )
                )
        return out


class GlobalActiveActive(_NepCheck):
    """NEPGT002: writes against multiple regions of a global database."""

    id = "NEPGT002"
    title = "Multi-region active-active write assumption"
    why = "A global database takes writes in exactly one region; write "
    "traffic fanning to several regions assumes active-active."
    when_ok = "Only the primary region receives writes."
    fix = "Pin writes to the primary region."
    confidence = Confidence.LOW
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.global_clusters or model.write_traversals == 0:
            return []
        regions = {
            m.group(1)
            for e in model.endpoints
            if (m := re.search(r"\.([a-z]{2}-[a-z]+-\d)\.", e.value))
        }
        if len(regions) < 2:
            return []
        return [
            self.result(
                Severity.INFO,
                f"endpoints span {len(regions)} regions "
                f"({sorted(regions)}) with write traversals present - "
                "only the primary region accepts writes",
            )
        ]


class GlobalRecoveryTopology(_NepCheck):
    """NEPGT003: global cluster without a visible secondary topology."""

    id = "NEPGT003"
    title = "Cross-region recovery topology incomplete"
    why = "A global cluster with no secondary cluster evidence cannot "
    "demonstrate cross-region recovery."
    when_ok = "Secondary clusters are managed elsewhere."
    fix = "Attach secondary clusters or document the recovery path."
    confidence = Confidence.LOW
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        return [
            self.result(
                Severity.INFO,
                f"global cluster {g.name!r} has no secondary-region "
                "cluster evidence in this project",
                file=g.file,
                line=g.line,
            )
            for g in model.global_clusters
            if len(model.clusters) < 2
        ]


class ManualAlgorithmWithAnalytics(_NepCheck):
    """NEPA001: hand-rolled graph algorithm while Analytics is present."""

    id = "NEPA001"
    title = "Manual algorithm with Neptune Analytics available"
    why = "Neptune Analytics ships built-in pathfinding/centrality/"
    "community algorithms; a hand-rolled duplicate is unmeasured "
    "maintenance when the managed call exists."
    when_ok = "The algorithm is domain-specific or out-of-analytics "
    "scope."
    fix = "Evaluate the corresponding Neptune Analytics algorithm call."
    confidence = Confidence.LOW
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.analytics_calls or not model.manual_algorithms:
            return []
        return [
            self.result(
                Severity.INFO,
                f"manual {a.name} implementation while Neptune Analytics "
                "is configured - an equivalent managed call may exist",
                file=a.file,
                line=a.line,
            )
            for a in model.manual_algorithms
        ]


class AnalyticsOnDatabase(_NepCheck):
    """NEPA002: analytics-style call against a Database-only project."""

    id = "NEPA002"
    title = "Algorithm call incompatible with detected product"
    why = "Graph algorithms run on Neptune Analytics; invoking them "
    "against a Database cluster is a product mismatch."
    when_ok = "The code targets an Analytics endpoint not visible here."
    fix = "Confirm the call reaches Neptune Analytics, not Database."
    confidence = Confidence.LOW
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if model.analytics_calls or NEPTUNE_ANALYTICS in model.products:
            return []
        out: list[CheckResult] = []
        for a in model.manual_algorithms:
            out.append(
                self.result(
                    Severity.INFO,
                    f"{a.name} algorithm usage with no Neptune Analytics "
                    "evidence - analytics algorithms live on a separate "
                    "service",
                    file=a.file,
                    line=a.line,
                )
            )
        return out


class StreamToNeptuneMutation(_NepCheck):
    """NEPCD001: DynamoDB stream consumer mutating Neptune without
    idempotency evidence - retry replays duplicate the mutation."""

    id = "NEPCD001"
    title = "Stream-fed Neptune mutation lacks idempotency"
    why = "DynamoDB stream records can replay; a consumer performing "
    "non-idempotent graph mutations duplicates effects on retry."
    when_ok = "Mutations are keyed/idempotent or deduplicated upstream."
    fix = "Make Neptune mutations idempotent (merge keys) or dedupe on "
    "eventID."
    confidence = Confidence.LOW
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        from forge_doctor.analyzers.dynamodb_model import dynamodb_model

        ddb = dynamodb_model(ctx)
        model = _model(ctx)
        at_risk = [s for s in ddb.streams if s.consumers and not s.idempotency_signal]
        if not at_risk or model.write_traversals == 0:
            return []
        return [
            self.result(
                Severity.WARNING,
                f"DynamoDB stream on {s.table or 'undeclared table'!r} "
                "feeds consumers with no idempotency signal while "
                f"{model.write_traversals} graph write traversal(s) are "
                "present - replayed records may double-apply mutations",
                file=s.file,
                line=s.line,
            )
            for s in at_risk
        ]


CHECKS: list[Check] = [
    NeptuneUsage(),
    LanguageParadigmMismatch(),
    WeakSelectivity(),
    UnboundedVariableLength(),
    CartesianPattern(),
    LargeProjection(),
    LateFilter(),
    RowByRowIngestion(),
    BulkLoaderCandidate(),
    BulkLoadIam(),
    MalformedInput(),
    ReadReplicaCoverage(),
    WeakBackup(),
    PublicAccess(),
    IamAuthMismatch(),
    SecurityGroupTopology(),
    ClusterInstanceMismatch(),
    GlobalSecondaryWrite(),
    GlobalActiveActive(),
    GlobalRecoveryTopology(),
    ManualAlgorithmWithAnalytics(),
    AnalyticsOnDatabase(),
    StreamToNeptuneMutation(),
]
