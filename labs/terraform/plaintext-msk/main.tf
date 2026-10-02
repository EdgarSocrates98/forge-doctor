resource "aws_msk_cluster" "bus" {
  cluster_name = "bus"
}

resource "aws_msk_configuration" "cfg" {
  name = "cfg"
  server_properties = "auto.create.topics.enable=true"
}
