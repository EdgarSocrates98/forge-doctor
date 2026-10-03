"""Performance & scale benchmark - synthetic projects, cold/warm timing.

Generates deterministic synthetic projects at N files (seeded RNG — same
seed, same corpus) and measures:

- ``cold_ms`` / ``warm_ms`` — full check run, fresh vs disk-cached index
- ``ast_parsed`` — .py modules with an AST (1 parse/file = ideal)
- ``graph_ms`` — platform-graph build time
- ``pack_ms`` — knowledge-pack registry load time
- ``peak_mb`` — tracemalloc peak during the cold scan
- ``findings`` — non-PASS result count (sanity)

Budgets (``bench run --budget-file b.json``) are machine-portable ratios
and counts, not absolute clocks: ``warm_ratio_max``,
``ast_parse_max_ratio`` (=1.0 → at most one AST parse per .py file),
``graph_ms_per_1k_files``, ``cold_ms_per_1k_files`` (generous).
"""

from __future__ import annotations

import json
import random
import time
import tracemalloc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forge_doctor_data.core.models import Severity

_FINDING_SEVERITIES = frozenset({Severity.INFO, Severity.WARNING, Severity.ERROR})

_PY_TEMPLATES = (
    "from pyspark.sql import SparkSession\n"
    "spark = SparkSession.builder.getOrCreate()\n"
    'df = spark.read.parquet("s3://b/{i}")\n'
    "out = df.filter(df.id > {i})\n{tail}\n",
    "import boto3\nclient = boto3.client('athena')\n{tail}\n",
    "import os\nx = os.environ.get('K{i}')\n{tail}\n",
)
_PY_TAILS = (
    "",
    "df.repartition(1).write.parquet('s3://b/out{i}')\n",
    "spark.sql('MERGE INTO t{i} USING s ON t{i}.id = s.id')\n",
    "df.join(df, 'id').collect()\n",
)
_TF_TEMPLATE = (
    'resource "aws_dynamodb_table" "t{i}" {{\n'
    '  name = "table-{i}"\n'
    "  stream_enabled = {stream}\n"
    "}}\n"
)
_SQL_TEMPLATE = "SELECT id, count(*) FROM db.t{i} GROUP BY id;\n"


def generate_project(root: Path, files: int, seed: int = 7) -> dict[str, int]:
    """Write a deterministic synthetic project; returns per-kind counts."""
    rng = random.Random(seed)
    counts = {"py": 0, "tf": 0, "sql": 0, "other": 0}
    for i in range(files):
        kind = rng.choices(["py", "tf", "sql", "other"], [55, 15, 15, 15])[0]
        if kind == "py":
            tpl = _PY_TEMPLATES[i % len(_PY_TEMPLATES)]
            tail = _PY_TAILS[rng.randrange(len(_PY_TAILS))]
            text = tpl.format(i=i, tail=tail)
        elif kind == "tf":
            text = _TF_TEMPLATE.format(i=i, stream=str(rng.random() < 0.5).lower())
        elif kind == "sql":
            text = _SQL_TEMPLATE.format(i=i)
        else:
            text = f"# note {i}\nkey = {i}\n"
        suffix = {"py": ".py", "tf": ".tf", "sql": ".sql", "other": ".txt"}[kind]
        rel = root / f"mod{i // 50}" / f"f{i}{suffix}"
        rel.parent.mkdir(parents=True, exist_ok=True)
        rel.write_text(text, encoding="utf-8")
        counts[kind] += 1
    return counts


@dataclass
class BenchResult:
    files: int = 0
    py_files: int = 0
    cold_ms: float = 0.0
    warm_ms: float = 0.0
    ast_parsed: int = 0
    graph_ms: float = 0.0
    graph_entities: int = 0
    graph_edges: int = 0
    pack_ms: float = 0.0
    packs: int = 0
    peak_mb: float = 0.0
    findings: int = 0
    budget_failures: list[str] = field(default_factory=list)

    @property
    def warm_ratio(self) -> float | None:
        return self.warm_ms / self.cold_ms if self.cold_ms else None

    @property
    def ast_ratio(self) -> float | None:
        return self.ast_parsed / self.py_files if self.py_files else None


def _scan_once(root: Path) -> tuple[int, int]:
    """One full check pass; returns (findings, ast_parsed)."""
    from forge_doctor_data.analyzers.index import project_index
    from forge_doctor_data.checks import builtin_checks
    from forge_doctor_data.core.context import ProjectContext

    ctx = ProjectContext(root=root.resolve())
    found = 0
    for check in builtin_checks():
        for r in check.run(ctx):
            if r.severity in _FINDING_SEVERITIES:
                found += 1
    modules = project_index(ctx).modules
    parsed = sum(1 for m in modules.values() if m.tree is not None)
    return found, parsed


def run_bench(path: Path, budget: dict[str, float] | None = None) -> BenchResult:
    """Cold + warm + graph + pack timings for a project at ``path``."""
    from forge_doctor_data.core.cache import ScanCache

    result = BenchResult()

    # knowledge pack registry load
    from forge_doctor_data.core.knowledge import list_packs

    t0 = time.perf_counter()
    result.packs = len(list_packs())
    result.pack_ms = (time.perf_counter() - t0) * 1000

    # cold scan (cache disabled → every file parsed)
    ScanCache(path.resolve()).clear()
    tracemalloc.start()
    t0 = time.perf_counter()
    result.findings, result.ast_parsed = _scan_once(path)
    result.cold_ms = (time.perf_counter() - t0) * 1000
    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    result.peak_mb = peak / 1e6

    # warm scan (index facts come from the disk cache)
    t0 = time.perf_counter()
    _scan_once(path)
    result.warm_ms = (time.perf_counter() - t0) * 1000

    # platform graph build
    from forge_doctor_data.analyzers.platform_graph_builder import build_platform_graph
    from forge_doctor_data.core.context import ProjectContext

    t0 = time.perf_counter()
    g = build_platform_graph(ProjectContext(root=path.resolve()))
    result.graph_ms = (time.perf_counter() - t0) * 1000
    result.graph_entities = len(list(g.entities()))
    result.graph_edges = len(list(g.relationships()))

    all_files = [p for p in path.rglob("*") if p.is_file()]
    result.files = len(all_files)
    result.py_files = sum(1 for p in all_files if p.suffix == ".py")

    if budget:
        _check_budget(result, budget)
    return result


def _check_budget(result: BenchResult, budget: dict[str, float]) -> None:
    fails = result.budget_failures
    warm_max = budget.get("warm_ratio_max")
    if warm_max is not None and result.warm_ratio is not None and result.warm_ratio > warm_max:
        fails.append(
            f"warm_ratio {result.warm_ratio:.2f} > {warm_max} "
            f"({result.warm_ms:.0f}ms vs {result.cold_ms:.0f}ms)"
        )
    ast_max = budget.get("ast_parse_max_ratio")
    if ast_max is not None and result.ast_ratio is not None and result.ast_ratio > ast_max:
        fails.append(
            f"ast_parse_ratio {result.ast_ratio:.2f} > {ast_max} "
            f"({result.ast_parsed}/{result.py_files})"
        )
    per_1k = max(1.0, result.files / 1000.0)
    graph_budget = budget.get("graph_ms_per_1k_files")
    if graph_budget is not None and result.graph_ms / per_1k > graph_budget:
        fails.append(f"graph {result.graph_ms:.0f}ms > {graph_budget}ms/1k files")
    cold_budget = budget.get("cold_ms_per_1k_files")
    if cold_budget is not None and result.cold_ms / per_1k > cold_budget:
        fails.append(f"cold {result.cold_ms:.0f}ms > {cold_budget}ms/1k files")


def load_budget(path: Path) -> dict[str, float]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("budget file must be a JSON object")
    return {str(k): float(v) for k, v in raw.items()}


def result_dict(r: BenchResult) -> dict[str, Any]:
    return {
        "files": r.files,
        "py_files": r.py_files,
        "cold_ms": round(r.cold_ms, 1),
        "warm_ms": round(r.warm_ms, 1),
        "warm_ratio": None if r.warm_ratio is None else round(r.warm_ratio, 3),
        "ast_parsed": r.ast_parsed,
        "ast_ratio": None if r.ast_ratio is None else round(r.ast_ratio, 3),
        "graph_ms": round(r.graph_ms, 1),
        "graph_entities": r.graph_entities,
        "graph_edges": r.graph_edges,
        "pack_ms": round(r.pack_ms, 1),
        "packs": r.packs,
        "peak_mb": round(r.peak_mb, 1),
        "findings": r.findings,
        "budget_failures": r.budget_failures,
    }
