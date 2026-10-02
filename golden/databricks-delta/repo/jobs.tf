resource "databricks_job" "nightly" {
  name = "nightly-compaction"
  job_cluster {
    spark_version = "13.3.x-scala2.12"
    node_type_id  = "i3.xlarge"
    num_workers   = 4
  }
  task {
    task_key = "compact"
    job_cluster_key = "nightly"
    notebook_task { notebook_path = "/nb/compact" }
  }
}
