from airflow import DAG
from airflow.providers.amazon.aws.operators.glue import GlueJobOperator
from airflow.providers.amazon.aws.operators.athena import AthenaOperator
from datetime import datetime, timedelta

with DAG(
    dag_id="orders_pipeline",
    start_date=datetime(2024, 1, 1),
    schedule_interval="@daily",
    catchup=False,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
) as dag:
    extract = GlueJobOperator(
        task_id="glue_etl",
        job_name="orders-etl",
        aws_conn_id="aws_default",
    )
    aggregate = AthenaOperator(
        task_id="rollup",
        query="INSERT INTO analytics.daily SELECT dt, count(*) FROM sales.orders GROUP BY dt",
        database="analytics",
        output_location="s3://athena-results/",
    )
    extract >> aggregate
