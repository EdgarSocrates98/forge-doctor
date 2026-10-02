import awsglue
from awsglue.context import GlueContext

glue = GlueContext(spark.sparkContext)
dyf = glue.create_dynamic_frame.from_catalog(database="db", table_name="t")
dyf.toDF().coalesce(1).write.parquet("s3://b/out")
