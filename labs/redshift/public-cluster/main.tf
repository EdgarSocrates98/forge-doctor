resource "aws_redshift_cluster" "analytics" {
  cluster_identifier  = "analytics"
  node_type           = "ra3.xlplus"
  database_name       = "analytics_db"
  publicly_accessible = true
  encrypted           = false
}
