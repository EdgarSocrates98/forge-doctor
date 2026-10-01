"""Nightly ETL AWS Glue job: catalog table to partitioned parquet."""

import sys

from awsglue.context import GlueContext
from awsglue.dynamicframe import DynamicFrame
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext

sc = SparkContext()
glue_context = GlueContext(sc)
job = Job(glue_context)

args = getResolvedOptions(sys.argv, ["JOB_NAME", "SOURCE_TABLE", "TARGET_PATH"])
job.init(args["JOB_NAME"], args)

frame: DynamicFrame = glue_context.create_dynamic_frame.from_catalog(
    database="nightly",
    table_name=args["SOURCE_TABLE"],
)
df = frame.toDF()
df.write.mode("overwrite").parquet(args["TARGET_PATH"])

job.commit()
