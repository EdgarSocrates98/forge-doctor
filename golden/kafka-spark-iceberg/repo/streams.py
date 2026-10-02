from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()
orders = (spark.readStream.format("kafka")
          .option("subscribe", "orders")
          .option("kafka.bootstrap.servers", "b1:9092,b2:9092")
          .option("maxOffsetsPerTrigger", "100000")
          .load())
q = (orders.writeStream.format("iceberg")
     .outputMode("append")
     .option("checkpointLocation", "s3://checkpoints/orders")
     .toTable("lake.db.orders"))
