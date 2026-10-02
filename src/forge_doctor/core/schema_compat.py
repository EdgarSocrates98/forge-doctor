"""Schema type compatibility across dialects (spec 234).

``SchemaCompatibility`` maps one source type to a target dialect type
with nullability/precision/nested-shape notes and an honest
compatibility grade. Coverage targets the hard cases first: BigQuery
STRUCT / Snowflake VARIANT / Trino ROW / ClickHouse Tuple / OpenSearch
object — plus scalar families via normalization. Unknown types report
UNKNOWN compatibility, never a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Compatibility(Enum):
    DIRECT = "direct"  # same family, same-ish semantics
    COERCED = "coerced"  # automatic widening/representation change
    LOSSY = "lossy"  # information may be lost (precision/schema)
    INCOMPATIBLE = "incompatible"  # no faithful target type
    UNKNOWN = "unknown"  # not in the mapping evidence


@dataclass(frozen=True)
class SchemaCompatibility:
    source_type: str
    target_type: str  # "" when incompatible/unknown
    nullability: str = ""  # note on nullability carry-over
    precision: str = ""  # note on scale/precision carry-over
    nested_shape: str = ""  # how nested structure survives
    coercion: str = ""  # required cast/conversion note
    compatibility: Compatibility = Compatibility.UNKNOWN


# type-spelling -> normalized family per dialect. Families keep the
# mapping tractable: only family crossings carry compatibility risk.
_FAMILIES: dict[str, dict[str, str]] = {
    # shared spellings first, dialect overrides after
    "snowflake": {
        "NUMBER": "numeric",
        "DECIMAL": "numeric",
        "NUMERIC": "numeric",
        "INT": "int",
        "INTEGER": "int",
        "BIGINT": "int",
        "SMALLINT": "int",
        "FLOAT": "float",
        "DOUBLE": "float",
        "REAL": "float",
        "VARCHAR": "string",
        "STRING": "string",
        "TEXT": "string",
        "CHAR": "string",
        "BOOLEAN": "bool",
        "DATE": "date",
        "DATETIME": "timestamp_ntz",
        "TIMESTAMP_NTZ": "timestamp_ntz",
        "TIMESTAMP_LTZ": "timestamp_ltz",
        "TIMESTAMP_TZ": "timestamp_tz",
        "VARIANT": "variant",
        "OBJECT": "object",
        "ARRAY": "array",
        "GEOGRAPHY": "geo",
        "BINARY": "binary",
    },
    "bigquery": {
        "INT64": "int",
        "INTEGER": "int",
        "SMALLINT": "int",
        "BIGINT": "int",
        "NUMERIC": "numeric",
        "BIGNUMERIC": "bignumeric",
        "DECIMAL": "numeric",
        "FLOAT64": "float",
        "STRING": "string",
        "BYTES": "binary",
        "BOOL": "bool",
        "BOOLEAN": "bool",
        "DATE": "date",
        "DATETIME": "timestamp_ntz",
        "TIMESTAMP": "timestamp_tz",
        "STRUCT": "struct",
        "ARRAY": "array",
        "JSON": "json",
        "GEOGRAPHY": "geo",
        "INTERVAL": "interval",
    },
    "redshift": {
        "INTEGER": "int",
        "INT": "int",
        "BIGINT": "int",
        "SMALLINT": "int",
        "DECIMAL": "numeric",
        "NUMERIC": "numeric",
        "REAL": "float",
        "DOUBLE PRECISION": "float",
        "FLOAT": "float",
        "VARCHAR": "string",
        "CHAR": "string",
        "TEXT": "string",
        "BOOLEAN": "bool",
        "DATE": "date",
        "TIMESTAMP": "timestamp_ntz",
        "TIMESTAMPTZ": "timestamp_tz",
        "SUPER": "variant",
        "VARBYTE": "binary",
        "GEOMETRY": "geo",
    },
    "trino": {
        "INTEGER": "int",
        "INT": "int",
        "BIGINT": "int",
        "SMALLINT": "int",
        "TINYINT": "int",
        "DECIMAL": "numeric",
        "REAL": "float",
        "DOUBLE": "float",
        "VARCHAR": "string",
        "CHAR": "string",
        "VARBINARY": "binary",
        "BOOLEAN": "bool",
        "DATE": "date",
        "TIMESTAMP": "timestamp_ntz",
        "TIMESTAMP WITH TIME ZONE": "timestamp_tz",
        "ROW": "row",
        "ARRAY": "array",
        "MAP": "map",
        "JSON": "json",
        "INTERVAL YEAR TO MONTH": "interval",
        "INTERVAL DAY TO SECOND": "interval",
    },
    "clickhouse": {
        "INT32": "int",
        "INT64": "int",
        "UINT64": "int",
        "UINT32": "int",
        "DECIMAL": "numeric",
        "DECIMAL64": "numeric",
        "FLOAT32": "float",
        "FLOAT64": "float",
        "STRING": "string",
        "FIXEDSTRING": "string",
        "BOOL": "bool",
        "DATE": "date",
        "DATETIME": "timestamp_ntz",
        "DATETIME64": "timestamp_ntz",
        "TUPLE": "tuple",
        "ARRAY": "array",
        "MAP": "map",
        "NESTED": "tuple",
        "JSON": "json",
        "UUID": "string",
    },
    "opensearch": {
        "TEXT": "string",
        "KEYWORD": "string",
        "LONG": "int",
        "INTEGER": "int",
        "DOUBLE": "float",
        "FLOAT": "float",
        "BOOLEAN": "bool",
        "DATE": "date",
        "OBJECT": "object",
        "NESTED": "object",
        "BINARY": "binary",
        "IP": "string",
        "GEO_POINT": "geo",
    },
    "spark": {
        "INT": "int",
        "INTEGER": "int",
        "BIGINT": "int",
        "SMALLINT": "int",
        "TINYINT": "int",
        "DECIMAL": "numeric",
        "DOUBLE": "float",
        "FLOAT": "float",
        "STRING": "string",
        "VARCHAR": "string",
        "BOOLEAN": "bool",
        "DATE": "date",
        "TIMESTAMP": "timestamp_ntz",
        "TIMESTAMP_NTZ": "timestamp_ntz",
        "STRUCT": "struct",
        "ARRAY": "array",
        "MAP": "map",
        "BINARY": "binary",
    },
    "databricks": {
        "INT": "int",
        "BIGINT": "int",
        "DECIMAL": "numeric",
        "DOUBLE": "float",
        "STRING": "string",
        "BOOLEAN": "bool",
        "DATE": "date",
        "TIMESTAMP": "timestamp_ntz",
        "STRUCT": "struct",
        "ARRAY": "array",
        "MAP": "map",
        "BINARY": "binary",
        "VARIANT": "variant",
    },
}

# family -> target spelling per dialect ("" = no native type)
_FAMILY_TARGET: dict[str, dict[str, str]] = {
    "snowflake": {
        "int": "NUMBER(38,0)",
        "numeric": "NUMBER",
        "float": "FLOAT",
        "string": "VARCHAR",
        "bool": "BOOLEAN",
        "date": "DATE",
        "timestamp_ntz": "TIMESTAMP_NTZ",
        "timestamp_ltz": "TIMESTAMP_LTZ",
        "timestamp_tz": "TIMESTAMP_TZ",
        "variant": "VARIANT",
        "object": "OBJECT",
        "array": "ARRAY",
        "map": "VARIANT",
        "struct": "OBJECT",
        "tuple": "VARIANT",
        "row": "OBJECT",
        "json": "VARIANT",
        "geo": "GEOGRAPHY",
        "binary": "BINARY",
        "interval": "",
        "bignumeric": "NUMBER",
    },
    "bigquery": {
        "int": "INT64",
        "numeric": "NUMERIC",
        "bignumeric": "BIGNUMERIC",
        "float": "FLOAT64",
        "string": "STRING",
        "bool": "BOOL",
        "date": "DATE",
        "timestamp_ntz": "DATETIME",
        "timestamp_ltz": "TIMESTAMP",
        "timestamp_tz": "TIMESTAMP",
        "variant": "JSON",
        "object": "JSON",
        "array": "ARRAY",
        "map": "JSON",
        "struct": "STRUCT",
        "tuple": "STRUCT",
        "row": "STRUCT",
        "json": "JSON",
        "geo": "GEOGRAPHY",
        "binary": "BYTES",
        "interval": "INTERVAL",
    },
    "redshift": {
        "int": "BIGINT",
        "numeric": "DECIMAL",
        "float": "DOUBLE PRECISION",
        "string": "VARCHAR",
        "bool": "BOOLEAN",
        "date": "DATE",
        "timestamp_ntz": "TIMESTAMP",
        "timestamp_ltz": "TIMESTAMPTZ",
        "timestamp_tz": "TIMESTAMPTZ",
        "variant": "SUPER",
        "object": "SUPER",
        "array": "SUPER",
        "map": "SUPER",
        "struct": "SUPER",
        "tuple": "SUPER",
        "row": "SUPER",
        "json": "SUPER",
        "geo": "GEOMETRY",
        "binary": "VARBYTE",
        "interval": "",
        "bignumeric": "DECIMAL",
    },
    "trino": {
        "int": "BIGINT",
        "numeric": "DECIMAL",
        "float": "DOUBLE",
        "string": "VARCHAR",
        "bool": "BOOLEAN",
        "date": "DATE",
        "timestamp_ntz": "TIMESTAMP",
        "timestamp_ltz": "TIMESTAMP WITH TIME ZONE",
        "timestamp_tz": "TIMESTAMP WITH TIME ZONE",
        "variant": "JSON",
        "object": "ROW",
        "array": "ARRAY",
        "map": "MAP",
        "struct": "ROW",
        "tuple": "ROW",
        "row": "ROW",
        "json": "JSON",
        "geo": "",
        "binary": "VARBINARY",
        "interval": "INTERVAL DAY TO SECOND",
        "bignumeric": "DECIMAL",
    },
    "clickhouse": {
        "int": "Int64",
        "numeric": "Decimal",
        "float": "Float64",
        "string": "String",
        "bool": "Bool",
        "date": "Date",
        "timestamp_ntz": "DateTime64",
        "timestamp_ltz": "DateTime64",
        "timestamp_tz": "DateTime64",
        "variant": "JSON",
        "object": "Tuple",
        "array": "Array",
        "map": "Map",
        "struct": "Tuple",
        "tuple": "Tuple",
        "row": "Tuple",
        "json": "JSON",
        "geo": "",
        "binary": "String",
        "interval": "",
        "bignumeric": "Decimal256",
    },
    "opensearch": {
        "int": "long",
        "numeric": "double",
        "float": "double",
        "string": "keyword",
        "bool": "boolean",
        "date": "date",
        "timestamp_ntz": "date",
        "timestamp_ltz": "date",
        "timestamp_tz": "date",
        "variant": "object",
        "object": "object",
        "array": "object",
        "map": "object",
        "struct": "object",
        "tuple": "object",
        "row": "object",
        "json": "object",
        "geo": "geo_point",
        "binary": "binary",
        "interval": "",
        "bignumeric": "double",
    },
}
# dialects sharing a spellbook
_FAMILY_TARGET["spark"] = _FAMILY_TARGET["bigquery"] | {
    "int": "BIGINT",
    "timestamp_tz": "TIMESTAMP",
    "variant": "VARIANT",
    "object": "STRUCT",
    "tuple": "STRUCT",
    "row": "STRUCT",
    "json": "STRING",
    "geo": "",
    "binary": "BINARY",
    "interval": "INTERVAL",
}
_FAMILY_TARGET["databricks"] = _FAMILY_TARGET["spark"]

# family pairs that stay faithful vs. lose something — honest notes,
# keyed (src_family, dst_family). Default same-family => DIRECT.
_CROSS_FAMILY: dict[tuple[str, str], tuple[Compatibility, str]] = {
    ("struct", "object"): (
        Compatibility.LOSSY,
        "schema-enforced STRUCT becomes schema-free VARIANT/OBJECT",
    ),
    ("struct", "variant"): (
        Compatibility.LOSSY,
        "typed STRUCT fields collapse into VARIANT (no schema)",
    ),
    ("struct", "tuple"): (
        Compatibility.LOSSY,
        "named STRUCT fields map to positional Tuple",
    ),
    ("row", "object"): (
        Compatibility.COERCED,
        "ROW becomes OBJECT — field names preserved, types not enforced",
    ),
    ("variant", "struct"): (
        Compatibility.LOSSY,
        "schemaless VARIANT needs an explicit target schema",
    ),
    ("variant", "json"): (Compatibility.COERCED, "VARIANT serializes into JSON"),
    ("object", "struct"): (
        Compatibility.LOSSY,
        "schema-free OBJECT needs an explicit STRUCT schema",
    ),
    ("object", "tuple"): (
        Compatibility.LOSSY,
        "OBJECT keys become positional Tuple elements",
    ),
    ("object", "object"): (Compatibility.DIRECT, ""),
    ("object", "json"): (
        Compatibility.COERCED,
        "typed OBJECT fields serialize into schema-free JSON",
    ),
    ("struct", "json"): (
        Compatibility.LOSSY,
        "schema-enforced STRUCT serializes into schema-free JSON",
    ),
    ("tuple", "struct"): (
        Compatibility.COERCED,
        "positional Tuple gains named STRUCT fields",
    ),
    ("tuple", "object"): (
        Compatibility.LOSSY,
        "positional Tuple elements have no names to carry into OBJECT keys",
    ),
    ("tuple", "json"): (
        Compatibility.LOSSY,
        "positional Tuple serializes to a JSON array — element names lost",
    ),
    ("json", "variant"): (Compatibility.DIRECT, ""),
    ("json", "struct"): (
        Compatibility.LOSSY,
        "schema-free JSON needs an explicit STRUCT schema",
    ),
    ("map", "struct"): (Compatibility.LOSSY, "MAP keys are dynamic; STRUCT is fixed"),
    ("array", "array"): (Compatibility.DIRECT, ""),
    ("timestamp_tz", "timestamp_ntz"): (
        Compatibility.LOSSY,
        "timezone offset dropped — convert explicitly before migrate",
    ),
    ("timestamp_ltz", "timestamp_ntz"): (
        Compatibility.LOSSY,
        "local-timestamp semantics flatten to naive",
    ),
    ("timestamp_ltz", "timestamp_tz"): (
        Compatibility.COERCED,
        "session-zone LTZ becomes explicit TZ",
    ),
    ("bignumeric", "numeric"): (
        Compatibility.LOSSY,
        "BIGNUMERIC(76,38) exceeds NUMBER(38) scale — check ranges",
    ),
    ("interval", "interval"): (Compatibility.DIRECT, ""),
    ("geo", "geo"): (Compatibility.DIRECT, ""),
}

_TYPE_RE = re.compile(r"^\s*([A-Za-z]+(?:\s+[A-Za-z]+)?)\s*(\(.*\))?\s*$")


def _family(dialect: str, type_name: str) -> str:
    spell = _TYPE_RE.match(type_name.upper())
    raw = spell.group(1).strip() if spell else type_name.upper().strip()
    fams = _FAMILIES.get(dialect.lower(), {})
    if raw in fams:
        return fams[raw]
    # longest-prefix for "TIMESTAMP WITH TIME ZONE" style spellings
    for key in sorted(fams, key=len, reverse=True):
        if raw.startswith(key):
            return fams[key]
    return ""


def map_type(source_dialect: str, source_type: str, target_dialect: str) -> SchemaCompatibility:
    """One source type spelling -> target dialect type + compatibility."""
    src_fam = _family(source_dialect, source_type)
    tgt = _FAMILY_TARGET.get(target_dialect.lower())
    if not src_fam or tgt is None:
        return SchemaCompatibility(
            source_type=source_type,
            target_type="",
            compatibility=Compatibility.UNKNOWN,
            coercion="no mapping evidence for this type/dialect pair",
        )
    target_type = tgt.get(src_fam, "")
    if target_type == "":
        return SchemaCompatibility(
            source_type=source_type,
            target_type="",
            compatibility=Compatibility.INCOMPATIBLE,
            nested_shape=f"{target_dialect} has no native {src_fam} type",
            coercion="redesign required",
        )
    # same-family mappings default to DIRECT; cross-family lookups are for
    # when a source spelling normalizes differently on the target.
    pair = _CROSS_FAMILY.get((src_fam, _family(target_dialect, target_type)))
    compat, note = pair if pair is not None else (Compatibility.DIRECT, "")
    precision = ""
    if "(" in source_type:
        precision = "precision/scale args carry over subject to target bounds"
    nested = ""
    if src_fam in ("struct", "row", "tuple", "object", "variant", "map", "array"):
        nested = note or f"nested family {src_fam} -> {target_type}"
    return SchemaCompatibility(
        source_type=source_type.strip(),
        target_type=target_type,
        nullability="nullability is column-level metadata; unchanged"
        if compat in (Compatibility.DIRECT, Compatibility.COERCED)
        else "",
        precision=precision,
        nested_shape=nested,
        coercion=note,
        compatibility=compat,
    )


def dialects() -> tuple[str, ...]:
    return tuple(sorted(_FAMILIES))
