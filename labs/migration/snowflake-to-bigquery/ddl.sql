CREATE SCHEMA analytics.core;
CREATE TABLE analytics.core.orders (order_id NUMBER, amount NUMBER);
CREATE STAGE analytics.core.s3_stage URL='s3://bucket/inbound';
CREATE STREAM analytics.core.orders_stream ON TABLE analytics.core.orders;
CREATE TASK analytics.core.hourly_load
  WAREHOUSE = ANALYTICS_WH
  SCHEDULE = '60 MINUTE'
AS
  COPY INTO analytics.core.orders FROM @analytics.core.s3_stage;
