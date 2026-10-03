resource "aws_s3_bucket" "raw" {
  bucket = "raw-orders"
}
resource "aws_kinesis_stream" "events" {
  name = "events"
}
