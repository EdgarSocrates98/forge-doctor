"""dbt vendor model — transformation-layer evidence (spec 216).

Evidence planes:

- **`dbt_project.yml`** — the defining artifact: project name, profile,
  ``model-paths``/``source-paths`` dirs. No project file → no dbt
  evidence (the adversarial lab pins this).
- **Model SQL** — ``<model_dir>/**/*.sql``: materialization from
  ``{{ config(materialized=...) }}``, ``unique_key``, ``ref()`` and
  ``source()`` edges, macro invocations.
- **`schema.yml`/`*.yml` under model dirs** — declared tests
  (unique/not_null/relationships/accepted_values), descriptions,
  sources + freshness, exposures.
- **`seeds/`, `snapshots/`, `tests/`, `macros/`** — file presence.
- **`profiles.yml`** — profile names + target **key names** only
  (``type``/``host``/``pass``/...); values are never read into the
  model, so env-var or literal secrets cannot leak into findings.
- **`target/manifest.json` / `run_results.json`** — observed compiled
  graph + run outcomes (never produced, only read).

Constraints: no ``dbt compile``/``dbt run``; profiles.yml contributes
key names only — secret values are never surfaced.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext

_CACHE_ATTR = "_forge_doctor_dbt_model"

# ---------------------------------------------------------------------------
# Model rows


@dataclass(frozen=True)
class DbtModel:
    """A `.sql` model under a model dir."""

    name: str  # stem, e.g. "stg_orders"
    file: Path
    materialized: str = ""  # table|view|incremental|ephemeral|"" (unknown)
    unique_key: str = ""
    refs: tuple[str, ...] = ()
    sources_used: tuple[str, ...] = ()  # "source.table"
    macros: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()  # test names from schema.yml
    has_description: bool = False


@dataclass(frozen=True)
class DbtSource:
    """A `sources:` table declared in schema yml."""

    name: str  # "source_name.table_name"
    file: Path
    has_freshness: bool = False
    has_tests: bool = False
    has_description: bool = False


@dataclass(frozen=True)
class DbtExposure:
    name: str
    type: str  # dashboard|notebook|ml|...
    file: Path
    depends_on: tuple[str, ...] = ()


@dataclass
class DbtProjectModel:
    """All dbt evidence for a project (one repo may host several roots)."""

    project_roots: list[Path] = field(default_factory=list)  # dbt_project.yml dirs
    project_name: str = ""
    profile: str = ""
    profile_names: list[str] = field(default_factory=list)  # profiles.yml entries
    profile_keys: tuple[str, ...] = ()  # target key names only, never values
    model_dirs: tuple[str, ...] = ("models",)
    models: list[DbtModel] = field(default_factory=list)
    sources: list[DbtSource] = field(default_factory=list)
    exposures: list[DbtExposure] = field(default_factory=list)
    seeds: list[str] = field(default_factory=list)
    snapshots: list[str] = field(default_factory=list)
    singular_tests: list[str] = field(default_factory=list)  # tests/*.sql
    macros_defined: list[str] = field(default_factory=list)
    manifest_nodes: int = 0  # observed compiled graph size
    run_results: list[tuple[str, str, float]] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)

    @property
    def has_evidence(self) -> bool:
        return bool(self.project_roots)

    def materialized_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for m in self.models:
            out[m.materialized or "unknown"] = out.get(m.materialized or "unknown", 0) + 1
        return dict(sorted(out.items()))

    def doc_coverage(self) -> float:
        """Fraction of models carrying a description (0..1)."""
        if not self.models:
            return 1.0
        return sum(1 for m in self.models if m.has_description) / len(self.models)


# ---------------------------------------------------------------------------
# YAML — reuse the contract loader (pyyaml when present, mini fallback).

_JINJA_CALL = re.compile(r"\{\{\s*([\w.]+)\s*\(", re.IGNORECASE)
_REF_RE = re.compile(r"\bref\s*\(\s*['\"]([\w.]+)['\"]", re.IGNORECASE)
_SOURCE_RE = re.compile(
    r"\bsource\s*\(\s*['\"]([\w.]+)['\"]\s*,\s*['\"]([\w.]+)['\"]", re.IGNORECASE
)
_CONFIG_MAT = re.compile(
    r"config\s*\([^)]*materialized\s*=\s*['\"](\w+)['\"]", re.IGNORECASE | re.DOTALL
)
_CONFIG_UNIQUE = re.compile(
    r"config\s*\([^)]*unique_key\s*=\s*['\"]([\w.]+)['\"]", re.IGNORECASE | re.DOTALL
)
_MACRO_DEF = re.compile(r"\{%\s*macro\s+(\w+)", re.IGNORECASE)
_BUILTINS = {
    "ref",
    "source",
    "config",
    "var",
    "env_var",
    "is_incremental",
    "this",
    "target",
    "run_started_at",
    "invocation_id",
    "adapter",
    "store_result",
    "return",
    "dbt",
    "modules",
    "log",
    "if",
    "for",
    "set",
}


def _yml(doc: Any) -> dict[str, Any]:
    return doc if isinstance(doc, dict) else {}


def _yml_list(doc: Any, key: str) -> list[dict[str, Any]]:
    val = _yml(doc).get(key)
    if isinstance(val, list):
        return [v for v in val if isinstance(v, dict)]
    return []


def _load(ctx: ProjectContext, rel: Path) -> Any:
    from forge_doctor.core.contract import _load_yaml

    text = ctx.read_text(rel)
    if text is None:
        return None
    doc, err = _load_yaml(text)
    return None if err else doc


# ---------------------------------------------------------------------------
# Project roots + dbt_project.yml


def _project_files(ctx: ProjectContext) -> list[Path]:
    return [rel for rel in sorted(ctx.files) if rel.name in {"dbt_project.yml", "dbt_project.yaml"}]


def _model_dirs_for(root: Path, doc: dict[str, Any]) -> tuple[str, ...]:
    raw = doc.get("model-paths") or doc.get("source-paths") or ["models"]
    dirs = [str(d) for d in raw if isinstance(d, str)] if isinstance(raw, list) else []
    return tuple(dirs) or ("models",)


def _dirs_under(root: Path, dirs: tuple[str, ...]) -> tuple[Path, ...]:
    return tuple(root / d for d in dirs)


def _in_dirs(rel: Path, dirs: tuple[Path, ...]) -> bool:
    return any(rel.is_relative_to(d) for d in dirs)


def _scan_profiles(model: DbtProjectModel, ctx: ProjectContext, root: Path) -> None:
    """``profiles.yml`` next to the project: names + key names, no values."""
    keys: set[str] = set(model.profile_keys)
    for fname in ("profiles.yml", "profiles.yaml"):
        rel = root / fname
        if rel not in ctx.files:
            continue
        doc = _yml(_load(ctx, rel))
        for pname, pdef in doc.items():
            if not isinstance(pdef, dict):
                continue
            model.profile_names.append(str(pname))
            outputs = pdef.get("outputs")
            if isinstance(outputs, dict):
                for target in outputs.values():
                    if isinstance(target, dict):
                        keys.update(str(k) for k in target)
    model.profile_keys = tuple(sorted(keys))


# ---------------------------------------------------------------------------
# Model SQL — jinja surface (config/refs/sources/macros)


def _scan_model_sql(
    text: str,
) -> tuple[str, str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    mat = _CONFIG_MAT.search(text)
    uniq = _CONFIG_UNIQUE.search(text)
    refs = tuple(sorted(set(_REF_RE.findall(text))))
    sources = tuple(sorted({f"{s}.{t}" for s, t in _SOURCE_RE.findall(text)}))
    macros = tuple(sorted({m for m in _JINJA_CALL.findall(text) if m.lower() not in _BUILTINS}))
    return (
        mat.group(1).lower() if mat else "",
        uniq.group(1) if uniq else "",
        refs,
        sources,
        macros,
    )


# ---------------------------------------------------------------------------
# schema.yml — models/tests/sources/exposures

_TEST_KEYS = {"tests", "data_tests"}


def _schema_model_tests(doc: dict[str, Any]) -> tuple[dict[str, list[str]], set[str]]:
    """``models:`` entries -> per-model test names + described flag."""
    tests_map: dict[str, list[str]] = {}
    described: set[str] = set()
    for entry in _yml_list(doc, "models"):
        name = str(entry.get("name") or "")
        if not name:
            continue
        if entry.get("description"):
            described.add(name)
        tests = [
            str(t) if isinstance(t, str) else next(iter(t), "")
            for key in _TEST_KEYS
            for t in (entry.get(key) or [])
            if isinstance(t, (str, dict))
        ]
        tests += [
            str(t) if isinstance(t, str) else next(iter(t), "")
            for col in entry.get("columns") or []
            if isinstance(col, dict)
            for key in _TEST_KEYS
            for t in (col.get(key) or [])
            if isinstance(t, (str, dict))
        ]
        tests_map[name] = [t for t in tests if t]
    return tests_map, described


def _scan_schema(model: DbtProjectModel, rel: Path, doc: dict[str, Any]) -> None:
    for src in _yml_list(doc, "sources"):
        src_name = str(src.get("name") or "")
        src_fresh = bool(src.get("freshness"))
        src_desc = bool(src.get("description"))
        src_tests = any(
            bool(t.get("tests") or t.get("data_tests"))
            for t in src.get("tables") or []
            if isinstance(t, dict)
        )
        for tbl in src.get("tables") or []:
            if not isinstance(tbl, dict):
                continue
            tname = str(tbl.get("name") or "")
            if not tname:
                continue
            model.sources.append(
                DbtSource(
                    f"{src_name}.{tname}",
                    rel,
                    has_freshness=src_fresh or bool(tbl.get("freshness")),
                    has_tests=src_tests or bool(tbl.get("tests") or tbl.get("data_tests")),
                    has_description=src_desc or bool(tbl.get("description")),
                )
            )
    for ex in _yml_list(doc, "exposures"):
        deps = [
            str(d).replace("ref('", "").rstrip("')")
            for d in ex.get("depends_on") or []
            if isinstance(d, str)
        ]
        model.exposures.append(
            DbtExposure(str(ex.get("name") or ""), str(ex.get("type") or ""), rel, tuple(deps))
        )


def _attach_schema(
    model: DbtProjectModel,
    schema_tests: dict[str, list[str]],
    schema_described: set[str],
) -> None:
    out: list[DbtModel] = []
    for m in model.models:
        out.append(
            DbtModel(
                m.name,
                m.file,
                m.materialized,
                m.unique_key,
                m.refs,
                m.sources_used,
                m.macros,
                tuple(schema_tests.get(m.name, ())),
                m.has_description or m.name in schema_described,
            )
        )
    model.models = out


# ---------------------------------------------------------------------------
# Observed artifacts — manifest.json / run_results.json under target/


def _from_observed(model: DbtProjectModel, ctx: ProjectContext) -> None:
    for rel in sorted(ctx.files):
        if rel.name == "manifest.json" and "target" in rel.parts:
            text = ctx.read_text(rel)
            if text is None:
                continue
            try:
                doc = json.loads(text)
            except json.JSONDecodeError:
                model.unparsed.append(rel.as_posix())
                continue
            nodes = doc.get("nodes") if isinstance(doc, dict) else None
            if isinstance(nodes, dict):
                model.manifest_nodes += len(nodes)
        elif rel.name == "run_results.json" and "target" in rel.parts:
            text = ctx.read_text(rel)
            if text is None:
                continue
            try:
                doc = json.loads(text)
            except json.JSONDecodeError:
                model.unparsed.append(rel.as_posix())
                continue
            for r in (doc or {}).get("results") or []:
                if isinstance(r, dict):
                    model.run_results.append(
                        (
                            str(r.get("unique_id") or ""),
                            str(r.get("status") or ""),
                            float(r.get("execution_time") or 0.0),
                        )
                    )


def dbt_model(ctx: ProjectContext) -> DbtProjectModel:
    """Memoized dbt model over ctx evidence."""
    cached = getattr(ctx, _CACHE_ATTR, None)
    if cached is not None:
        return cast(DbtProjectModel, cached)
    model = DbtProjectModel()
    roots = _project_files(ctx)
    if not roots:
        setattr(ctx, _CACHE_ATTR, model)
        return model
    model.project_roots = [p.parent for p in roots]
    model_dirs: list[str] = []
    schema_tests: dict[str, list[str]] = {}
    schema_described: set[str] = set()

    for proj in roots:
        root = proj.parent
        doc = _yml(_load(ctx, proj))
        if not model.project_name:
            model.project_name = str(doc.get("name") or "")
            model.profile = str(doc.get("profile") or "")
        for d in _model_dirs_for(root, doc):
            model_dirs.append(d)
        _scan_profiles(model, ctx, root)
    model.model_dirs = tuple(dict.fromkeys(model_dirs)) or ("models",)

    model_dir_set = tuple(
        d for root in model.project_roots for d in _dirs_under(root, model.model_dirs)
    )
    for rel in sorted(ctx.files):
        if rel.suffix.lower() == ".sql":
            if _in_dirs(rel, model_dir_set):
                text = ctx.read_text(rel) or ""
                mat, uniq, refs, sources, macros = _scan_model_sql(text)
                model.models.append(DbtModel(rel.stem, rel, mat, uniq, refs, sources, macros))
            else:
                # non-model sql dirs: macros/tests/snapshots conventions
                if "macros" in rel.parts:
                    for mm in _MACRO_DEF.finditer(ctx.read_text(rel) or ""):
                        model.macros_defined.append(mm.group(1))
                elif "tests" in rel.parts:
                    model.singular_tests.append(rel.stem)
                elif "snapshots" in rel.parts:
                    model.snapshots.append(rel.stem)
        elif rel.suffix.lower() == ".csv" and "seeds" in rel.parts:
            model.seeds.append(rel.stem)
        elif rel.suffix.lower() in {".yml", ".yaml"} and _in_dirs(rel, model_dir_set):
            doc = _yml(_load(ctx, rel))
            if doc:
                tests_map, described = _schema_model_tests(doc)
                for name, tests in tests_map.items():
                    schema_tests.setdefault(name, []).extend(tests)
                schema_described |= described
                _scan_schema(model, rel, doc)
    _attach_schema(model, schema_tests, schema_described)
    _from_observed(model, ctx)
    setattr(ctx, _CACHE_ATTR, model)
    return model
