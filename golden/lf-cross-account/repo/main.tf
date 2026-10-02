resource "aws_lakeformation_data_lake_settings" "main" {
  admins = ["arn:aws:iam::111122223333:role/LFAdmin"]
}

resource "aws_lakeformation_resource" "lake" {
  arn      = "s3://corp-lake"
  role_arn = "arn:aws:iam::111122223333:role/LFReg"
}

resource "aws_glue_catalog_database" "shared_link" {
  name = "consumer_db_link"
  target_database {
    catalog_id    = "999988887777"
    database_name = "producer_db"
  }
}

resource "aws_lakeformation_permissions" "reader" {
  principal   = "arn:aws:iam::111122223333:role/Reader"
  permissions = ["SELECT"]
  table {
    database_name = "consumer_db_link"
    name          = "events"
  }
}
