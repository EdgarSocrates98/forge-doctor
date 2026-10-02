CREATE TABLE fact (id INT, amount DOUBLE) DISTSTYLE EVEN;
SELECT f.id FROM fact f JOIN dim d ON f.id = d.id;
