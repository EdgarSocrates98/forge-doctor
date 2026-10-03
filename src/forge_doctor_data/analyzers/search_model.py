"""Search/indexing platform model — OpenSearch + Elasticsearch (spec 220).

Evidence planes:

- **Index templates / mappings / settings JSON** — claimed only on
  compound evidence (filename convention *and* key shape): filenames
  containing `index-template`/`template`/`mapping`/`settings`, or JSON
  carrying `index_patterns`, `mappings` (+ `properties`/`dynamic`),
  `settings.index.*`, `template` with settings/mappings. A bare
  ``{"mappings": ...}`` key never attributes (adversarial lab pins it).
- **ISM/ILM policies JSON** — `policy` with `states` (ISM/OpenSearch)
  or `phases` (ILM/Elasticsearch); filename `*policy*`/`ism`/`ilm`.
- **Ingest pipelines** — JSON `processors` list with processor `type`s.
- **Terraform** — `aws_opensearch_*`/`aws_elasticsearch_domain`/
  `elasticsearch_*`/`opensearch_*` resources (encryption, TLS attrs).
- **Observed cluster exports** — JSON under `opensearch/`,
  `elasticsearch/`/`elastic/`, or `.forge-doctor-data/evidence/` carrying
  `cluster_name`+`status`/`number_of_nodes` or `persistent`/`transient`
  settings shapes.

Vendor attribution: ISM keys → ``opensearch``; ILM keys →
``elasticsearch``; Terraform resource prefix decides; otherwise the
generic ``search`` vendor marker is used (checks stay vendor-agnostic —
shared ``SRCH`` prefix per the spec's open-question decision).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from forge_doctor_data.core.context import ProjectContext

_CACHE_ATTR = "_forge_doctor_data_search_model"


@dataclass(frozen=True)
class SearchIndex:
    """One index template / concrete index with mapping+settings."""

    vendor: str  # opensearch | elasticsearch | search
    name: str
    file: Path
    line: int = 0
    kind: str = "template"  # template | index
    index_patterns: tuple[str, ...] = ()
    shards: str = ""
    replicas: str = ""
    dynamic: str = ""  # mappings.dynamic when declared
    field_types: tuple[str, ...] = ()  # mapping leaf types seen
    object_keys: tuple[str, ...] = ()  # nested objects without enabled:false
    props: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class SearchPolicy:
    """One ISM/ILM lifecycle policy."""

    vendor: str
    name: str
    file: Path
    kind: str  # ism | ilm
    index_patterns: tuple[str, ...] = ()
    has_rollover: bool = False
    has_retention: bool = False  # delete phase / delete state


@dataclass(frozen=True)
class IngestPipeline:
    """One ingest pipeline definition."""

    vendor: str
    name: str
    file: Path
    processors: tuple[str, ...] = ()


@dataclass(frozen=True)
class SearchDomain:
    """A Terraform-managed search domain/cluster."""

    vendor: str
    name: str
    file: Path
    line: int
    address: str
    encryption_at_rest: str = ""  # true|false|""
    node_to_node: str = ""
    https_tls: str = ""  # enforce_https / tls security policy
    props: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ObservedRow:
    """One row from an exported cluster-settings artifact."""

    vendor: str
    file: Path
    fields: tuple[tuple[str, str], ...]

    def get(self, *keys: str) -> str:
        d = {k.lower(): v for k, v in self.fields}
        for key in keys:
            if key.lower() in d:
                return d[key.lower()]
        return ""


@dataclass
class SearchPlatformModel:
    """All search-platform evidence for a project."""

    indices: list[SearchIndex] = field(default_factory=list)
    policies: list[SearchPolicy] = field(default_factory=list)
    pipelines: list[IngestPipeline] = field(default_factory=list)
    domains: list[SearchDomain] = field(default_factory=list)
    observed: list[ObservedRow] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)

    @property
    def has_evidence(self) -> bool:
        return bool(self.indices or self.policies or self.pipelines or self.domains)


# ---------------------------------------------------------------------------
# JSON evidence attribution


_SEARCH_NAME_HINT = re.compile(
    r"index[-_]?template|composable|component[-_]?template|mapping|"
    r"pipeline|ism|ilm|policy|opensearch|elastic|search",
    re.IGNORECASE,
)
_SEARCH_DIR = re.compile(r"(?:^|[/\\])(?:opensearch|elastic(?:search)?)(?:$|[/\\])", re.I)


def _vendor_from(doc: dict[str, Any], rel: Path) -> str:
    """Positive vendor attribution, else the generic 'search' marker."""
    text = json.dumps(doc).lower()
    if "ism_template" in text or '"states"' in text:
        return "opensearch"
    if '"phases"' in text or "_ilm" in text:
        return "elasticsearch"
    for part in rel.parts:
        low = part.lower()
        if "opensearch" in low:
            return "opensearch"
        if "elastic" in low:
            return "elasticsearch"
    return "search"


def _is_index_doc(doc: dict[str, Any]) -> bool:
    """Template/index shape: index_patterns, template{settings|mappings},
    top-level settings.index.*, or mappings with properties/dynamic."""
    if "index_patterns" in doc:
        return True
    if isinstance(doc.get("template"), dict) and any(
        k in doc["template"] for k in ("settings", "mappings", "aliases")
    ):
        return True
    settings = doc.get("settings")
    if isinstance(settings, dict) and any(
        str(k).startswith("index.") or k == "index" for k in settings
    ):
        return True
    mappings = doc.get("mappings")
    return isinstance(mappings, dict) and any(k in mappings for k in ("properties", "dynamic"))


def _is_policy_doc(doc: dict[str, Any]) -> bool:
    pol = doc.get("policy", doc)
    if not isinstance(pol, dict):
        return False
    return "ism_template" in pol or "states" in pol or "phases" in pol


def _is_pipeline_doc(doc: dict[str, Any]) -> bool:
    return isinstance(doc.get("processors"), list) and bool(doc["processors"])


def _field_types(mappings: Any) -> tuple[list[str], list[str], str]:
    """(leaf types, object keys without enabled:false, dynamic setting)."""
    types: list[str] = []
    objects: list[str] = []
    dynamic = ""
    if not isinstance(mappings, dict):
        return types, objects, dynamic
    if isinstance(mappings.get("dynamic"), (str, bool)):
        dynamic = str(mappings["dynamic"]).lower()

    def walk(node: Any, path: str) -> None:
        if not isinstance(node, dict):
            return
        props = node.get("properties")
        if isinstance(props, dict):
            for key, spec in props.items():
                if not isinstance(spec, dict):
                    continue
                t = spec.get("type", "")
                child_path = f"{path}.{key}" if path else str(key)
                if t:
                    types.append(str(t))
                    if t in ("object", "nested") and spec.get("enabled") is not False:
                        objects.append(child_path)
                elif "properties" in spec or "fields" in spec:
                    # untyped object (implicit object) — same risk class
                    if spec.get("enabled") is not False:
                        objects.append(child_path)
                walk(spec, child_path)

    walk(mappings, "")
    return sorted(set(types)), objects, dynamic


def _scan_json_evidence(ctx: ProjectContext, model: SearchPlatformModel) -> None:
    for rel in sorted(ctx.files):
        if rel.suffix.lower() != ".json":
            continue
        posix = rel.as_posix()
        name_hint = bool(_SEARCH_NAME_HINT.search(posix)) or bool(_SEARCH_DIR.search(posix))
        text = ctx.read_text(rel)
        if text is None:
            continue
        # cheap pre-filter: only parse JSON when it could plausibly hold
        # search keys OR the filename already hints (compound evidence)
        if not name_hint and not any(
            k in text for k in ("index_patterns", '"mappings"', '"processors"', '"policy"')
        ):
            continue
        try:
            doc = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(doc, dict):
            continue
        claimed = False
        vendor = _vendor_from(doc, rel)

        if _is_index_doc(doc) and (
            name_hint or "index_patterns" in doc or _SEARCH_DIR.search(posix)
        ):
            kind = "template" if ("index_patterns" in doc or "template" in doc) else "index"
            body = doc.get("template", doc)
            settings = body.get("settings", {}) if isinstance(body, dict) else {}
            if isinstance(settings, dict) and isinstance(settings.get("index"), dict):
                settings = settings["index"]
            mappings = body.get("mappings", {}) if isinstance(body, dict) else {}
            types, objects, dynamic = _field_types(mappings)
            raw_patterns = body.get("index_patterns", doc.get("index_patterns", []))
            if isinstance(raw_patterns, str):
                raw_patterns = [raw_patterns]
            model.indices.append(
                SearchIndex(
                    vendor=vendor,
                    name=str(doc.get("name", rel.stem)),
                    file=rel,
                    kind=kind,
                    index_patterns=tuple(str(p) for p in raw_patterns if isinstance(p, str)),
                    shards=str(settings.get("number_of_shards", ""))
                    if isinstance(settings, dict)
                    else "",
                    replicas=str(settings.get("number_of_replicas", ""))
                    if isinstance(settings, dict)
                    else "",
                    dynamic=dynamic,
                    field_types=tuple(types),
                    object_keys=tuple(objects),
                )
            )
            claimed = True

        if _is_policy_doc(doc) and (name_hint or "policy" in doc):
            pol = doc.get("policy", doc)
            ism = "ism_template" in pol or "states" in pol
            patterns: list[str] = []
            tmpl = pol.get("ism_template")
            if isinstance(tmpl, dict):
                pats = tmpl.get("index_patterns", [])
                patterns = [str(p) for p in pats] if isinstance(pats, list) else [str(pats)]
            rollover = "rollover" in json.dumps(pol).lower()
            retention = bool(
                re.search(r'"delete"', json.dumps(pol))
                or re.search(r'"min_age"[^}]*delete', json.dumps(pol))
            )
            model.policies.append(
                SearchPolicy(
                    vendor="opensearch" if ism else "elasticsearch",
                    name=str(doc.get("policy_id", doc.get("name", rel.stem))),
                    file=rel,
                    kind="ism" if ism else "ilm",
                    index_patterns=tuple(patterns),
                    has_rollover=rollover,
                    has_retention=retention,
                )
            )
            claimed = True

        if _is_pipeline_doc(doc) and name_hint:
            procs = tuple(
                sorted({str(k) for p in doc["processors"] if isinstance(p, dict) for k in p})
            )
            model.pipelines.append(
                IngestPipeline(vendor=vendor, name=rel.stem, file=rel, processors=procs)
            )
            claimed = True

        if not claimed and _SEARCH_DIR.search(posix):
            model.unparsed.append(posix)


# ---------------------------------------------------------------------------
# Terraform


_TF_VENDOR = (
    (re.compile(r"aws_opensearch"), "opensearch"),
    (re.compile(r"aws_elasticsearch"), "elasticsearch"),
    (re.compile(r"^opensearch"), "opensearch"),
    (re.compile(r"^elasticsearch"), "elasticsearch"),
)

# Whole-domain/cluster resource types — index/role/policy sub-resources
# (aws_opensearch_domain_policy et al.) are not domain surfaces.
_TF_DOMAIN_TYPES = {
    "aws_opensearch_domain",
    "aws_elasticsearch_domain",
    "elasticsearch_domain",
    "opensearch_domain",
    "aws_opensearchserverless_collection",
    "aws_elasticsearch_cluster",
}


def _scan_terraform(ctx: ProjectContext, model: SearchPlatformModel) -> None:
    from forge_doctor_data.analyzers.terraform_model import terraform_model

    for b in terraform_model(ctx).resources:
        if b.kind != "resource" or len(b.labels) < 2:
            continue
        rtype, name = b.labels[0], b.labels[1]
        if rtype not in _TF_DOMAIN_TYPES:
            continue
        vendor = next((v for pat, v in _TF_VENDOR if pat.search(rtype)), "search")
        enc = re.search(
            r"encrypt_at_rest\s*\{[^}]*enabled\s*=\s*(true|false)", b.body, re.IGNORECASE
        ) or re.search(
            r"encryption_at_rest\s*\{[^}]*enabled\s*=\s*(true|false)",
            b.body,
            re.IGNORECASE,
        )
        n2n = re.search(
            r"node_to_node_encryption\s*\{[^}]*enabled\s*=\s*(true|false)",
            b.body,
            re.IGNORECASE,
        )
        tls = re.search(
            r"(?:enforce_https|tls_security_policy)\s*=\s*(\"?[\w.-]+\"?)",
            b.body,
            re.IGNORECASE,
        )
        model.domains.append(
            SearchDomain(
                vendor=vendor,
                name=name,
                file=b.file,
                line=b.line,
                address=b.address,
                encryption_at_rest=enc.group(1).lower() if enc else "",
                node_to_node=n2n.group(1).lower() if n2n else "",
                https_tls=tls.group(1).strip('"').lower() if tls else "",
            )
        )


# ---------------------------------------------------------------------------
# Observed exports


_OBSERVED_DIRS = re.compile(
    r"(?:^|[/\\])(?:opensearch|elastic(?:search)?|\.forge-doctor-data[/\\]evidence)(?:$|[/\\])",
    re.IGNORECASE,
)
_OBSERVED_SIGNAL = re.compile(
    r"cluster_name|number_of_nodes|number_of_data_nodes|active_shards|"
    r"persistent|transient|status",
    re.IGNORECASE,
)


def _scan_observed(model: SearchPlatformModel, ctx: ProjectContext) -> None:
    for rel in sorted(ctx.files):
        if rel.suffix.lower() != ".json" or not _OBSERVED_DIRS.search(rel.as_posix()):
            continue
        text = ctx.read_text(rel)
        if text is None:
            continue
        try:
            doc = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            model.unparsed.append(rel.as_posix())
            continue
        rows: list[dict[str, Any]]
        if isinstance(doc, dict):
            rows = (
                [doc]
                if all(isinstance(v, (str, int, float, bool)) for v in doc.values())
                else [r for r in doc.values() if isinstance(r, dict)]
            )
        elif isinstance(doc, list):
            rows = [r for r in doc if isinstance(r, dict)]
        else:
            rows = []
        dir_vendor = ""
        for part in rel.parts:
            low = part.lower()
            if "opensearch" in low:
                dir_vendor = "opensearch"
            elif "elastic" in low:
                dir_vendor = "elasticsearch"
        claimed = False
        for row in rows:
            keys = {str(k).lower() for k in row}
            if not any(_OBSERVED_SIGNAL.search(k) for k in keys):
                continue
            model.observed.append(
                ObservedRow(
                    dir_vendor or "search",
                    rel,
                    tuple(sorted((str(k), str(v)) for k, v in row.items())),
                )
            )
            claimed = True
        if not claimed:
            model.unparsed.append(rel.as_posix())


# ---------------------------------------------------------------------------
# Model entry point


def search_model(ctx: ProjectContext) -> SearchPlatformModel:
    """Memoized search-platform model over ctx evidence."""
    cached = getattr(ctx, _CACHE_ATTR, None)
    if cached is not None:
        return cast(SearchPlatformModel, cached)
    model = SearchPlatformModel()
    _scan_json_evidence(ctx, model)
    _scan_terraform(ctx, model)
    if model.has_evidence:
        _scan_observed(model, ctx)
    setattr(ctx, _CACHE_ATTR, model)
    return model
