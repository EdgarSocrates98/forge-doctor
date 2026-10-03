"""AWS Glue checks (GLUE###) driven by the AST analyzer - never imports code."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from forge_doctor_data.analyzers.glue_ast import GlueAnalyzer
from forge_doctor_data.core.models import Severity
from forge_doctor_data.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor_data.core.context import ProjectContext
    from forge_doctor_data.core.models import CheckResult

# Pseudo-pattern buckets (line is always 0): files that use Glue, and the
# subset that also imports pyspark - GLUE004 needs both without re-walking.
_GLUE_FILES = "glue_files"
_PYSPARK_FILES = "pyspark_files"
_VERSION_PREFIX = "glue_version:"
_CACHE_ATTR = "_forge_doctor_data_glue_buckets"


def analyze_project(ctx: ProjectContext) -> dict[str, list[tuple[Path, int]]]:
    """Analyze every ``*.py`` file once; bucket ``(file, line)`` per pattern.

    The result is memoized on the context so all GLUE checks share a single
    pass over the project's files.
    """
    cached = getattr(ctx, _CACHE_ATTR, None)
    if cached is not None:
        return cast(dict[str, list[tuple[Path, int]]], cached)

    from forge_doctor_data.analyzers.index import project_index
    from forge_doctor_data.core.cache import scan_cache

    cache = scan_cache(ctx)
    index = project_index(ctx)
    buckets: dict[str, list[tuple[Path, int]]] = {}
    for relative, module in sorted(index.modules.items()):
        if module.glue_buckets is not None:
            for pattern, lines in module.glue_buckets.items():
                buckets.setdefault(pattern, []).extend((relative, line) for line in lines)
            continue
        tree = module.tree
        if tree is None or not module.uses_glue:
            continue
        analyzer = GlueAnalyzer()
        analyzer.visit(tree)
        buckets.setdefault(_GLUE_FILES, []).append((relative, 0))
        file_buckets: dict[str, list[int]] = {_GLUE_FILES: [0]}
        if module.uses_pyspark:
            buckets.setdefault(_PYSPARK_FILES, []).append((relative, 0))
            file_buckets[_PYSPARK_FILES] = [0]
        for finding in analyzer.findings:
            buckets.setdefault(finding.pattern, []).append((relative, finding.line))
            file_buckets.setdefault(finding.pattern, []).append(finding.line)
        module.glue_buckets = file_buckets
        cache.update_facts(relative, {"glue_buckets": file_buckets})

    setattr(ctx, _CACHE_ATTR, buckets)
    return buckets


class _GlueCheck(CheckBase):
    """Shared base for GLUE checks: access to the per-pattern buckets."""

    category = "glue"

    def occurrences(self, ctx: ProjectContext, *patterns: str) -> list[tuple[Path, int]]:
        """All ``(file, line)`` hits for ``patterns``, sorted deterministically."""
        buckets = analyze_project(ctx)
        found = [item for pattern in patterns for item in buckets.get(pattern, [])]
        return sorted(found, key=lambda item: (item[0].as_posix(), item[1]))


class GlueUsage(_GlueCheck):
    """GLUE001: how many project files actually use AWS Glue."""

    id = "GLUE001"
    title = "Glue usage"
    why = "Confirms the project uses AWS Glue so the other GLUE checks apply."
    when_ok = "Always - informational anchor."
    fix = "No action needed."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        files = analyze_project(ctx).get(_GLUE_FILES, [])
        if not files:
            return [self.result(Severity.INFO, "no AWS Glue usage detected")]
        return [self.result(Severity.PASS, f"{len(files)} file(s) use awsglue/glue client")]


class EolGlueRuntime(_GlueCheck):
    """GLUE002: ``glue_version`` pins on EOL/aging runtimes (knowledge pack)."""

    id = "GLUE002"
    title = "EOL Glue runtime"
    why = "EOL Glue runtimes cannot create new jobs; aging ones are heading there."
    when_ok = "Legacy jobs still run while a migration to a supported runtime is planned."
    fix = "Move jobs to a supported Glue runtime (see `forge-doctor-data compatibility`)."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        from forge_doctor_data.core.knowledge import glue_current, glue_status

        buckets = analyze_project(ctx)
        found = [
            (pattern.removeprefix(_VERSION_PREFIX), file, line)
            for pattern, hits in buckets.items()
            if pattern.startswith(_VERSION_PREFIX)
            for file, line in hits
        ]
        found.sort(key=lambda item: (item[1].as_posix(), item[2]))
        results: list[CheckResult] = []
        current = glue_current()
        for version, file, line in found:
            status = glue_status(version)
            if status == "eol":
                severity = Severity.WARNING
                message = f"Glue {version} is end-of-life - new jobs cannot be created on it"
            elif status == "aging":
                severity = Severity.INFO
                message = f"Glue {version} is aging - {current} is the current runtime"
            else:
                continue
            results.append(
                self.result(
                    severity,
                    message,
                    recommendation=f"Migrate towards Glue {current}.",
                    file=file,
                    line=line,
                )
            )
        return results


class JobParameters(_GlueCheck):
    """GLUE003: Glue jobs should read parameters via ``getResolvedOptions``."""

    id = "GLUE003"
    title = "Job parameters"
    why = "getResolvedOptions is the supported way to read Glue job arguments."
    when_ok = "Job parameters are intentionally hardcoded or injected another way."
    fix = "Read arguments via getResolvedOptions(sys.argv, [...]) instead of hardcoding."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        if not analyze_project(ctx).get(_GLUE_FILES):
            return []
        hits = self.occurrences(ctx, "get_resolved_options")
        if hits:
            files = {file for file, _ in hits}
            return [
                self.result(
                    Severity.PASS,
                    f"getResolvedOptions used in {len(files)} file(s)",
                )
            ]
        return [
            self.result(
                Severity.INFO,
                "no getResolvedOptions usage - job params may be hardcoded",
                recommendation="Pass parameters via job arguments and getResolvedOptions.",
            )
        ]


class DynamicFrameMixing(_GlueCheck):
    """GLUE004: files mixing DynamicFrame and Spark DataFrame APIs."""

    id = "GLUE004"
    title = "DynamicFrame/DataFrame mixing"
    why = "Mixing DynamicFrame and DataFrame APIs complicates lineage and testing."
    when_ok = "Conversion points are deliberate at job boundaries."
    fix = "Keep one API per stage; convert with fromDF/toDF at explicit boundaries."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        buckets = analyze_project(ctx)
        first_marker_line: dict[Path, int] = {}
        for pattern in ("dynamic_frame", "fromDF"):
            for file, line in buckets.get(pattern, []):
                first_marker_line[file] = min(line, first_marker_line.get(file, line))
        spark_side = {file for file, _ in buckets.get(_PYSPARK_FILES, [])} | {
            file for file, _ in buckets.get("toDF", [])
        }
        return [
            self.result(
                Severity.INFO,
                "file mixes DynamicFrame and Spark DataFrame APIs",
                recommendation="Keep conversion points explicit at job boundaries.",
                file=file,
                line=first_marker_line[file],
            )
            for file in sorted(first_marker_line)
            if file in spark_side
        ]


CHECKS: list[Check] = [
    GlueUsage(),
    EolGlueRuntime(),
    JobParameters(),
    DynamicFrameMixing(),
]
