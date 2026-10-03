"""Versioned platform contract + architecture drift detection.

A ``platform-contract.yml`` declares *desired* architecture: pipelines with
compute platform/version, storage format, orchestration, SLA, semantics,
ownership, and an allowed-dependency list. :func:`detect_drift` compares the
contract against what is *declared* (IaC), *implemented* (code models), and
*runtime* (exported artifacts) and emits ``ArchitectureDrift`` records for
the ARCH001-008 families.

Everything is offline and deterministic; a missing contract means no drift,
not a violation.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from forge_doctor_data.core.models import Severity
from forge_doctor_data.core.runtime_evidence import RuntimeEvidenceModel

if TYPE_CHECKING:
    from forge_doctor_data.core.context import ProjectContext

_CONTRACT_NAMES = ("platform-contract.yml", "platform-contract.yaml")


def _load_yaml(text: str) -> tuple[Any, str | None]:
    """``yaml.safe_load`` when pyyaml is installed; else a strict subset.

    The fallback parses only the contract's documented shape: nested
    ``key:`` mappings, ``key: scalar``, block ``- item`` lists, and inline
    ``[a, b]`` lists. Anything else returns a parse error rather than a
    silently wrong document.
    """
    try:
        import yaml

        try:
            return yaml.safe_load(text), None
        except yaml.YAMLError as exc:
            return None, f"malformed YAML: {exc}"
    except ImportError:
        pass
    try:
        return _mini_yaml(text), None
    except ValueError as exc:
        return None, f"malformed YAML (minimal parser): {exc}"


def _mini_yaml(text: str) -> Any:
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        stripped = raw.split("#", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if not stripped.strip():
            continue
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise ValueError("tabs in indentation are unsupported")
        lines.append((len(raw) - len(raw.lstrip()), stripped.strip()))
    if not lines:
        return None
    value, pos = _mini_block(lines, 0, lines[0][0])
    if pos != len(lines):
        raise ValueError(f"unexpected content at line {pos + 1}")
    return value


def _mini_block(lines: list[tuple[int, str]], pos: int, indent: int) -> tuple[Any, int]:
    out: dict[str, Any] | list[Any] | None = None
    while pos < len(lines):
        ind, content = lines[pos]
        if ind < indent:
            break
        if ind > indent:
            raise ValueError(f"unexpected indentation at line {pos + 1}")
        if content.startswith("- "):
            if out is None:
                out = []
            if not isinstance(out, list):
                raise ValueError(f"cannot mix list items and mappings (line {pos + 1})")
            body = content[2:].strip()
            if _looks_like_mapping_item(body):
                # "- key: value" starts a mapping whose column is ind + 2;
                # rewrite the line in place and parse it as a block.
                lines[pos] = (ind + 2, body)
                child, pos = _mini_block(lines, pos, ind + 2)
                out.append(child)
                continue
            out.append(_mini_scalar(body))
            pos += 1
            continue
        if not isinstance(out, dict) and out is not None:
            raise ValueError(f"cannot mix mappings and list items (line {pos + 1})")
        if out is None:
            out = {}
        key, sep, rest = content.partition(":")
        if not sep:
            raise ValueError(f"expected 'key:' at line {pos + 1}")
        key = key.strip()
        rest = rest.strip()
        pos += 1
        if rest:
            out[key] = _mini_scalar(rest)
        elif pos < len(lines) and lines[pos][0] > indent:
            child, pos = _mini_block(lines, pos, lines[pos][0])
            out[key] = child
        else:
            out[key] = None
    return out, pos


def _looks_like_mapping_item(body: str) -> bool:
    """``- key:`` or ``- key: value`` opens a mapping item; URLs and
    other ``scheme://x`` scalars must not match (no ``': '`` boundary)."""
    if body.endswith(":"):
        return bool(re.match(r"^[\w.-]+$", body[:-1]))
    key, sep, _rest = body.partition(": ")
    return bool(sep) and bool(re.match(r"^[\w.-]+$", key))


def _mini_scalar(text: str) -> Any:
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        return text[1:-1]
    if text.startswith("'") and text.endswith("'") and len(text) >= 2:
        return text[1:-1]
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        return [_mini_scalar(p.strip()) for p in inner.split(",")] if inner else []
    if text.startswith("{") and text.endswith("}"):
        inner = text[1:-1].strip()
        out: dict[str, Any] = {}
        if not inner:
            return out
        for part in inner.split(","):
            key, sep, val = part.partition(":")
            if not sep:
                raise ValueError(f"bad inline mapping entry {part!r}")
            out[key.strip()] = _mini_scalar(val.strip())
        return out
    lowered = text.lower()
    if lowered in {"true", "yes"}:
        return True
    if lowered in {"false", "no"}:
        return False
    if lowered in {"null", "~"}:
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(s|sec|m|min|h|hr|d)?$")


def _duration_seconds(value: Any) -> float | None:
    """'30m' | '90s' | '1.5h' | '2d' | bare number (seconds) -> seconds."""
    if value is None:
        return None
    m = _DURATION_RE.match(str(value).strip().lower())
    if not m:
        return None
    amount = float(m.group(1))
    unit = m.group(2) or "s"
    mult = {"s": 1, "sec": 1, "m": 60, "min": 60, "h": 3600, "hr": 3600, "d": 86400}[unit]
    return amount * mult


@dataclass(frozen=True)
class PipelineContract:
    """Desired properties of one named pipeline."""

    name: str
    compute_platform: str | None = None
    compute_version: str | None = None
    storage_format: str | None = None
    orchestrator: str | None = None
    sla_seconds: float | None = None
    idempotent: bool | None = None
    owner: str | None = None
    approved_capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlatformContract:
    """Parsed ``platform-contract.yml`` - versioned declarative architecture."""

    contract_version: int
    path: Path
    pipelines: tuple[PipelineContract, ...] = ()
    datasets: tuple[str, ...] = ()
    declared_platforms: frozenset[str] = frozenset()
    allowed_dependencies: frozenset[str] = frozenset()
    owners: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def ok(self) -> bool:
        return not self.issues


def find_contract(root: Path) -> Path | None:
    for name in _CONTRACT_NAMES:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def load_contract(path: Path) -> PlatformContract:
    """Parse + validate a contract file. Issues are reported, never raised."""
    issues: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return PlatformContract(0, path, issues=(f"unreadable: {exc}",))
    doc, error = _load_yaml(text)
    if error:
        return PlatformContract(0, path, issues=(error,))
    if not isinstance(doc, dict):
        return PlatformContract(0, path, issues=("contract must be a mapping",))

    body = doc.get("platform-contract", doc)
    if not isinstance(body, dict):
        return PlatformContract(0, path, issues=("'platform-contract' must be a mapping",))
    version = doc.get("contract_version") or body.get("contract_version") or 1
    if not isinstance(version, int) or version < 1:
        issues.append(f"contract_version must be a positive int, got {version!r}")
        version = 1

    pipelines: list[PipelineContract] = []
    raw_pipelines = body.get("pipelines") or {}
    if not isinstance(raw_pipelines, dict):
        issues.append("'pipelines' must be a mapping of name -> contract")
        raw_pipelines = {}
    for name in sorted(raw_pipelines):
        spec = raw_pipelines[name]
        if not isinstance(spec, dict):
            issues.append(f"pipeline '{name}' must be a mapping")
            continue
        pipelines.append(_pipeline(str(name), spec, issues))

    raw_datasets = body.get("datasets") or {}
    datasets: list[str] = []
    if isinstance(raw_datasets, dict):
        datasets = sorted(str(k) for k in raw_datasets)
    elif isinstance(raw_datasets, list):
        datasets = sorted(str(d) for d in raw_datasets)
    elif raw_datasets:
        issues.append("'datasets' must be a mapping or list")

    governance = body.get("governance") or {}
    allowed: set[str] = set()
    if isinstance(governance, dict):
        for dep in governance.get("allowed_dependencies") or governance.get("dependencies") or []:
            allowed.add(str(dep).lower())
    declared: set[str] = set()
    for p in pipelines:
        for v in (p.compute_platform, p.storage_format, p.orchestrator):
            if v:
                declared.add(v.lower())
    owners = sorted({p.owner for p in pipelines if p.owner})
    if "contract_version" not in doc and "contract_version" not in body:
        issues.append("missing contract_version (assumed 1)")
    return PlatformContract(
        contract_version=version,
        path=path,
        pipelines=tuple(pipelines),
        datasets=tuple(datasets),
        declared_platforms=frozenset(declared),
        allowed_dependencies=frozenset(allowed),
        owners=tuple(owners),
        issues=tuple(issues),
        raw=body,
    )


def _pipeline(name: str, spec: dict[str, Any], issues: list[str]) -> PipelineContract:
    def section(label: str) -> dict[str, Any]:
        value = spec.get(label) or {}
        if not isinstance(value, dict):
            issues.append(f"pipeline '{name}': '{label}' must be a mapping")
            return {}
        return value

    compute = section("compute")
    storage = section("storage")
    orch = section("orchestration")
    sla = section("sla")
    sem = section("semantics")
    own = section("ownership")
    caps = section("capabilities")
    orchestrator = orch.get("engine") or orch.get("type")
    if orchestrator is None:
        for key in ("airflow", "controlm", "stepfunctions"):
            if orch.get(key) is True:
                orchestrator = key
                break
    sla_seconds = _duration_seconds(sla.get("duration"))
    if sla.get("duration") is not None and sla_seconds is None:
        issues.append(f"pipeline '{name}': malformed sla.duration {sla.get('duration')!r}")
    approved = caps.get("approved") or []
    return PipelineContract(
        name=name,
        compute_platform=_str(compute.get("platform")),
        compute_version=_str(compute.get("version")),
        storage_format=_str(storage.get("format")),
        orchestrator=_str(orchestrator),
        sla_seconds=sla_seconds,
        idempotent=sem.get("idempotent") if isinstance(sem.get("idempotent"), bool) else None,
        owner=_str(own.get("owner") or spec.get("owner")),
        approved_capabilities=tuple(sorted(str(c).lower() for c in approved)),
    )


def _str(v: Any) -> str | None:
    return str(v).strip() if v is not None and str(v).strip() else None


# --- drift --------------------------------------------------------------------


@dataclass(frozen=True)
class ArchitectureDrift:
    """One desired-vs-observed divergence, evidence-tagged."""

    check_id: str
    drift_type: str
    entity: str
    expected: str
    observed: str
    source: str
    severity: Severity
    evidence_kind: str  # static|config|observed_metadata|runtime|derived
    message: str

    @property
    def id(self) -> str:
        material = f"{self.check_id}|{self.entity}|{self.drift_type}|{self.observed}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:10]


def detect_drift(
    ctx: ProjectContext,
    contract: PlatformContract,
    runtime: list[RuntimeEvidenceModel] | None = None,
) -> list[ArchitectureDrift]:
    """Compare contract (desired) vs declared/implemented/runtime (observed)."""
    runtime = runtime or []
    drift: list[ArchitectureDrift] = []
    observed = _observed_platform(ctx)

    for pipe in contract.pipelines:
        drift.extend(_platform_drift(pipe, observed, runtime))
        drift.extend(_version_drift(pipe, observed))
        drift.extend(_format_drift(pipe, observed))
        drift.extend(_sla_drift(pipe, runtime))
        drift.extend(_idempotency_drift(pipe, observed))
        drift.extend(_capability_drift(ctx, pipe, observed))
    drift.extend(_undeclared_dependencies(contract, observed))
    drift.extend(_ownership_conflicts(ctx))
    return sorted(drift, key=lambda d: (d.check_id, d.entity, d.id))


# --- observed planes ------------------------------------------------------------


@dataclass(frozen=True)
class _Observed:
    """What the code/IaC/runtime planes actually use."""

    platforms: frozenset[str]  # glue|emr|databricks|spark|dynamodb|neptune|...
    glue_versions: tuple[str, ...]
    formats: frozenset[str]  # iceberg|parquet|delta
    dependencies: frozenset[str]  # service domains observed
    idempotent_sinks: frozenset[str]  # files with idempotent write evidence
    nonidempotent_sinks: frozenset[str]  # files with append-style writes
    orchestrators: frozenset[str]


def _observed_platform(ctx: ProjectContext) -> _Observed:
    from forge_doctor_data.analyzers.airflow_model import airflow_model
    from forge_doctor_data.analyzers.controlm_model import controlm_model
    from forge_doctor_data.analyzers.dynamodb_model import dynamodb_model
    from forge_doctor_data.analyzers.iceberg_model import iceberg_model
    from forge_doctor_data.analyzers.index import project_index
    from forge_doctor_data.analyzers.neptune_model import neptune_model
    from forge_doctor_data.analyzers.parquet_model import parquet_model
    from forge_doctor_data.analyzers.streaming_model import streaming_model
    from forge_doctor_data.analyzers.terraform_model import terraform_model

    platforms: set[str] = set()
    deps: set[str] = set()
    formats: set[str] = set()
    orchestrators: set[str] = set()
    versions: list[str] = []

    index = project_index(ctx)
    if any(m.uses_pyspark for m in index.modules.values()):
        platforms.add("spark")
        deps.add("spark")
    if any(m.uses_glue for m in index.modules.values()):
        platforms.add("glue")
        deps.add("glue")
    for m in index.modules.values():
        for call in m.calls:
            for k, v in call.kwargs:
                if k in _GLUE_VERSION_KWARGS and v:
                    versions.append(v)

    iceberg = iceberg_model(ctx)
    if iceberg.has_iceberg:
        formats.add("iceberg")
        deps.add("iceberg")
    if parquet_model(ctx).has_parquet:
        formats.add("parquet")
        deps.add("parquet")
    streams = streaming_model(ctx)
    if (
        "delta" in streams.sources
        or "delta" in streams.sinks
        or any(
            i.module == "delta" or i.module.startswith("delta.")
            for m in index.modules.values()
            for i in m.imports
        )
    ):
        formats.add("delta")
        deps.add("delta")
    if streams.has_streaming:
        deps.add("spark-streaming")
        deps.update(streams.sources)
        deps.update(streams.sinks)

    tf = terraform_model(ctx)
    for r in tf.resources:
        tf_type = r.labels[0] if r.labels else ""
        family = _TF_PLATFORM.get(tf_type)
        if family:
            platforms.add(family)
            deps.add(family)
            if tf_type == "aws_glue_job":
                ver = r.attrs.get("glue_version")
                if isinstance(ver, str):
                    versions.append(ver)

    if airflow_model(ctx).dags:
        orchestrators.add("airflow")
    if controlm_model(ctx).jobs:
        orchestrators.add("controlm")
    if any(r.labels and r.labels[0] == "aws_sfn_state_machine" for r in tf.resources):
        orchestrators.add("stepfunctions")

    ddb = dynamodb_model(ctx)
    if ddb.tables:
        deps.add("dynamodb")
        platforms.add("dynamodb")
    nep = neptune_model(ctx)
    if nep.clusters or nep.endpoints:
        deps.add("neptune")
        platforms.add("neptune")

    # idempotency evidence in write paths
    idempotent: set[str] = set()
    nonidem: set[str] = set()
    overwrite = re.compile(r"createorreplace|overwrite|merge|upsert", re.IGNORECASE)
    for ev in iceberg.by_kind("write_api"):
        (idempotent if overwrite.search(ev.value) else nonidem).add(ev.file.as_posix())
    for op in iceberg.operations:
        if op.name in {"merge", "update", "delete", "overwrite"}:
            idempotent.add(op.file.as_posix())
        elif op.name == "insert":
            nonidem.add(op.file.as_posix())
    return _Observed(
        platforms=frozenset(platforms),
        glue_versions=tuple(sorted(set(versions))),
        formats=frozenset(formats),
        dependencies=frozenset(deps),
        idempotent_sinks=frozenset(idempotent),
        nonidempotent_sinks=frozenset(nonidem - idempotent),
        orchestrators=frozenset(orchestrators),
    )


_GLUE_VERSION_KWARGS = {"glue_version", "glueversion"}


_TF_PLATFORM = {
    "aws_glue_job": "glue",
    "aws_glue_catalog_database": "glue",
    "aws_emr_cluster": "emr",
    "aws_emrserverless_application": "emr",
    "aws_dynamodb_table": "dynamodb",
    "aws_kinesis_stream": "kinesis",
    "aws_lambda_function": "lambda",
    "aws_sfn_state_machine": "stepfunctions",
    "aws_neptune_cluster": "neptune",
    "aws_msk_cluster": "kafka",
}


_RUNTIME_PLATFORM = {
    "spark_eventlog": "spark",
    "spark_ss_progress": "spark",
    "glue_logs": "glue",
    "athena_stats": "athena",
    "lambda_report": "lambda",
    "sfn_history": "stepfunctions",
    "neptune_explain": "neptune",
}


def _platform_drift(
    pipe: PipelineContract, observed: _Observed, runtime: list[RuntimeEvidenceModel]
) -> list[ArchitectureDrift]:
    """ARCH001: compute/runtime platform differs from contract."""
    if not pipe.compute_platform:
        return []
    want = pipe.compute_platform.lower()
    drift = []
    others = observed.platforms - {want} - {"spark"} if want != "spark" else set()
    # 'spark' is an engine, not a platform mismatch when glue/emr/databricks
    # declared. Flag only distinct platform families.
    platform_families = others - {"iceberg", "parquet", "delta"}
    for got in sorted(platform_families):
        if got in {"glue", "emr", "databricks", "lambda", "stepfunctions"} and got != want:
            drift.append(
                ArchitectureDrift(
                    "ARCH001",
                    "platform",
                    pipe.name,
                    want,
                    got,
                    "code",
                    Severity.WARNING,
                    "derived",
                    f"pipeline '{pipe.name}' declares platform '{want}' but code/IaC uses '{got}'",
                )
            )
    for m in runtime:
        rt = _RUNTIME_PLATFORM.get(m.source)
        if rt and rt != want and rt in {"glue", "emr", "athena", "lambda", "stepfunctions"}:
            rid = m.identifiers.get("job_name") or m.identifiers.get("app_name") or m.source
            drift.append(
                ArchitectureDrift(
                    "ARCH001",
                    "platform",
                    pipe.name,
                    want,
                    rt,
                    f"runtime:{m.artifact.name if m.artifact else m.source}",
                    Severity.WARNING,
                    "runtime",
                    f"pipeline '{pipe.name}' declares '{want}' but runtime artifact"
                    f" '{rid}' is {rt}",
                )
            )
    return drift


def _version_drift(pipe: PipelineContract, observed: _Observed) -> list[ArchitectureDrift]:
    """ARCH002: declared version differs from detected version."""
    if not pipe.compute_version:
        return []
    want = pipe.compute_version
    out = []
    for got in observed.glue_versions:
        if got != want:
            out.append(
                ArchitectureDrift(
                    "ARCH002",
                    "version",
                    pipe.name,
                    want,
                    got,
                    "config",
                    Severity.WARNING,
                    "config",
                    f"pipeline '{pipe.name}' wants {pipe.compute_platform} {want}"
                    f" but glue_version={got} is configured",
                )
            )
    return out


def _format_drift(pipe: PipelineContract, observed: _Observed) -> list[ArchitectureDrift]:
    """ARCH003: storage format differs from contract."""
    if not pipe.storage_format:
        return []
    want = pipe.storage_format.lower()
    out = []
    for got in sorted(observed.formats - {want}):
        out.append(
            ArchitectureDrift(
                "ARCH003",
                "storage-format",
                pipe.name,
                want,
                got,
                "code",
                Severity.WARNING,
                "derived",
                f"pipeline '{pipe.name}' declares storage '{want}' but '{got}'"
                " writes are implemented",
            )
        )
    return out


def _sla_drift(
    pipe: PipelineContract, runtime: list[RuntimeEvidenceModel]
) -> list[ArchitectureDrift]:
    """ARCH005: runtime duration violates the contracted SLA."""
    if pipe.sla_seconds is None:
        return []
    out = []
    for m in runtime:
        tokens = {v.lower() for v in m.identifiers.values()} | {e.id.lower() for e in m.executions}
        if pipe.name.lower() not in tokens and not any(pipe.name.lower() in t for t in tokens):
            continue
        for ex in m.executions:
            if ex.duration_ms and ex.duration_ms > pipe.sla_seconds * 1000:
                out.append(
                    ArchitectureDrift(
                        "ARCH005",
                        "sla",
                        pipe.name,
                        f"<= {pipe.sla_seconds / 60:g}m",
                        f"{ex.duration_ms / 60000:.1f}m ({ex.id})",
                        f"runtime:{m.artifact.name if m.artifact else m.source}",
                        Severity.ERROR,
                        "runtime",
                        f"pipeline '{pipe.name}' SLA {pipe.sla_seconds / 60:g}m violated:"
                        f" {ex.id} ran {ex.duration_ms / 60000:.1f}m",
                    )
                )
    return out


def _idempotency_drift(pipe: PipelineContract, observed: _Observed) -> list[ArchitectureDrift]:
    """ARCH006: contract demands idempotency; writes show none."""
    if not pipe.idempotent:
        return []
    if not observed.nonidempotent_sinks:
        return []
    return [
        ArchitectureDrift(
            "ARCH006",
            "idempotency",
            pipe.name,
            "idempotent writes",
            f"append-style writes without dedup in {len(observed.nonidempotent_sinks)} file(s)",
            "code",
            Severity.WARNING,
            "derived",
            f"pipeline '{pipe.name}' contracts idempotent=true but write paths"
            " show no merge/upsert/dedup evidence",
        )
    ]


def _ownership_conflicts(ctx: ProjectContext) -> list[ArchitectureDrift]:
    """ARCH007: same resource provisioned by multiple owners.

    Owners observed: terraform (``resource`` blocks), manual (boto3
    ``create_*`` calls bound to a ``boto3.client("<service>")`` receiver),
    and airflow provisioning operators. Resources key on
    ``(service, cloud-name)`` — the same name created by two systems is
    the conflict.
    """
    from forge_doctor_data.analyzers.index import project_index
    from forge_doctor_data.analyzers.terraform_model import terraform_model

    owners: dict[tuple[str, str], dict[str, str]] = {}
    tf = terraform_model(ctx)
    for r in tf.resources:
        if not r.labels:
            continue
        spec = _TF_OWNERSHIP.get(r.labels[0])
        if not spec:
            continue
        service, name_attr = spec
        name = r.attrs.get(name_attr) or r.labels[-1]
        owners.setdefault((service, str(name)), {})["terraform"] = r.address

    index = project_index(ctx)
    for file, module in sorted(index.modules.items(), key=lambda kv: kv[0].as_posix()):
        if not any(i.module == "boto3" or i.module.startswith("boto3") for i in module.imports):
            continue
        # receiver var -> aws service, from ``x = boto3.client("svc")``
        clients: dict[str, str] = {}
        for assign in module.assigns:
            if not (assign.value_call and assign.value_call.endswith("client")):
                continue
            for call in module.calls:
                if call.line == assign.line and call.name == "client" and call.args:
                    clients[assign.target] = call.args[0]
                    break
        for call in module.calls:
            if call.name not in _BOTO3_OWNERSHIP or call.receiver not in clients:
                continue
            service = clients[call.receiver]
            expected_service, name_kwarg = _BOTO3_OWNERSHIP[call.name]
            if service != expected_service:
                continue
            name = dict(call.kwargs).get(name_kwarg)
            if not name:
                continue
            owners.setdefault((service, name), {}).setdefault(
                "manual", f"{file.as_posix()}:{call.line}"
            )
    out = []
    for (service, name), systems in sorted(owners.items()):
        if len(systems) < 2:
            continue
        out.append(
            ArchitectureDrift(
                "ARCH007",
                "ownership",
                f"{service}:{name}",
                "single provisioning owner",
                " + ".join(sorted(systems)),
                ",".join(sorted(systems.values())),
                Severity.WARNING,
                "derived",
                f"{service} '{name}' is provisioned by multiple owners:"
                f" {', '.join(sorted(systems))}",
            )
        )
    return out


# tf resource type -> (service, attr naming the cloud resource)
_TF_OWNERSHIP = {
    "aws_glue_job": ("glue", "name"),
    "aws_dynamodb_table": ("dynamodb", "name"),
    "aws_s3_bucket": ("s3", "bucket"),
    "aws_lambda_function": ("lambda", "function_name"),
    "aws_kinesis_stream": ("kinesis", "name"),
    "aws_sfn_state_machine": ("stepfunctions", "name"),
    "aws_neptune_cluster": ("neptune", "cluster_identifier"),
    "aws_glue_catalog_database": ("glue", "name"),
}

# boto3 dotted call -> (service, kwarg naming the created resource)
_BOTO3_OWNERSHIP = {
    "create_job": ("glue", "Name"),
    "create_table": ("dynamodb", "TableName"),
    "create_bucket": ("s3", "Bucket"),
    "create_function": ("lambda", "FunctionName"),
    "create_stream": ("kinesis", "StreamName"),
    "create_state_machine": ("stepfunctions", "name"),
    "create_dbcluster": ("neptune", "DBClusterIdentifier"),
}


def _undeclared_dependencies(
    contract: PlatformContract, observed: _Observed
) -> list[ArchitectureDrift]:
    """ARCH004: observed platform dependency absent from contract."""
    if not contract.allowed_dependencies:
        return []
    out = []
    for dep in sorted(observed.dependencies - contract.allowed_dependencies):
        out.append(
            ArchitectureDrift(
                "ARCH004",
                "dependency",
                dep,
                f"subset of {sorted(contract.allowed_dependencies)}",
                dep,
                "code",
                Severity.WARNING,
                "derived",
                f"'{dep}' is used by code/IaC but absent from governance.allowed_dependencies",
            )
        )
    return out


def _capability_drift(
    ctx: ProjectContext, pipe: PipelineContract, observed: _Observed
) -> list[ArchitectureDrift]:
    """ARCH008: implemented features outside the pipeline's approved list."""
    if not pipe.approved_capabilities:
        return []
    out = []
    if observed.nonidempotent_sinks and "idempotent_writes" not in pipe.approved_capabilities:
        pass  # covered by ARCH006
    used_features: set[str] = set()
    if "iceberg" in observed.formats:
        used_features.add("iceberg")
    if "parquet" in observed.formats:
        used_features.add("parquet")
    for dep in observed.dependencies:
        used_features.add(dep)
    for feat in sorted(used_features - set(pipe.approved_capabilities) - {"spark"}):
        if feat in (pipe.compute_platform or "", pipe.storage_format or ""):
            continue
        out.append(
            ArchitectureDrift(
                "ARCH008",
                "capability",
                pipe.name,
                f"approved: {sorted(pipe.approved_capabilities)}",
                feat,
                "code",
                Severity.INFO,
                "derived",
                f"pipeline '{pipe.name}' uses '{feat}' which is not in its"
                " approved capabilities list",
            )
        )
    return out
