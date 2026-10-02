"""DynamoDBProjectModel extraction tests (spec 176)."""

from __future__ import annotations

from pathlib import Path

from forge_doctor.analyzers.dynamodb_model import dynamodb_model
from forge_doctor.core.context import ProjectContext


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


TF_TABLE = """
resource "aws_dynamodb_table" "orders" {
  name         = "orders"
  hash_key     = "pk"
  range_key    = "sk"
  billing_mode = "PAY_PER_REQUEST"

  global_secondary_index {
    name            = "by_status"
    hash_key        = "status"
    range_key       = "sk"
    projection_type = "ALL"
  }
  local_secondary_index {
    name            = "by_ts"
    range_key       = "ts"
    projection_type = "KEYS_ONLY"
  }
  stream_enabled   = true
  stream_view_type = "NEW_AND_OLD_IMAGES"
  ttl {
    attribute_name = "expires"
    enabled        = true
  }
  server_side_encryption {
    enabled = true
  }
  point_in_time_recovery {
    enabled = true
  }
  replica {
    region_name = "us-west-2"
  }
}
"""


def test_tf_table_extracted(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"infra.tf": TF_TABLE})
    model = dynamodb_model(ctx)
    assert len(model.tables) == 1
    t = model.tables[0]
    assert t.name == "orders"
    assert t.source == "terraform"
    assert t.partition_key == "pk"
    assert t.sort_key == "sk"
    assert t.billing_mode == "PAY_PER_REQUEST"
    assert t.ttl_attribute == "expires"
    assert t.encrypted is True
    assert t.pitr is True


def test_tf_indexes(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"infra.tf": TF_TABLE})
    t = dynamodb_model(ctx).tables[0]
    kinds = {g.name: g.kind for g in t.indexes}
    assert kinds == {"by_status": "gsi", "by_ts": "lsi"}
    gsi = next(g for g in t.indexes if g.kind == "gsi")
    assert gsi.partition_key == "status" and gsi.sort_key == "sk"


def test_tf_global_table_mrec_default(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"infra.tf": TF_TABLE})
    model = dynamodb_model(ctx)
    assert model.tables[0].global_mode == "mrec"
    assert model.global_tables[0].regions == ["us-west-2"]


def test_tf_stream_detected(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"infra.tf": TF_TABLE})
    streams = dynamodb_model(ctx).streams
    assert streams and streams[0].view_type == "NEW_AND_OLD_IMAGES"


def test_cfn_json_table(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "template.json": """{
  "Resources": {
    "Orders": {
      "Type": "AWS::DynamoDB::Table",
      "Properties": {
        "TableName": "orders",
        "BillingMode": "PAY_PER_REQUEST",
        "KeySchema": [
          {"AttributeName": "pk", "KeyType": "HASH"},
          {"AttributeName": "sk", "KeyType": "RANGE"}
        ],
        "GlobalSecondaryIndexes": [
          {"IndexName": "by_status", "KeySchema": [
            {"AttributeName": "status", "KeyType": "HASH"}
          ], "Projection": {"ProjectionType": "ALL"}}
        ],
        "StreamSpecification": {"StreamViewType": "KEYS_ONLY"}
      }
    }
  }
}"""
        },
    )
    model = dynamodb_model(ctx)
    t = model.tables[0]
    assert t.name == "orders" and t.partition_key == "pk" and t.sort_key == "sk"
    assert t.indexes[0].name == "by_status"
    assert model.streams[0].view_type == "KEYS_ONLY"


def test_cfn_global_table_mrsc(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "template.json": """{
  "Resources": {
    "T": {
      "Type": "AWS::DynamoDB::GlobalTable",
      "Properties": {
        "TableName": "gt",
        "MultiRegionConsistency": "STRONG",
        "Replicas": [{"Region": "us-east-1"}, {"Region": "eu-west-1"}]
      }
    }
  }
}"""
        },
    )
    g = dynamodb_model(ctx).global_tables[0]
    assert g.mode == "mrsc" and len(g.regions) == 2


def test_boto3_ops_extracted(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "job.py": (
                "import boto3\n"
                'dynamodb = boto3.resource("dynamodb")\n'
                'table = dynamodb.Table("orders")\n'
                'client = boto3.client("dynamodb")\n'
                'table.get_item(Key={"pk": "USER#1"})\n'
                'table.query(KeyConditionExpression="pk = :p")\n'
                "client.transact_write_items(TransactItems=[])\n"
            )
        },
    )
    model = dynamodb_model(ctx)
    ops = {a.op for a in model.accesses}
    assert ops == {"get_item", "query", "transact_write_items"}
    assert model.transactions_used
    get = next(a for a in model.accesses if a.op == "get_item")
    assert get.table == "orders" and get.has_key_condition
    query = next(a for a in model.accesses if a.op == "query")
    assert ("lit", "pk = :p") in query.key_literals


def test_table_factory_without_dynamodb_silent(tmp_path: Path) -> None:
    """``x.Table`` on an unrelated receiver must not bind."""
    ctx = make_context(
        tmp_path,
        {"job.py": 'catalog = catalog()\nt = catalog.Table("t")\nt.scan()\n'},
    )
    assert not dynamodb_model(ctx).accesses


def test_entity_prefixes(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "job.py": 'import boto3\nd = boto3.client("dynamodb")\n'
            'key = f"USER#{uid}"\nkey2 = "ORDER#42"\n'
        },
    )
    entities = dynamodb_model(ctx).single_table.entities
    assert "USER" in entities and "ORDER" in entities


def test_no_dynamodb_clean(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"a.py": "x = 1\n"})
    model = dynamodb_model(ctx)
    assert not model.has_dynamodb
