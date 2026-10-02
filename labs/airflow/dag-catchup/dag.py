from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator
from airflow.models import Variable
from datetime import datetime

TOKEN = Variable.get("api_token")

with DAG(
    dag_id="daily_load",
    start_date=datetime.now(),
    schedule_interval="@daily",
    catchup=True,
    default_args={"retries": 3},
) as dag:
    t = BashOperator(task_id="load", bash_command="echo hi")
    t2 = PythonOperator(task_id="wait", python_callable=lambda: 1)
