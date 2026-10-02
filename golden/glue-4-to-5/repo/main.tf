resource "aws_glue_job" "etl" {
  name         = "orders-etl"
  glue_version = "4.0"
  worker_type  = "G.1X"
  default_arguments = {
    "--enable-metrics" = "true"
  }
}
