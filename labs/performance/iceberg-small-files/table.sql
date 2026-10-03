CREATE TABLE iceberg.db.events (id BIGINT, ts TIMESTAMP)
PARTITIONED BY (days(ts));
