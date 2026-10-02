resource "aws_lakeformation_resource" "loc" {
  arn      = "s3://data-lake"
  role_arn = "arn:aws:iam::111122223333:role/LFReg"
}

resource "aws_glue_catalog_database" "link" {
  name = "shared_link"
  target_database {
    catalog_id    = "999988887777"
    database_name = "shared_db"
  }
}
