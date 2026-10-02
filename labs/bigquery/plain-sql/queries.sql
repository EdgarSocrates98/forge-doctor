CREATE TABLE orders (id INT, total DOUBLE);
CREATE VIEW order_totals AS SELECT id, total FROM orders;
SELECT id, total FROM orders WHERE id > 10;
