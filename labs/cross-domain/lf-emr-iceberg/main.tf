resource "aws_emr_cluster" "lake" {
  name          = "lake"
  release_label = "emr-6.15.0"
  applications  = ["Spark", "Iceberg"]
}

resource "aws_lakeformation_permissions" "g" {
  principal = "arn:aws:iam::111122223333:role/EMR"
  permissions = ["ALL"]
  table {
    database_name = "lake"
    name          = "events"
  }
}
