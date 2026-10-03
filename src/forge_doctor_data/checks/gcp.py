"""GCP data-platform checks (GCP###) over the GcpPlatformModel.

Evidence plane: config (Terraform google_* resources). Deterministic
config facts only — no cloud calls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge_doctor_data.analyzers.gcp_model import gcp_model
from forge_doctor_data.core.models import EvidenceKind, Severity
from forge_doctor_data.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor_data.core.context import ProjectContext
    from forge_doctor_data.core.models import CheckResult


class _GcpCheck(CheckBase):
    category = "gcp"


class GcpSurface(_GcpCheck):
    """GCP000: anchor census of the GCP data-platform surface."""

    id = "GCP000"
    title = "GCP data-platform surface"
    why = "Anchor: sizes the GCP estate feeding the GCP checks."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the counts to size the surface."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = gcp_model(ctx)
        if not model.has_evidence:
            return [self.result(Severity.PASS, "no GCP data-platform evidence detected")]
        parts = [
            f"{len(model.gcs_buckets)} gcs buckets",
            f"{len(model.dataflow_jobs)} dataflow jobs",
            f"{len(model.dataproc_clusters)} dataproc clusters",
            f"{len(model.pubsub_topics)} pubsub topics",
            f"{len(model.pubsub_subscriptions)} pubsub subscriptions",
        ]
        return [self.result(Severity.INFO, ", ".join(parts))]


class PubSubNoDeadLetter(_GcpCheck):
    """GCP001: Pub/Sub subscription without a dead-letter policy."""

    id = "GCP001"
    title = "Pub/Sub subscription without dead-letter policy"
    why = (
        "Without dead_letter_policy, a message that cannot be delivered "
        "after the configured attempts is dropped — silent poison-message "
        "loss with no replayable parking lot."
    )
    when_ok = "Subscriptions declare dead_letter_policy with a DLQ topic."
    fix = "Add dead_letter_policy { dead_letter_topic, max_delivery_attempts }."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        out: list[CheckResult] = []
        for sub in gcp_model(ctx).pubsub_subscriptions:
            if sub.has_dead_letter:
                continue
            out.append(
                self.result(
                    Severity.WARNING,
                    f"pubsub subscription '{sub.name}' has no "
                    "dead_letter_policy — undeliverable messages are dropped",
                    file=sub.file,
                    line=sub.line,
                    evidence="dead_letter_policy absent",
                    evidence_kind=EvidenceKind.CONFIG,
                )
            )
        return out


class GcsForceDestroyNoVersioning(_GcpCheck):
    """GCP002: bucket that can be force-destroyed with versioning off."""

    id = "GCP002"
    title = "GCS bucket force_destroy with versioning off"
    why = (
        "force_destroy=true lets Terraform delete a non-empty bucket; "
        "with versioning disabled there is no recovery path for objects "
        "— one terraform destroy loses the data permanently."
    )
    when_ok = "Data buckets either version objects or forbid force-destroy."
    fix = "Set force_destroy=false and/or versioning { enabled = true }."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        out: list[CheckResult] = []
        for b in gcp_model(ctx).gcs_buckets:
            if b.force_destroy != "true":
                continue
            if b.versioning == "true":
                continue
            out.append(
                self.result(
                    Severity.WARNING,
                    f"gcs bucket '{b.name}' has force_destroy=true and "
                    f"versioning {b.versioning or 'unset'} — destroy loses data",
                    file=b.file,
                    line=b.line,
                    evidence=f"force_destroy=true, versioning={b.versioning or 'missing'}",
                    evidence_kind=EvidenceKind.CONFIG,
                )
            )
        return out


class DataflowCancelOnDelete(_GcpCheck):
    """GCP003: streaming Dataflow job that cancels (not drains) on delete."""

    id = "GCP003"
    title = "Dataflow job cancelled (not drained) on delete"
    why = (
        "on_delete=cancel abandons in-flight work; drain finishes the "
        "pipeline's buffered data. For streaming jobs cancel means data "
        "loss between checkpoint boundaries."
    )
    when_ok = "Jobs use on_delete=drain (default) so teardown finishes in-flight data."
    fix = "Remove on_delete=cancel or set on_delete=drain."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        out: list[CheckResult] = []
        for job in gcp_model(ctx).dataflow_jobs:
            if job.on_delete.lower() != "cancel":
                continue
            out.append(
                self.result(
                    Severity.WARNING,
                    f"dataflow job '{job.name}' uses on_delete=cancel — "
                    "in-flight data is abandoned on teardown",
                    file=job.file,
                    line=job.line,
                    evidence="on_delete=cancel",
                    evidence_kind=EvidenceKind.CONFIG,
                )
            )
        return out


CHECKS: tuple[Check, ...] = (
    GcpSurface(),
    PubSubNoDeadLetter(),
    GcsForceDestroyNoVersioning(),
    DataflowCancelOnDelete(),
)
