from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()
spark.conf.set("spark.sql.catalog.lake", "org.apache.iceberg.spark.SparkCatalog")
spark.conf.set("spark.sql.catalog.lake.catalog-impl", "org.apache.iceberg.aws.glue.GlueCatalog")
spark.sql("INSERT INTO lake.db.events SELECT * FROM src")
