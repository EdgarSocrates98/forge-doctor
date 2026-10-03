"""Schema extraction and compatibility diffing.

Supported sources: Avro ``.avsc``, JSON Schema ``.json``, SQL DDL
``CREATE TABLE``, and dbt ``schema.yml`` (via the optional ``pyyaml`` extra).
Diffing classifies changes as compatible / potentially-breaking / breaking so
contract changes are caught before a deploy.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

WIDENING = {
    ("int", "bigint"),
    ("int", "long"),
    ("integer", "bigint"),
    ("float", "double"),
    ("int", "double"),
    ("bigint", "double"),
    ("string", "text"),
    ("varchar", "string"),
}

_BREAKING = "breaking"
_COMPATIBLE = "compatible"
_MAYBE = "potentially breaking"


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    nullable: bool = True


@dataclass(frozen=True)
class SchemaChange:
    kind: str  # "added" | "dropped" | "type" | "nullability" | "rename?"
    column: str
    detail: str
    classification: str  # compatible | potentially breaking | breaking


@dataclass(frozen=True)
class SchemaInfo:
    source: str  # "avro" | "json-schema" | "ddl" | "dbt" | "unknown"
    name: str
    columns: dict[str, Column]


def parse_schema(path: Path) -> SchemaInfo:
    """Best-effort schema parse by file extension/content."""
    text = path.read_text(encoding="utf-8", errors="replace")
    suffix = path.suffix.lower()
    if suffix == ".avsc":
        return _parse_avro(text, path)
    if suffix in {".yml", ".yaml"}:
        return _parse_dbt(text, path)
    if suffix == ".sql":
        return _parse_ddl(text, path)
    if suffix == ".json":
        payload = json.loads(text)
        if "properties" in payload or "$schema" in payload:
            return _parse_json_schema(payload, path)
        if "fields" in payload:
            return _parse_avro(text, path)
    return SchemaInfo(source="unknown", name=path.stem, columns={})


def _norm_type(t: object) -> str:
    """Normalize a type token across formats."""
    if isinstance(t, dict):
        t = t.get("type", t)
    if isinstance(t, list):  # avro union [null, x]
        non_null = [x for x in t if x != "null"]
        t = non_null[0] if non_null else "null"
    t = str(t).strip().lower()
    t = re.sub(r"\(\s*\d+\s*(,\s*\d+\s*)?\)", "", t)  # decimal(10,2) -> decimal
    return {"integer": "int", "long": "bigint", "character varying": "varchar"}.get(t, t)


def _parse_avro(text: str, path: Path) -> SchemaInfo:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return SchemaInfo("avro", path.stem, {})
    columns: dict[str, Column] = {}
    for f in payload.get("fields", []):
        if not isinstance(f, dict):
            continue
        t = f.get("type")
        # Avro spec: nullable iff the field type is a union containing
        # "null" (or is the literal "null"). A ``default`` only helps
        # reader-side evolution - it does NOT make a field nullable.
        nullable = (isinstance(t, list) and "null" in t) or t == "null"
        columns[str(f["name"])] = Column(str(f["name"]), _norm_type(t), nullable)
    return SchemaInfo("avro", str(payload.get("name", path.stem)), columns)


def _parse_json_schema(payload: dict[str, Any], path: Path) -> SchemaInfo:
    props = payload.get("properties", {})
    required = set(payload.get("required", []))
    columns = {
        name: Column(
            name,
            _norm_type(spec.get("type", "any") if isinstance(spec, dict) else spec),
            name not in required,
        )
        for name, spec in props.items()
        if isinstance(name, str)
    }
    return SchemaInfo("json-schema", str(payload.get("title", path.stem)), columns)


def _parse_ddl(text: str, path: Path) -> SchemaInfo:
    """``CREATE TABLE x (col TYPE [NOT NULL], ...)`` - first table wins."""
    match = re.search(
        r"(?i)create\s+table\s+(?:if\s+not\s+exists\s+)?([\w.\"`]+)\s*\((.*?)\)\s*[;)]?",
        text,
        re.DOTALL,
    )
    if not match:
        return SchemaInfo("ddl", path.stem, {})
    name, body = match.group(1).strip('"`'), match.group(2)
    columns: dict[str, Column] = {}
    for piece in _split_columns(body):
        parts = piece.split()
        if len(parts) < 2 or parts[0].upper() in {
            "PRIMARY",
            "FOREIGN",
            "UNIQUE",
            "CONSTRAINT",
            "KEY",
            "PARTITION",
        }:
            continue
        col_name = parts[0].strip('"`')
        col_type = _norm_type(" ".join(parts[1:2]) if len(parts) > 1 else "?")
        nullable = "NOT NULL" not in piece.upper()
        columns[col_name] = Column(col_name, col_type, nullable)
    return SchemaInfo("ddl", name, columns)


def _split_columns(body: str) -> Iterable[str]:
    depth = 0
    start = 0
    for i, ch in enumerate(body):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            yield body[start:i]
            start = i + 1
    yield body[start:]


def _parse_dbt(text: str, path: Path) -> SchemaInfo:
    try:
        import yaml
    except ImportError:
        return SchemaInfo("dbt", path.stem, {})
    payload = yaml.safe_load(text) or {}
    models = payload.get("models", [])
    if not isinstance(models, list) or not models:
        return SchemaInfo("dbt", path.stem, {})
    model = models[0] if isinstance(models[0], dict) else {}
    columns = {
        str(c.get("name")): Column(str(c.get("name")), str(c.get("data_type", "?")), True)
        for c in model.get("columns", [])
        if isinstance(c, dict) and c.get("name")
    }
    return SchemaInfo("dbt", str(model.get("name", path.stem)), columns)


def diff_schemas(old: SchemaInfo, new: SchemaInfo) -> list[SchemaChange]:
    """Classify column-level differences between two parsed schemas."""
    changes: list[SchemaChange] = []
    dropped = {n: c for n, c in old.columns.items() if n not in new.columns}
    added = {n: c for n, c in new.columns.items() if n not in old.columns}

    # Rename detection: dropped+added sharing a normalized type.
    paired: set[str] = set()
    for dname, dcol in list(dropped.items()):
        for aname, acol in list(added.items()):
            if acol.type == dcol.type and aname not in paired:
                changes.append(
                    SchemaChange(
                        "rename?",
                        f"{dname} -> {aname}",
                        f"dropped `{dname}` and added `{aname}` (same type "
                        f"{dcol.type}) - possible rename",
                        _BREAKING,
                    )
                )
                paired.add(aname)
                dropped.pop(dname)
                added.pop(aname)
                break

    for name in sorted(added):
        col = added[name]
        changes.append(
            SchemaChange(
                "added",
                name,
                f"new {col.type} column" + (" (nullable)" if col.nullable else " (required)"),
                _COMPATIBLE if col.nullable else _MAYBE,
            )
        )
    for name in sorted(dropped):
        changes.append(
            SchemaChange("dropped", name, f"removed {dropped[name].type} column", _BREAKING)
        )
    for name in sorted(old.columns.keys() & new.columns.keys()):
        o, n = old.columns[name], new.columns[name]
        if o.type != n.type:
            classification = _COMPATIBLE if (o.type, n.type) in WIDENING else _BREAKING
            changes.append(
                SchemaChange(
                    "type",
                    name,
                    f"type changed {o.type} -> {n.type}",
                    classification,
                )
            )
        if o.nullable and not n.nullable:
            changes.append(
                SchemaChange(
                    "nullability",
                    name,
                    "nullable -> required",
                    _BREAKING,
                )
            )
    return changes
