resource "azurerm_storage_account" "lake" {
  name           = "stbadlake"
  location       = "westeurope"
  is_hns_enabled = false
}

resource "azurerm_storage_data_lake_gen2_filesystem" "fs" {
  name               = "curated"
  storage_account_id = azurerm_storage_account.lake.id
}
