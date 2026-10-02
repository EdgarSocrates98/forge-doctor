resource "aws_redshift_cluster" "analytics" {
  cluster_identifier = "analytics"
  node_type          = "ra3.xlplus"
  database_name      = "warehouse"
  master_username    = "admin"
}

resource "aws_redshiftserverless_workgroup" "adhoc" {
  workgroup_name = "adhoc"
  namespace_name = "analytics"
}
