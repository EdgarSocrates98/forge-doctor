resource "snowflake_warehouse" "wh" {
  name           = "ANALYTICS_WH"
  warehouse_size = "LARGE"
}

resource "snowflake_database" "db" {
  name = "ANALYTICS"
}
