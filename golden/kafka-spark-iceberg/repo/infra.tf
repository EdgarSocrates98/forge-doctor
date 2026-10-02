resource "aws_msk_cluster" "bus" {
  cluster_name = "orders-bus"
  encryption_info {
    encryption_in_transit {
      client_broker = "TLS"
    }
  }
}
