resource "azurerm_eventhub" "orders" {
  name              = "orders"
  partition_count   = 4
  resource_group_name = "rg-data"
}
