"""Sample PySpark module with anti-patterns: collect() and repartition(1)."""

import pyspark.sql


def collect_all(df: "pyspark.sql.DataFrame") -> list[object]:
    """Pull the whole DataFrame to the driver and squash partitions."""
    single_partition = df.repartition(1)
    return single_partition.collect()
