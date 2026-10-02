CREATE TABLE customers (id INT, name STRING);

INSERT INTO orders SELECT * FROM customers;
