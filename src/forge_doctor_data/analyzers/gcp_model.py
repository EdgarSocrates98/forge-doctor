"""GCP data-platform model (spec 233).

Deterministic, offline evidence only: ``google_*`` Terraform blocks and
committed config artifacts. Strings keep "unset" distinct from "false".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from forge_doctor_data.core.context import ProjectContext

_CACHE_ATTR = "_forge_doctor_data_gcp_model"


@dataclass(frozen=True)
class GcsBucket:
    name: str
    file: Path
    line: int
    storage_class: str = ""
    versioning: str = ""  # versioning.enabled true|false|""
    force_destroy: str = ""
    has_lifecycle: bool = False  # lifecycle_rule block present
    kms: str = ""  # encryption.default_kms_key_name
    uniform_access: str = ""  # uniform_bucket_level_access


@dataclass(frozen=True)
class DataflowJob:
    name: str
    file: Path
    line: int
    streaming: str = ""  # parameters.streaming / template_gcs_path streaming flag
    on_delete: str = ""  # on_delete cancel|drain
    max_workers: str = ""
    service_account: str = ""


@dataclass(frozen=True)
class DataprocCluster:
    name: str
    file: Path
    line: int
    has_autoscaling: bool = False
    preemptible_workers: str = ""  # worker_config.num_preemptible_instances
    image_version: str = ""


@dataclass(frozen=True)
class PubSubTopic:
    name: str
    file: Path
    line: int
    resource: str = ""  # terraform label (ref lookups)
    message_retention: str = ""  # message_retention_duration
    has_schema: bool = False
    kms: str = ""  # kms_key_name


@dataclass(frozen=True)
class PubSubSubscription:
    name: str
    file: Path
    line: int
    topic: str = ""
    ack_deadline: str = ""
    enable_ordering: str = ""  # enable_message_ordering
    has_dead_letter: bool = False  # dead_letter_policy block
    has_retry: bool = False  # retry_policy block
    max_delivery_attempts: str = ""


@dataclass(frozen=True)
class ComposerEnv:
    name: str
    file: Path
    line: int
    image_version: str = ""


@dataclass(frozen=True)
class DataplexLake:
    name: str
    file: Path
    line: int
    zones: tuple[str, ...] = ()


@dataclass(frozen=True)
class CloudFunction:
    name: str
    file: Path
    line: int


@dataclass
class GcpPlatformModel:
    """All GCP data-platform evidence for a project."""

    gcs_buckets: list[GcsBucket] = field(default_factory=list)
    dataflow_jobs: list[DataflowJob] = field(default_factory=list)
    dataproc_clusters: list[DataprocCluster] = field(default_factory=list)
    pubsub_topics: list[PubSubTopic] = field(default_factory=list)
    pubsub_subscriptions: list[PubSubSubscription] = field(default_factory=list)
    composer_envs: list[ComposerEnv] = field(default_factory=list)
    dataplex_lakes: list[DataplexLake] = field(default_factory=list)
    functions: list[CloudFunction] = field(default_factory=list)

    @property
    def has_evidence(self) -> bool:
        return bool(self.services())

    def services(self) -> list[str]:
        out: list[str] = []
        for attr, name in (
            ("gcs_buckets", "gcs"),
            ("dataflow_jobs", "dataflow"),
            ("dataproc_clusters", "dataproc"),
            ("pubsub_topics", "pubsub"),
            ("pubsub_subscriptions", "pubsub"),
            ("composer_envs", "composer"),
            ("dataplex_lakes", "dataplex"),
            ("functions", "cloud_functions"),
        ):
            if getattr(self, attr):
                out.append(name)
        return sorted(set(out))


def _s(attrs: dict[str, Any], *keys: str) -> str:
    for key in keys:
        v = attrs.get(key)
        if v is not None and str(v) != "":
            if isinstance(v, bool):
                return str(v).lower()
            return str(v)
    return ""


def _body_has(body: str, pattern: str) -> bool:
    """Nested blocks live in the raw body — flat attrs only cover
    top-level scalars."""
    import re

    return bool(re.search(pattern, body, re.IGNORECASE | re.DOTALL))


def _body_value(body: str, pattern: str) -> str:
    import re

    m = re.search(pattern, body, re.IGNORECASE | re.DOTALL)
    return m.group(1) if m else ""


def gcp_model(ctx: ProjectContext) -> GcpPlatformModel:
    """Memoized GCP evidence model over Terraform resources."""
    cached = getattr(ctx, _CACHE_ATTR, None)
    if cached is not None:
        return cast(GcpPlatformModel, cached)

    from forge_doctor_data.analyzers.terraform_model import terraform_model

    model = GcpPlatformModel()
    tf = terraform_model(ctx)
    lakes: dict[str, DataplexLake] = {}
    zones: list[tuple[str, str]] = []

    for block in tf.blocks:
        if block.kind != "resource" or len(block.labels) < 2:
            continue
        rtype, rname = block.labels[0], block.labels[1]
        attrs = block.attrs or {}
        if not rtype.startswith("google_"):
            continue
        if rtype == "google_storage_bucket":
            model.gcs_buckets.append(
                GcsBucket(
                    name=_s(attrs, "name") or rname,
                    file=block.file,
                    line=block.line,
                    storage_class=_s(attrs, "storage_class"),
                    versioning=_body_value(
                        block.body, r"versioning\s*\{[^}]*enabled\s*=\s*(true|false)"
                    ),
                    force_destroy=_s(attrs, "force_destroy"),
                    has_lifecycle=_body_has(block.body, r"lifecycle_rule\s*\{"),
                    kms=_body_value(
                        block.body, r"encryption\s*\{[^}]*default_kms_key_name\s*=\s*\"([^\"]+)"
                    ),
                    uniform_access=_s(attrs, "uniform_bucket_level_access"),
                )
            )
        elif rtype in ("google_dataflow_job", "google_dataflow_flex_template_job"):
            model.dataflow_jobs.append(
                DataflowJob(
                    name=_s(attrs, "name") or rname,
                    file=block.file,
                    line=block.line,
                    streaming=_body_value(
                        block.body,
                        r"(?:parameters\s*\{[^}]*|--)streaming\s*[=\s]\s*\"?(true|false)",
                    ),
                    on_delete=_s(attrs, "on_delete"),
                    max_workers=_s(attrs, "max_workers"),
                    service_account=_s(attrs, "service_account_email"),
                )
            )
        elif rtype == "google_dataproc_cluster":
            model.dataproc_clusters.append(
                DataprocCluster(
                    name=_s(attrs, "cluster_config.cluster_name", "name") or rname,
                    file=block.file,
                    line=block.line,
                    has_autoscaling=_body_has(block.body, r"autoscaling_config\s*\{"),
                    preemptible_workers=_body_value(
                        block.body,
                        r"preemptible_worker_config\s*\{[^}]*num_instances\s*=\s*(\d+)",
                    ),
                    image_version=_body_value(
                        block.body, r"software_config\s*\{[^}]*image_version\s*=\s*\"([^\"]+)"
                    ),
                )
            )
        elif rtype == "google_pubsub_topic":
            model.pubsub_topics.append(
                PubSubTopic(
                    name=_s(attrs, "name") or rname,
                    file=block.file,
                    line=block.line,
                    resource=rname,
                    message_retention=_s(attrs, "message_retention_duration"),
                    has_schema=_body_has(block.body, r"schema_settings\s*\{"),
                    kms=_s(attrs, "kms_key_name"),
                )
            )
        elif rtype == "google_pubsub_subscription":
            model.pubsub_subscriptions.append(
                PubSubSubscription(
                    name=_s(attrs, "name") or rname,
                    file=block.file,
                    line=block.line,
                    topic=_s(attrs, "topic"),
                    ack_deadline=_s(attrs, "ack_deadline_seconds"),
                    enable_ordering=_s(attrs, "enable_message_ordering"),
                    has_dead_letter=_body_has(block.body, r"dead_letter_policy\s*\{"),
                    has_retry=_body_has(block.body, r"retry_policy\s*\{"),
                    max_delivery_attempts=_body_value(
                        block.body,
                        r"dead_letter_policy\s*\{[^}]*max_delivery_attempts\s*=\s*(\d+)",
                    ),
                )
            )
        elif rtype == "google_composer_environment":
            model.composer_envs.append(
                ComposerEnv(
                    name=_s(attrs, "name") or rname,
                    file=block.file,
                    line=block.line,
                    image_version=_body_value(
                        block.body, r"software_config\s*\{[^}]*image_version\s*=\s*\"([^\"]+)"
                    ),
                )
            )
        elif rtype == "google_dataplex_lake":
            lakes[rname] = DataplexLake(
                name=_s(attrs, "name") or rname, file=block.file, line=block.line
            )
        elif rtype == "google_dataplex_zone":
            zones.append((_s(attrs, "name") or rname, _s(attrs, "lake")))
        elif rtype in ("google_cloudfunctions_function", "google_cloudfunctions2_function"):
            model.functions.append(
                CloudFunction(name=_s(attrs, "name") or rname, file=block.file, line=block.line)
            )

    if lakes and zones:
        # zone.lake references the lake resource (google_dataplex_lake.X
        # or the lake name) — explicit linkage, no guessing.
        for key, lake in list(lakes.items()):
            owned = [
                z for z, ref in zones if f"google_dataplex_lake.{key}." in ref or ref == lake.name
            ]
            lakes[key] = DataplexLake(
                name=lake.name,
                file=lake.file,
                line=lake.line,
                zones=tuple(sorted(owned)),
            )
    model.dataplex_lakes.extend(sorted(lakes.values(), key=lambda lake: lake.name))
    for items in (
        model.gcs_buckets,
        model.dataflow_jobs,
        model.dataproc_clusters,
        model.pubsub_topics,
        model.pubsub_subscriptions,
        model.composer_envs,
        model.functions,
    ):
        items.sort(key=lambda r: (str(r.file), r.line, r.name))

    setattr(ctx, _CACHE_ATTR, model)
    return model
