resource "aws_dynamodb_table" "orders" {
  name             = "orders"
  billing_mode     = "PAY_PER_REQUEST"
  hash_key         = "order_id"
  stream_enabled   = true
  stream_view_type = "NEW_AND_OLD_IMAGES"

  attribute {
    name = "order_id"
    type = "S"
  }
}

resource "aws_lambda_function" "consumer" {
  function_name = "orders-consumer"
  runtime       = "python3.11"
  handler       = "handler.main"
  reserved_concurrent_executions = 10
}

resource "aws_lambda_event_source_mapping" "orders_stream" {
  event_source_arn                   = aws_dynamodb_table.orders.stream_arn
  function_name                      = aws_lambda_function.consumer.arn
  starting_position                  = "TRIM_HORIZON"
  batch_size                         = 500
  maximum_batching_window_in_seconds = 5
}
