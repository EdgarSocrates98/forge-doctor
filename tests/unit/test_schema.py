import json
from pathlib import Path

from forge_doctor.core.schema import diff_schemas, parse_schema


def test_avro_parse(tmp_path: Path):
    schema = tmp_path / "orders.avsc"
    schema.write_text(
        json.dumps(
            {
                "type": "record",
                "name": "Order",
                "fields": [
                    {"name": "id", "type": "long"},
                    {"name": "email", "type": ["null", "string"], "default": None},
                ],
            }
        ),
        encoding="utf-8",
    )
    info = parse_schema(schema)
    assert info.source == "avro"
    assert info.columns["id"].nullable is False
    assert info.columns["email"].nullable is True


def test_avro_default_does_not_imply_nullable(tmp_path: Path):
    """`default` aids reader evolution only - the field stays required."""
    schema = tmp_path / "t.avsc"
    schema.write_text(
        json.dumps(
            {
                "type": "record",
                "name": "T",
                "fields": [
                    {"name": "id", "type": "long", "default": 0},
                    {"name": "tag", "type": "null"},
                    {"name": "opt", "type": ["null", "string"]},
                ],
            }
        ),
        encoding="utf-8",
    )
    info = parse_schema(schema)
    assert info.columns["id"].nullable is False  # default != nullable
    assert info.columns["tag"].nullable is True  # literal "null" type
    assert info.columns["opt"].nullable is True  # union containing "null"


def test_sql_ddl_parse(tmp_path: Path):
    ddl = tmp_path / "t.sql"
    ddl.write_text(
        "CREATE TABLE orders (id BIGINT NOT NULL, amount DECIMAL(10,2), tag STRING);",
        encoding="utf-8",
    )
    info = parse_schema(ddl)
    assert info.source == "ddl"
    assert "id" in info.columns and info.columns["id"].nullable is False
    assert info.columns["amount"].nullable is True


def test_json_schema_parse(tmp_path: Path):
    j = tmp_path / "s.json"
    j.write_text(
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {"id": {"type": "integer"}, "note": {"type": "string"}},
                "required": ["id"],
            }
        ),
        encoding="utf-8",
    )
    info = parse_schema(j)
    assert info.columns["id"].nullable is False
    assert info.columns["note"].nullable is True


def test_diff_classifications(tmp_path: Path):
    old = tmp_path / "old.avsc"
    new = tmp_path / "new.avsc"
    base = {
        "type": "record",
        "name": "T",
        "fields": [
            {"name": "id", "type": "int"},
            {"name": "dropped", "type": "binary"},
            {"name": "renamed", "type": "string"},
        ],
    }
    evolved = {
        "type": "record",
        "name": "T",
        "fields": [
            {"name": "id", "type": "long"},  # widening
            {"name": "extra", "type": ["null", "double"], "default": None},
            {"name": "req", "type": "int"},  # new required
            {"name": "renamed_to", "type": "string"},
        ],
    }
    old.write_text(json.dumps(base), encoding="utf-8")
    new.write_text(json.dumps(evolved), encoding="utf-8")
    changes = diff_schemas(parse_schema(old), parse_schema(new))
    by_col = {c.column: c for c in changes}
    assert by_col["id"].classification == "compatible"  # int -> long widening
    assert by_col["dropped"].classification == "breaking"
    assert by_col["extra"].classification == "compatible"  # added nullable
    assert by_col["req"].classification == "potentially breaking"  # added required
    # rename detection: dropped+added with same type
    assert by_col["renamed -> renamed_to"].kind == "rename?"
    assert by_col["renamed -> renamed_to"].classification == "breaking"
