"""PySpark checks (SPARK###) driven by the AST analyzer - never imports code."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from forge_doctor.analyzers.spark_ast import SparkAnalyzer, dataframe_names
from forge_doctor.core.models import Confidence, Severity
from forge_doctor.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.models import CheckResult

# Pseudo-pattern buckets: files that import pyspark, and the probable
# DataFrame names each analyzer pass resolved (receiver grading).
_PYSPARK_FILES = "pyspark_files"
_DFNAMES = "dfnames"
_CACHE_ATTR = "_forge_doctor_spark_buckets"

# Bucket entries: (file, line, receiver-name-or-None).
Occurrence = tuple[Path, int, "str | None"]


def analyze_project(ctx: ProjectContext) -> dict[str, list[Occurrence]]:
    """Analyze every ``*.py`` file once; bucket ``(file, line, receiver)``.

    The result is memoized on the context so all SPARK checks share a single
    pass over the project's files.
    """
    cached = getattr(ctx, _CACHE_ATTR, None)
    if cached is not None:
        return cast(dict[str, list[Occurrence]], cached)

    from forge_doctor.analyzers.index import project_index
    from forge_doctor.core.cache import scan_cache

    cache = scan_cache(ctx)
    index = project_index(ctx)
    buckets: dict[str, list[Occurrence]] = {}
    for relative, module in sorted(index.modules.items()):
        if module.spark_buckets is not None:
            # Cache-restored facts: replay without parsing.
            for pattern, occurrences in module.spark_buckets.items():
                buckets.setdefault(pattern, []).extend(
                    (relative, line, receiver) for line, receiver in occurrences
                )
            continue
        tree = module.tree
        if tree is None:
            continue
        # Gate on real Spark evidence: a pyspark import, or names proven
        # df-ish by cross-module producer propagation. The name heuristic
        # alone (``df = f()``) must not open the gate - polars/pandas false
        # positives live there.
        has_evidence = module.uses_pyspark or module.propagated_names
        if not has_evidence:
            continue
        analyzer = SparkAnalyzer()
        # Index-resolved names include cross-module producer bindings.
        analyzer.df_names = dataframe_names(tree) | module.df_names
        analyzer.visit(tree)
        if module.uses_pyspark:
            buckets.setdefault(_PYSPARK_FILES, []).append((relative, 0, None))
        buckets.setdefault(_DFNAMES, []).extend((relative, 0, name) for name in analyzer.df_names)
        file_buckets: dict[str, list[list[object]]] = {
            _DFNAMES: [[0, name] for name in analyzer.df_names],
        }
        if module.uses_pyspark:
            file_buckets[_PYSPARK_FILES] = [[0, None]]
        for finding in analyzer.findings:
            buckets.setdefault(finding.pattern, []).append(
                (relative, finding.line, finding.receiver)
            )
            file_buckets.setdefault(finding.pattern, []).append([finding.line, finding.receiver])
        module.spark_buckets = file_buckets
        cache.update_facts(relative, {"spark_buckets": file_buckets})

    setattr(ctx, _CACHE_ATTR, buckets)
    return buckets


def _df_names(ctx: ProjectContext, file: Path) -> set[str]:
    """DataFrame-probable names the analyzer resolved inside ``file``."""
    return {
        name
        for f, _, name in analyze_project(ctx).get(_DFNAMES, [])
        if f == file and name is not None
    }


def _df_confidence(receiver: str | None, df_names: set[str] | None = None) -> Confidence:
    """HIGH when the receiver is DataFrame-probable; MEDIUM otherwise."""
    if receiver is None:
        return Confidence.MEDIUM
    lowered = receiver.lower()
    if (
        (df_names and receiver in df_names)
        or "df" in lowered
        or lowered in {"spark", "sparksession", "sc", "sparkcontext"}
    ):
        return Confidence.HIGH
    return Confidence.MEDIUM


class _SparkCheck(CheckBase):
    """Shared base for SPARK checks: access to the per-pattern buckets."""

    category = "spark"

    def occurrences(self, ctx: ProjectContext, *patterns: str) -> list[Occurrence]:
        """All ``(file, line, receiver)`` hits for ``patterns``, sorted."""
        buckets = analyze_project(ctx)
        found = [item for pattern in patterns for item in buckets.get(pattern, [])]
        return sorted(found, key=lambda item: (item[0].as_posix(), item[1]))


class PySparkUsage(_SparkCheck):
    """SPARK007: how many project files actually use PySpark."""

    id = "SPARK007"
    title = "PySpark usage"
    why = "Anchor: tells you how much of the project actually touches Spark."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the count to size the Spark surface."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        files = analyze_project(ctx).get(_PYSPARK_FILES, [])
        if not files:
            return [self.result(Severity.INFO, "no PySpark usage detected")]
        return [self.result(Severity.PASS, f"{len(files)} PySpark files analyzed")]


class CollectToDriver(_SparkCheck):
    """SPARK001: ``collect()`` moves the whole dataset to the driver."""

    id = "SPARK001"
    title = "collect()"
    why = "collect() moves every row to the driver - the classic OOM."
    when_ok = "Bounded reference data, tests, single-driver jobs."
    fix = "Write to storage, or bound with limit() first."
    tags: tuple[str, ...] = ("performance", "driver-memory")

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.WARNING,
                "collect() moves all rows to the driver",
                recommendation=(
                    "Verify the volume is intentionally bounded; prefer "
                    "write/collect_list with limits."
                ),
                file=file,
                line=line,
                evidence=self.evidence_at(ctx, file, line),
                confidence=_df_confidence(receiver, _df_names(ctx, file)),
            )
            for file, line, receiver in self.occurrences(ctx, "collect")
        ]


class ToPandas(_SparkCheck):
    """SPARK002: ``toPandas()`` pulls the full dataset into driver memory."""

    id = "SPARK002"
    title = "toPandas()"
    why = "Same driver-bound risk as collect(), plus Arrow conversion overhead."
    when_ok = "Small aggregates destined for plotting or reporting."
    fix = "Prefer Spark-native ops or Arrow-optimized transfers."
    tags: tuple[str, ...] = ("performance", "driver-memory")

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.WARNING,
                "toPandas() pulls the full dataset into driver memory",
                recommendation=("Prefer Spark-native ops or Arrow-optimized transfers."),
                file=file,
                line=line,
                evidence=self.evidence_at(ctx, file, line),
                confidence=_df_confidence(receiver, _df_names(ctx, file)),
            )
            for file, line, receiver in self.occurrences(ctx, "toPandas")
        ]


class SinglePartition(_SparkCheck):
    """SPARK003: ``repartition(1)``/``coalesce(1)`` force a single task."""

    id = "SPARK003"
    title = "repartition(1)/coalesce(1)"
    why = "Forces all work onto a single task - parallelism collapses."
    when_ok = "Deliberately producing exactly one small output file."
    fix = "Keep the natural partitioning or pick a larger count."
    tags: tuple[str, ...] = ("performance", "shuffle")

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.WARNING,
                "single partition forces all work onto one task",
                recommendation="Only acceptable for tiny outputs.",
                file=file,
                line=line,
                evidence=self.evidence_at(ctx, file, line),
            )
            for file, line, _ in self.occurrences(ctx, "repartition(1)", "coalesce(1)")
        ]


class PythonUdf(_SparkCheck):
    """SPARK004: plain Python UDFs serialize rows through Python."""

    id = "SPARK004"
    title = "Python UDF"
    why = "Row-serializes through the Python interpreter - order-of-magnitude slower."
    when_ok = "Unavoidable domain logic with no built-in equivalent."
    fix = "Prefer built-in functions or pandas_udf (vectorized)."
    tags: tuple[str, ...] = ("performance",)

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.INFO,
                (
                    "Python UDFs serialize rows through Python; prefer "
                    "built-in functions or pandas_udf"
                ),
                file=file,
                line=line,
                evidence=self.evidence_at(ctx, file, line),
            )
            for file, line, _ in self.occurrences(ctx, "python_udf")
        ]


class ActionInLoop(_SparkCheck):
    """SPARK005: Spark actions inside loops may launch one job per iteration."""

    id = "SPARK005"
    title = "Action inside loop"
    why = "One job per iteration - scheduler and lineage can explode."
    when_ok = "Deliberate per-partition jobs with bounded iteration."
    fix = "Restructure to a single action where possible."
    tags: tuple[str, ...] = ("performance", "jobs")

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.WARNING,
                "a Spark action inside a loop may launch one job per iteration",
                recommendation="Restructure to a single action.",
                file=file,
                line=line,
                evidence=self.evidence_at(ctx, file, line),
            )
            for file, line, _ in self.occurrences(ctx, "action_in_loop")
        ]


class CacheWithoutUnpersist(_SparkCheck):
    """SPARK006: ``cache()``/``persist()`` without ``unpersist()`` on the same var."""

    id = "SPARK006"
    title = "cache() without unpersist()"
    why = "Cached frames may never be released - executor memory leaks."
    when_ok = "Short-lived sessions; still tidy to unpersist()."
    fix = "Call unpersist() on the same variable when the frame is no longer needed."
    tags: tuple[str, ...] = ("performance", "memory")

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        buckets = analyze_project(ctx)
        # Same-variable pairing: df.cache() is covered only by df.unpersist(),
        # not by any unpersist() elsewhere in the file.
        unpersisted: dict[Path, set[str]] = {}
        for file, _, receiver in buckets.get("unpersist", []):
            if receiver:
                unpersisted.setdefault(file, set()).add(receiver)

        results: list[CheckResult] = []
        flagged: set[tuple[Path, str | None]] = set()
        for pattern in ("cache", "persist"):
            for file, line, receiver in buckets.get(pattern, []):
                key = (file, receiver)
                if receiver is not None and receiver in unpersisted.get(file, set()):
                    continue
                if key in flagged:
                    continue
                flagged.add(key)
                results.append(
                    self.result(
                        Severity.INFO,
                        f"cached {receiver or 'frame'} may never be released",
                        recommendation="unpersist() the same variable when done.",
                        file=file,
                        line=line,
                        evidence=self.evidence_at(ctx, file, line),
                        confidence=Confidence.MEDIUM if receiver is None else Confidence.HIGH,
                    )
                )
        return results


class RddEscapeHatch(_SparkCheck):
    """SPARK008: ``.rdd`` access drops out of the Catalyst-optimized world."""

    id = "SPARK008"
    title = "RDD access"
    why = "The RDD API bypasses Catalyst/Tungsten optimizations and is hard to tune."
    when_ok = "Rare cases needing partition-level control unavailable in DataFrames."
    fix = "Prefer DataFrame/SQL APIs; keep .rdd usage isolated and documented."
    tags: tuple[str, ...] = ("performance",)
    confidence = Confidence.MEDIUM

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.INFO,
                ".rdd access bypasses Catalyst optimizations",
                recommendation="Prefer DataFrame APIs; isolate RDD code if required.",
                file=file,
                line=line,
                evidence=self.evidence_at(ctx, file, line),
                confidence=_df_confidence(receiver, _df_names(ctx, file)),
            )
            for file, line, receiver in self.occurrences(ctx, "rdd")
        ]


class JoinWithoutCondition(_SparkCheck):
    """SPARK009: ``crossJoin``/``join`` without a condition is Cartesian."""

    id = "SPARK009"
    title = "Cartesian join"
    why = "Cartesian products explode row counts quadratically - classic OOM."
    when_ok = "Intentional cross joins on tiny bounded frames."
    fix = "Provide a join condition or filter; confirm the intent is Cartesian."
    tags: tuple[str, ...] = ("performance", "shuffle")

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        results: list[CheckResult] = []
        for file, line, receiver in self.occurrences(ctx, "crossJoin", "join_no_condition"):
            results.append(
                self.result(
                    Severity.WARNING,
                    "join without condition produces a Cartesian product",
                    recommendation="Add an 'on' condition or confirm bounded inputs.",
                    file=file,
                    line=line,
                    evidence=self.evidence_at(ctx, file, line),
                    confidence=_df_confidence(receiver, _df_names(ctx, file)),
                )
            )
        return results


class GlobalSort(_SparkCheck):
    """SPARK010: ``orderBy``/``sort`` triggers a full-range shuffle."""

    id = "SPARK010"
    title = "Global sort"
    why = "orderBy/sort shuffles every row - expensive on large datasets."
    when_ok = "Final reporting step on small aggregates."
    fix = "Use sortWithinPartitions when global ordering is not required."
    tags: tuple[str, ...] = ("performance", "shuffle")
    confidence = Confidence.MEDIUM

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.INFO,
                "global sort shuffles all partitions",
                recommendation="sortWithinPartitions suffices for most pipelines.",
                file=file,
                line=line,
                evidence=self.evidence_at(ctx, file, line),
            )
            for file, line, _ in self.occurrences(ctx, "global_sort")
        ]


class WithColumnInLoop(_SparkCheck):
    """SPARK011: ``withColumn`` in a loop grows the logical plan exponentially."""

    id = "SPARK011"
    title = "withColumn() in loop"
    why = "Each iteration wraps the plan; optimization time explodes."
    when_ok = "Very few iterations on small frames."
    fix = "Build a select() list or use expr/SQL for multi-column transforms."
    tags: tuple[str, ...] = ("performance", "plan")

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.WARNING,
                "withColumn() inside a loop grows the logical plan",
                recommendation="Accumulate expressions and select() once.",
                file=file,
                line=line,
                evidence=self.evidence_at(ctx, file, line),
            )
            for file, line, _ in self.occurrences(ctx, "withColumn_in_loop")
        ]


CHECKS: list[Check] = [
    CollectToDriver(),
    ToPandas(),
    SinglePartition(),
    PythonUdf(),
    ActionInLoop(),
    CacheWithoutUnpersist(),
    PySparkUsage(),
    RddEscapeHatch(),
    JoinWithoutCondition(),
    GlobalSort(),
    WithColumnInLoop(),
]
