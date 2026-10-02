SELECT device_id, SUM(amount) FROM orders WHERE device_id = 'abc' GROUP BY device_id;
