from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()
spark.sql("MERGE INTO db.events t USING updates s ON t.id = s.id WHEN MATCHED THEN UPDATE SET *")
spark.sql("MERGE INTO db.events t USING updates s ON t.id = s.id WHEN NOT MATCHED THEN INSERT *")
spark.sql("DELETE FROM db.events WHERE dt < '2024-01-01'")
spark.sql("ALTER TABLE db.events SET TBLPROPERTIES ('format-version'='1')")
