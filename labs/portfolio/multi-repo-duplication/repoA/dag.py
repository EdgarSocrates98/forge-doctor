from airflow import DAG
from datetime import datetime

with DAG(dag_id="etl", start_date=datetime(2026, 1, 1)) as dag:
    pass
