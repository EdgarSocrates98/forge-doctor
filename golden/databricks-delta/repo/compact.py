from delta.tables import DeltaTable

dt = DeltaTable.forName(spark, "silver.events")
dt.optimize().executeCompaction()
dt.vacuum(168)
spark.sql("MERGE INTO silver.events t USING staging s ON t.id = s.id WHEN MATCHED THEN UPDATE SET *")
