INSERT INTO iceberg.marts.daily_orders
SELECT order_id, customer_id FROM iceberg.staging.orders;

SELECT n_name FROM tpch.tiny.nation;

SELECT * FROM bogus.dw.orders o JOIN iceberg.staging.customers c ON o.cust = c.id;
