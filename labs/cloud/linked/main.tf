resource "google_storage_bucket" "raw" {
  name     = "raw-gcs"
  location = "EU"
}

resource "azurerm_storage_account" "lake" {
  name     = "stlakeprod"
  location = "westeurope"
}

resource "azurerm_cosmosdb_account" "db" {
  name     = "cosmos-prod"
  location = "westeurope"

  geo_location {
    location          = "northeurope"
    failover_priority = 0
  }
}
