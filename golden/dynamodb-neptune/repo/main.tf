resource "aws_dynamodb_table" "accounts" {
  name             = "accounts"
  billing_mode     = "PAY_PER_REQUEST"
  hash_key         = "account_id"
  stream_enabled   = true
  stream_view_type = "NEW_AND_OLD_IMAGES"

  attribute {
    name = "account_id"
    type = "S"
  }
}

resource "aws_neptune_cluster" "graph" {
  cluster_identifier = "transfers-graph"
}

resource "aws_lambda_function" "syncer" {
  function_name = "graph-syncer"
  runtime       = "python3.11"
  handler       = "sync.handler"
}

resource "aws_lambda_event_source_mapping" "accounts_stream" {
  event_source_arn  = aws_dynamodb_table.accounts.stream_arn
  function_name     = aws_lambda_function.syncer.arn
  starting_position = "LATEST"
  batch_size        = 100
}
