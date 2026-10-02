"""Data quality evidence model — Deequ, Great Expectations, SodaCL, dbt (spec 222).

Expectation suites are *evidence of intent*: they declare what the data
*should* look like. The model captures suites, the tables/columns they
target, and the gates (checkpoints, CI invocations, observed run
exports) that actually wire them into pipelines — the valuable
cross-domain findings are suites defined-but-never-run and prod tables
with zero coverage.

- **Great Expectations** — ``expectations/*.json`` suites with
  ``expectation_suite_name`` + ``expectations[].expectation_type``
  shapes; ``checkpoints/*.{yml,json}`` and ``uncommitted/validations/``
  exports wire suites to gates and observed runs.
- **SodaCL** — ``*.yml|*.yaml`` files whose top-level keys are
  ``checks for <dataset>:`` — a CI ``checks:`` key alone never
  attributes (adversarial gate).
- **Deequ** — Python/Scala source calling ``VerificationSuite`` /
  ``Check(`` analyzers (``isComplete``, ``hasSize``, ``isUnique``…);
  suites are code-bound so they always count as wired.
- **dbt** — schema/singular tests from ``DbtProjectModel`` (spec 216);
  wired when the project is invoked (``dbt test``/``dbt build`` /
  ``Dbt*Operator`` anywhere in CI/scripts/dags) since dbt runs the
  whole test suite per invocation.

Suites are parsed, never executed; observed results arrive only as
exported artifacts.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext

_CACHE_ATTR = "_forge_doctor_quality_model"

ENGINES = ("deequ", "great_expectations", "soda", "dbt")


@dataclass(frozen=True)
class QualityExpectation:
    """One declared expectation/check inside a suite."""

    name: str  # expectation_type / analyzer / SodaCL check text
    file: Path
    column: str = ""  # column-targeted expectations; "" = table-level


@dataclass(frozen=True)
class QualitySuite:
    """A declared expectation suite (per engine)."""

    engine: str  # deequ | great_expectations | soda | dbt
    name: str
    file: Path
    table: str = ""  # resolved target tail-name; "" when unbound
    expectations: tuple[QualityExpectation, ...] = ()
    columns: tuple[str, ...] = ()
    wired: bool = False  # checkpoint/pipeline/observed-run reference


@dataclass(frozen=True)
class QualityGate:
    """A gate that can run suites: checkpoint, CI invocation, operator."""

    kind: str  # checkpoint | ci_invocation | operator
    engine: str
    name: str
    file: Path
    suite_refs: tuple[str, ...] = ()  # suite names referenced ("" = all)


@dataclass(frozen=True)
class ObservedQualityRun:
    """An exported validation run result (observed evidence)."""

    engine: str
    suite: str
    file: Path
    success: bool | None = None


@dataclass
class DataQualityModel:
    """All declared data-quality evidence for a project."""

    suites: list[QualitySuite] = field(default_factory=list)
    gates: list[QualityGate] = field(default_factory=list)
    observed_runs: list[ObservedQualityRun] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)

    @property
    def has_evidence(self) -> bool:
        return bool(self.suites or self.gates or self.observed_runs)

    def engines(self) -> set[str]:
        return {s.engine for s in self.suites} | {g.engine for g in self.gates}

    def covered_tables(self) -> set[str]:
        """Tail-names with at least one suite targeting them."""
        return {s.table.lower() for s in self.suites if s.table}

    def unwired_suites(self) -> list[QualitySuite]:
        """Suites with no gate reference — defined but never run."""
        return [s for s in self.suites if not s.wired]


def _tail(name: str) -> str:
    return name.rstrip("/").rsplit("/", 1)[-1].rsplit(".", 1)[-1]


def _load_yaml_safe(text: str) -> Any:
    try:
        from forge_doctor.core.contract import _load_yaml

        doc, _err = _load_yaml(text)
    except Exception:
        return None
    return doc


def _gx_suite_table(doc: dict[str, Any], suite_name: str) -> str:
    meta = doc.get("meta")
    for src in (meta if isinstance(meta, dict) else {}, doc):
        for key in ("data_asset_name", "table", "dataset"):
            val = src.get(key)
            if isinstance(val, str) and val:
                return _tail(val)
    # suite-name convention: "<table>.<severity|purpose>"
    head = suite_name.split(".", 1)[0] if "." in suite_name else ""
    return head


# ---------------------------------------------------------------------------
# Great Expectations


def _is_gx_suite(doc: Any) -> bool:
    return isinstance(doc, dict) and isinstance(
        doc.get("expectations"), list
    ) and (
        isinstance(doc.get("expectation_suite_name"), str)
        or any(
            isinstance(e, dict) and "expectation_type" in e
            for e in doc["expectations"]
        )
    )


def _scan_gx_suites(ctx: ProjectContext, model: DataQualityModel) -> None:
    for rel in sorted(ctx.files):
        if rel.suffix.lower() != ".json":
            continue
        posix = rel.as_posix().lower()
        name_hint = "expectation" in posix or "great_expectations" in posix
        text = ctx.read_text(rel)
        if text is None or ("expectation" not in text and not name_hint):
            continue
        try:
            doc = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            if name_hint:
                model.unparsed.append(rel.as_posix())
            continue
        if not _is_gx_suite(doc):
            continue
        suite_name = str(doc.get("expectation_suite_name") or rel.stem)
        exps: list[QualityExpectation] = []
        cols: list[str] = []
        for exp in doc["expectations"]:
            if not isinstance(exp, dict):
                continue
            etype = str(exp.get("expectation_type") or "")
            if not etype:
                continue
            kwargs = exp.get("kwargs")
            col = ""
            if isinstance(kwargs, dict):
                c = kwargs.get("column") or kwargs.get("column_A")
                col = str(c) if c else ""
            exps.append(QualityExpectation(name=etype, file=rel, column=col))
            if col:
                cols.append(col)
        model.suites.append(
            QualitySuite(
                engine="great_expectations",
                name=suite_name,
                file=rel,
                table=_gx_suite_table(doc, suite_name),
                expectations=tuple(exps),
                columns=tuple(sorted(set(cols))),
            )
        )


def _scan_gx_gates(ctx: ProjectContext, model: DataQualityModel) -> None:
    """Checkpoints + observed validation exports."""
    for rel in sorted(ctx.files):
        posix = rel.as_posix().lower()
        if "validation" in posix or "run_results" in posix:
            if rel.suffix.lower() != ".json":
                continue
            text = ctx.read_text(rel)
            if text is None:
                continue
            try:
                doc = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(doc, dict):
                continue
            suite = str(doc.get("expectation_suite_name") or "")
            meta = doc.get("meta")
            if not suite and isinstance(meta, dict):
                suite = str(meta.get("expectation_suite_name") or "")
            if not suite:
                continue
            ok = doc.get("success")
            model.observed_runs.append(
                ObservedQualityRun(
                    engine="great_expectations",
                    suite=suite,
                    file=rel,
                    success=bool(ok) if ok is not None else None,
                )
            )
            continue
        is_ckpt = "checkpoint" in posix or "checkpoints" in posix
        if not is_ckpt or rel.suffix.lower() not in (".yml", ".yaml", ".json"):
            continue
        text = ctx.read_text(rel)
        if text is None or (
            "expectation_suite_name" not in text and "validations" not in text
        ):
            continue
        doc = _load_yaml_safe(text) if rel.suffix.lower() != ".json" else None
        if doc is None and rel.suffix.lower() == ".json":
            try:
                doc = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                continue
        if not isinstance(doc, dict):
            continue
        refs: list[str] = []
        vals = doc.get("validations")
        if isinstance(vals, list):
            for v in vals:
                if isinstance(v, dict) and isinstance(
                    v.get("expectation_suite_name"), str
                ):
                    refs.append(str(v["expectation_suite_name"]))
        if isinstance(doc.get("expectation_suite_name"), str):
            refs.append(str(doc["expectation_suite_name"]))
        if not refs and "expectation_suite_name" not in text:
            continue
        model.gates.append(
            QualityGate(
                kind="checkpoint",
                engine="great_expectations",
                name=str(doc.get("name") or rel.stem),
                file=rel,
                suite_refs=tuple(refs),
            )
        )


# ---------------------------------------------------------------------------
# SodaCL


_SODA_CHECKS_RE = re.compile(r"^checks\s+for\s+(.+?)\s*$", re.I)
_SODA_COL_RE = re.compile(r"\(\s*([A-Za-z_][\w.]*)\s*\)")


def _scan_soda(ctx: ProjectContext, model: DataQualityModel) -> None:
    for rel in sorted(ctx.files):
        if rel.suffix.lower() not in (".yml", ".yaml"):
            continue
        text = ctx.read_text(rel)
        if text is None or "checks for" not in text:
            continue
        doc = _load_yaml_safe(text)
        if not isinstance(doc, dict):
            continue
        for key, body in doc.items():
            k = str(key).strip().rstrip(":")
            m = _SODA_CHECKS_RE.match(k)
            if not m or not isinstance(body, list):
                continue
            table = _tail(m.group(1).strip('"').strip("'"))
            exps: list[QualityExpectation] = []
            cols: list[str] = []
            for item in body:
                if isinstance(item, str):
                    exps.append(
                        QualityExpectation(name=item.strip(), file=rel)
                    )
                    cols += [
                        c for c in _SODA_COL_RE.findall(item) if "." not in c
                    ]
                elif isinstance(item, dict):
                    for ck in item:
                        exps.append(
                            QualityExpectation(name=str(ck), file=rel)
                        )
            model.suites.append(
                QualitySuite(
                    engine="soda",
                    name=f"soda:{table}",
                    file=rel,
                    table=table,
                    expectations=tuple(exps),
                    columns=tuple(sorted(set(cols))),
                )
            )


# ---------------------------------------------------------------------------
# Deequ (code-bound suites)


_DEEQU_MARKERS = ("deequ", "VerificationSuite", "CheckSuite")
_DEEQU_ANALYZER_RE = re.compile(
    r"\.(isComplete|isUnique|hasSize|hasMin|hasMax|hasMean|hasSum|"
    r"hasStandardDeviation|hasEntropy|hasMutualInformation|hasUniqueness|"
    r"hasDistinctness|hasCompleteness|isNonNegative|isPositive|"
    r"hasPattern|containsValue|hasApproxCountDistinct|hasCorrelation|"
    r"isContainedIn|satisfies|hasDataType|hasHistogramValues|"
    r"hasMinLength|hasMaxLength|isPrimaryKey)\s*\(\s*[\"']([\w.]+)?",
    re.I,
)
_DEEQU_CHECK_DESC_RE = re.compile(r"Check\s*\([^,)]*,\s*[\"']([^\"']+)[\"']")


def _scan_deequ(ctx: ProjectContext, model: DataQualityModel) -> None:
    for rel in sorted(ctx.files):
        if rel.suffix.lower() not in (".py", ".scala", ".ipynb"):
            continue
        text = ctx.read_text(rel)
        if text is None or not any(m in text for m in _DEEQU_MARKERS):
            continue
        if "VerificationSuite" not in text and "Check(" not in text:
            continue
        exps: list[QualityExpectation] = []
        cols: list[str] = []
        for func, col in _DEEQU_ANALYZER_RE.findall(text):
            exps.append(
                QualityExpectation(
                    name=func.lower(), file=rel, column=col or ""
                )
            )
            if col:
                cols.append(col)
        descs = _DEEQU_CHECK_DESC_RE.findall(text)
        if not exps:
            continue
        model.suites.append(
            QualitySuite(
                engine="deequ",
                name="; ".join(descs[:2]) if descs else rel.stem,
                file=rel,
                expectations=tuple(exps),
                columns=tuple(sorted(set(cols))),
                wired=True,  # code-bound: the suite IS the pipeline
            )
        )


# ---------------------------------------------------------------------------
# dbt (reuse spec 216 model)


def _scan_dbt(ctx: ProjectContext, model: DataQualityModel) -> None:
    try:
        from forge_doctor.analyzers.dbt_model import dbt_model
    except ImportError:
        return
    dm = dbt_model(ctx)
    if not dm.has_evidence:
        return
    for m in dm.models:
        if not m.tests:
            continue
        model.suites.append(
            QualitySuite(
                engine="dbt",
                name=f"dbt:{m.name}",
                file=m.file,
                table=m.name,
                expectations=tuple(
                    QualityExpectation(name=t, file=m.file) for t in m.tests
                ),
            )
        )
    for s in dm.sources:
        if not s.has_tests:
            continue
        model.suites.append(
            QualitySuite(
                engine="dbt",
                name=f"dbt:src:{s.name}",
                file=s.file,
                table=_tail(s.name),
            )
        )
    for t in dm.singular_tests:
        model.suites.append(
            QualitySuite(
                engine="dbt",
                name=f"dbt:singular:{Path(t).stem}",
                file=Path(t),
            )
        )


# ---------------------------------------------------------------------------
# Gate wiring — CI/script/operator invocations


_INVOCATION_RES = (
    ("dbt", re.compile(r"\b(?:dbt\s+(?:test|build|run|compile)|Dbt\w+Operator)")),
    ("soda", re.compile(r"\b(?:soda\s+scan|soda\s+ingest|SodaScanOperator)")),
    (
        "great_expectations",
        re.compile(
            r"\b(?:GreatExpectationsOperator|run_checkpoint|"
            r"great_expectations\s+checkpoint\s+run)"
        ),
    ),
)


def _scan_invocations(ctx: ProjectContext, model: DataQualityModel) -> None:
    for rel in sorted(ctx.files):
        if rel.suffix.lower() not in (
            ".yml",
            ".yaml",
            ".py",
            ".sh",
            ".toml",
            ".cfg",
            ".txt",
        ):
            continue
        text = ctx.read_text(rel)
        if not text:
            continue
        for engine, pat in _INVOCATION_RES:
            m = pat.search(text)
            if not m:
                continue
            model.gates.append(
                QualityGate(
                    kind="ci_invocation"
                    if rel.suffix.lower() in (".yml", ".yaml", ".toml", ".cfg")
                    else "operator",
                    engine=engine,
                    name=m.group(0).strip(),
                    file=rel,
                )
            )
            break  # one gate row per file+engine


def _wire_suites(model: DataQualityModel) -> None:
    """Mark suites referenced by a checkpoint/invocation/observed run."""
    observed = {r.suite.lower() for r in model.observed_runs}
    out: list[QualitySuite] = []
    for s in model.suites:
        wired = s.wired
        for g in model.gates:
            if g.engine != s.engine:
                continue
            # engine-wide invocations (dbt test, soda scan) run every suite
            if not g.suite_refs or s.name.lower() in {
                r.lower() for r in g.suite_refs
            }:
                wired = True
                break
        if not wired and s.name.lower() in observed:
            wired = True
        if wired is not s.wired:
            s = QualitySuite(
                engine=s.engine,
                name=s.name,
                file=s.file,
                table=s.table,
                expectations=s.expectations,
                columns=s.columns,
                wired=True,
            )
        out.append(s)
    model.suites = out


# ---------------------------------------------------------------------------
# Model entry point


def quality_model(ctx: ProjectContext) -> DataQualityModel:
    """Memoized data-quality evidence model over ctx evidence."""
    cached = getattr(ctx, _CACHE_ATTR, None)
    if cached is not None:
        return cast(DataQualityModel, cached)
    model = DataQualityModel()
    _scan_gx_suites(ctx, model)
    _scan_gx_gates(ctx, model)
    _scan_soda(ctx, model)
    _scan_deequ(ctx, model)
    _scan_dbt(ctx, model)
    _scan_invocations(ctx, model)
    _wire_suites(model)
    setattr(ctx, _CACHE_ATTR, model)
    return model
