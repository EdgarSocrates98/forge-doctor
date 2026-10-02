resource "aws_emr_cluster" "lake" {
  name          = "iceberg-lake"
  release_label = "emr-6.15.0"
  applications  = ["Spark", "Iceberg", "Hive"]

  master_instance_group {
    instance_type = "m5.xlarge"
  }
  core_instance_group {
    instance_type = "m5.2xlarge"
    instance_count = 4
  }
}
