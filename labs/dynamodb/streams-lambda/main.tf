resource "aws_dynamodb_table" "orders" {
  name           = "orders"
  stream_enabled = true
  stream_view_type = "NEW_IMAGE"
}

resource "aws_lambda_function" "consumer" {
  function_name = "consumer"
  runtime       = "python3.9"
}

resource "aws_lambda_event_source_mapping" "esm" {
  event_source_arn  = aws_dynamodb_table.orders.stream_arn
  function_name     = aws_lambda_function.consumer.arn
  starting_position = "TRIM_HORIZON"
  batch_size        = 2000
}
