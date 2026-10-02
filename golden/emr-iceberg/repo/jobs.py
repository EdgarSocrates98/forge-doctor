from pyspark.sql import SparkSession

spark = (SparkSession.builder
         .config("spark.sql.catalog.lake", "org.apache.iceberg.spark.SparkCatalog")
         .config("spark.sql.catalog.lake.catalog-impl", "org.apache.iceberg.aws.glue.GlueCatalog")
         .config("spark.sql.catalog.lake.warehouse", "s3://lake/warehouse")
         .getOrCreate())
spark.sql("CREATE TABLE IF NOT EXISTS lake.db.events (id bigint, dt date) USING ICEBERG")
spark.sql("INSERT INTO lake.db.events SELECT * FROM staging.events")
