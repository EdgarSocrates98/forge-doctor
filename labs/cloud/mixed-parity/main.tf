resource "aws_s3_bucket" "raw" {
  bucket = "raw-aws"
  region = "eu-west-1"
}

resource "azurerm_storage_account" "lake" {
  name     = "stlakeprod"
  location = "westeurope"
}

resource "aws_kinesis_stream" "events" {
  name = "events-aws"
}

resource "azurerm_cosmosdb_account" "db" {
  name     = "cosmos-prod"
  location = "westeurope"
}
