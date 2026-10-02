resource "snowflake_warehouse" "analytics" {
  name           = "ANALYTICS_WH"
  warehouse_size = "LARGE"
}
