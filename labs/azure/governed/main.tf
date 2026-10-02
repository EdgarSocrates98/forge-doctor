resource "azurerm_storage_account" "lake" {
  name           = "stgoodlake"
  location       = "westeurope"
  is_hns_enabled = true
}

resource "azurerm_eventhub" "events" {
  name                       = "events"
  message_retention_in_days  = 7
  capture_description {
    enabled = true
  }
}

resource "azurerm_purview_account" "pv" {
  name = "purview-prod"
}
