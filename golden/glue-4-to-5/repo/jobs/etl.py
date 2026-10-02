import sys
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()
glue = GlueContext(spark.sparkContext)
job = Job(glue)
job.init(sys.argv[1:])
dyf = glue.create_dynamic_frame.from_catalog(database="sales", table_name="orders")
df = dyf.toDF()
df.repartition(1).write.mode("overwrite").parquet("s3://warehouse/orders")
job.commit()
