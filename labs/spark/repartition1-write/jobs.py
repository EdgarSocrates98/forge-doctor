from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()
df = spark.read.parquet("s3://bucket/in")
df.repartition(1).write.mode("overwrite").parquet("s3://bucket/out")
df2 = df.join(df, "id").collect()
