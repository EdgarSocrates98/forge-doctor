resource "google_storage_bucket" "raw" {
  name = "raw-orders"
}
resource "kafka_topic" "events" {
  name = "events"
}
resource "azurerm_data_factory_pipeline" "etl" {
  name = "etl"
}
