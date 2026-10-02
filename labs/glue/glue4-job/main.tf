resource "aws_glue_job" "etl" {
  name         = "etl"
  glue_version = "4.0"
  default_arguments = {
    "--job-language" = "python"
  }
}
