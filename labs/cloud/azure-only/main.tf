resource "azurerm_storage_account" "lake" {
  name     = "stlakeprod"
  location = "westeurope"
}

resource "azurerm_eventhub_namespace" "bus" {
  name     = "eh-prod"
  location = "westeurope"
}

resource "azurerm_synapse_workspace" "syn" {
  name = "syn-prod"
}

resource "azurerm_purview_account" "pv" {
  name = "purview-prod"
}
