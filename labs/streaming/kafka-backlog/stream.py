from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()
ss = (spark.readStream.format("kafka")
      .option("subscribe", "orders")
      .option("kafka.bootstrap.servers", "b1:9092")
      .load())
q = (ss.writeStream.format("delta")
     .option("checkpointLocation", "s3://ck/orders")
     .start("s3://out/orders"))
