"""Unit tests for the Spark AST checks (SPARK###)."""

from __future__ import annotations

from pathlib import Path

from forge_doctor.checks.spark import (
    CHECKS,
    ActionInLoop,
    CacheWithoutUnpersist,
    CollectToDriver,
    GlobalSort,
    JoinWithoutCondition,
    PySparkUsage,
    PythonUdf,
    RddEscapeHatch,
    SinglePartition,
    ToPandas,
    WithColumnInLoop,
    analyze_project,
)
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.models import Severity

JOB = """\
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

df = spark.read.parquet("s3://bucket/input")
rows = df.collect()
pandas_df = df.toPandas()
df.repartition(1).write.parquet("s3://bucket/output")
"""

LOOP_JOB = """\
import pyspark


def dump(df, tables):
    for table in tables:
        df.write.save(table)
"""

UDF_JOB = """\
from pyspark.sql.functions import udf


@udf("string")
def shout(value: str) -> str:
    return value.upper()
"""

CACHE_JOB = """\
from pyspark.sql import DataFrame


def process(df: DataFrame) -> int:
    df.cache()
    return df.count()
"""

UNPERSIST_JOB = """\
from pyspark.sql import DataFrame


def process(df: DataFrame) -> int:
    df.cache()
    count = df.count()
    df.unpersist()
    return count
"""

CLEAN_PYSPARK = """\
from pyspark.sql import SparkSession


def load(spark: SparkSession):
    return spark.read.parquet("s3://bucket/input").filter("x > 0")
"""

PLAIN_PYTHON = """\
items = [1, 2, 3]
result = items.collect()
"""


def make_context(tmp_path: Path, **sources: str) -> ProjectContext:
    for name, source in sources.items():
        filename = name if name.endswith(".py") else f"{name}.py"
        path = tmp_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


def test_analyze_project_buckets(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, job=JOB)
    buckets = analyze_project(ctx)
    assert buckets["collect"] == [(Path("job.py"), 6, "df")]
    assert buckets["toPandas"] == [(Path("job.py"), 7, "df")]
    assert buckets["repartition(1)"] == [(Path("job.py"), 8, "df")]


class TestCollectToDriver:
    """SPARK001."""

    def test_collect_reports_file_and_line(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, job=JOB)
        (result,) = CollectToDriver().run(ctx)
        assert result.check_id == "SPARK001"
        assert result.severity == Severity.WARNING
        assert result.file == Path("job.py")
        assert result.line == 6
        assert "driver" in result.message
        assert result.recommendation is not None

    def test_plain_python_collect_ignored(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, utils=PLAIN_PYTHON)
        assert CollectToDriver().run(ctx) == []

    def test_cross_file_producer_opens_gate(self, tmp_path: Path) -> None:
        """Regression: job.py has NO pyspark import but gets a proven
        DataFrame from reader.load_orders - the index's cross-module
        propagation must open the spark gate."""
        ctx = make_context(
            tmp_path,
            reader=(
                "from pyspark.sql import SparkSession\n"
                "\n"
                "def load_orders(spark):\n"
                '    return spark.read.parquet("/orders")\n'
            ),
            job=("from reader import load_orders\n\ndf = load_orders(spark)\ndf.collect()\n"),
        )
        results = CollectToDriver().run(ctx)
        assert len(results) == 1
        assert results[0].file == Path("job.py")
        assert results[0].line == 4

    def test_non_spark_collect_still_ignored(self, tmp_path: Path) -> None:
        """Name heuristics alone must not open the gate (polars/pandas)."""
        ctx = make_context(
            tmp_path,
            job=('import polars as pl\n\ndf = pl.scan_parquet("x")\nresult = df.collect()\n'),
        )
        assert CollectToDriver().run(ctx) == []


class TestToPandas:
    """SPARK002."""

    def test_topandas_reports_file_and_line(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, job=JOB)
        (result,) = ToPandas().run(ctx)
        assert result.severity == Severity.WARNING
        assert result.file == Path("job.py")
        assert result.line == 7


class TestSinglePartition:
    """SPARK003."""

    def test_repartition_one(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, job=JOB)
        (result,) = SinglePartition().run(ctx)
        assert result.severity == Severity.WARNING
        assert result.file == Path("job.py")
        assert result.line == 8

    def test_coalesce_one(self, tmp_path: Path) -> None:
        source = "import pyspark.sql\n\ndef shrink(df):\n    return df.coalesce(1)\n"
        ctx = make_context(tmp_path, job=source)
        (result,) = SinglePartition().run(ctx)
        assert result.severity == Severity.WARNING
        assert result.line == 4

    def test_repartition_many_not_flagged(self, tmp_path: Path) -> None:
        source = "import pyspark\n\ndef spread(df):\n    return df.repartition(8)\n"
        ctx = make_context(tmp_path, job=source)
        assert SinglePartition().run(ctx) == []


class TestPythonUdf:
    """SPARK004."""

    def test_udf_info_with_line(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, job=UDF_JOB)
        (result,) = PythonUdf().run(ctx)
        assert result.severity == Severity.INFO
        assert result.file == Path("job.py")
        assert result.line == 4


class TestActionInLoop:
    """SPARK005."""

    def test_for_loop_action(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, job=LOOP_JOB)
        (result,) = ActionInLoop().run(ctx)
        assert result.severity == Severity.WARNING
        assert result.file == Path("job.py")
        assert result.line == 6
        assert result.recommendation is not None

    def test_while_loop_action(self, tmp_path: Path) -> None:
        source = (
            "import pyspark\n\n"
            "def poll(df, pending):\n"
            "    while pending:\n"
            "        df.count()\n"
            "        pending.pop()\n"
        )
        ctx = make_context(tmp_path, job=source)
        (result,) = ActionInLoop().run(ctx)
        assert result.line == 5

    def test_action_outside_loop_not_flagged(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, job=CACHE_JOB)
        assert ActionInLoop().run(ctx) == []


class TestCacheWithoutUnpersist:
    """SPARK006."""

    def test_cache_without_unpersist_info(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, job=CACHE_JOB)
        (result,) = CacheWithoutUnpersist().run(ctx)
        assert result.severity == Severity.INFO
        assert result.file == Path("job.py")
        assert result.line == 5
        assert result.recommendation is not None
        assert "unpersist" in result.recommendation

    def test_cache_with_unpersist_clean(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, job=UNPERSIST_JOB)
        assert CacheWithoutUnpersist().run(ctx) == []

    def test_unpersist_in_other_file_does_not_clear(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, a=CACHE_JOB, b=UNPERSIST_JOB)
        (result,) = CacheWithoutUnpersist().run(ctx)
        assert result.file == Path("a.py")


class TestPySparkUsage:
    """SPARK007."""

    def test_counts_pyspark_files(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, a=JOB, b=CLEAN_PYSPARK, c=PLAIN_PYTHON)
        (result,) = PySparkUsage().run(ctx)
        assert result.severity == Severity.PASS
        assert result.message == "2 PySpark files analyzed"

    def test_no_pyspark_info(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path, utils=PLAIN_PYTHON)
        (result,) = PySparkUsage().run(ctx)
        assert result.severity == Severity.INFO
        assert result.message == "no PySpark usage detected"


def test_syntax_error_file_skipped(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, broken="import pyspark\ndef broken(:\n")
    assert CollectToDriver().run(ctx) == []
    (usage,) = PySparkUsage().run(ctx)
    assert usage.severity == Severity.INFO


def test_checks_metadata() -> None:
    assert {check.id for check in CHECKS} == {
        "SPARK001",
        "SPARK002",
        "SPARK003",
        "SPARK004",
        "SPARK005",
        "SPARK006",
        "SPARK007",
        "SPARK008",
        "SPARK009",
        "SPARK010",
        "SPARK011",
    }
    assert all(check.category == "spark" for check in CHECKS)


# AST v2 light - receiver resolution & literal receivers


LITERAL_RECEIVER = """import pyspark.sql.functions as F

[1, 2, 3].collect()
"abc".count()
df.collect()
"""


def test_literal_receivers_not_flagged(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, job=LITERAL_RECEIVER)
    results = CollectToDriver().run(ctx)
    # Only df.collect() is a finding; [1,2,3].collect() and "abc".count() skip.
    assert len(results) == 1
    assert results[0].line == 5


CHAINED_DF = """from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()
orders = spark.read.parquet("s3://x")
result = orders.filter("id > 0").collect()
"""


def test_chained_call_receiver_resolves(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, job=CHAINED_DF)
    results = CollectToDriver().run(ctx)
    assert len(results) == 1
    assert results[0].line == 5
    # Receiver resolves to `orders`, a tracked DataFrame name.
    assert results[0].confidence is not None
    assert results[0].confidence.value == "high"


SAME_VAR_CACHE = """import pyspark

df_a.cache()
df_b.cache()
df_a.unpersist()
"""


def test_spark006_same_variable_pairing(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, job=SAME_VAR_CACHE)
    results = CacheWithoutUnpersist().run(ctx)
    # df_b cached without df_b.unpersist(); df_a is covered.
    assert len(results) == 1
    assert results[0].line == 4
    assert "df_b" in results[0].message


PERF_PACK = """import pyspark

df.rdd
result = df.crossJoin(other)
result2 = df.join(other)
result3 = df.join(other, "key")
df.orderBy("ts")
for i in range(3):
    df = df.withColumn(f"c{i}", df.c0 + i)
"""


def test_spark008_rdd(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, job=PERF_PACK)
    results = RddEscapeHatch().run(ctx)
    assert len(results) == 1
    assert results[0].line == 3


def test_spark009_joins(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, job=PERF_PACK)
    results = JoinWithoutCondition().run(ctx)
    lines = {r.line for r in results}
    assert lines == {4, 5}  # crossJoin + join without condition; join(other,"key") OK


def test_spark010_global_sort(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, job=PERF_PACK)
    results = GlobalSort().run(ctx)
    assert len(results) == 1
    assert results[0].line == 7


def test_spark011_with_column_in_loop(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, job=PERF_PACK)
    results = WithColumnInLoop().run(ctx)
    assert len(results) == 1
    assert results[0].line == 9
