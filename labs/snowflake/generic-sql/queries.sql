-- Plain ANSI/Spark SQL - NOT Snowflake. None of this may trip SNOW rules.
CREATE VIEW v AS SELECT * FROM orders;
SELECT * FROM staging;
