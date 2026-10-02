"""SQL portability analysis (spec 234).

Static, dialect-aware portability findings over committed SQL text —
never a transpiler. ``SqlDialectCapabilities`` records what each dialect
natively supports; ``analyze_sql_portability`` emits SQLPORT001-007
findings with file/line evidence. Absent evidence (no SQL, unknown
dialect) produces no findings — never guesses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class PortabilityKind(Enum):
    DIALECT_FUNCTION = "SQLPORT001"
    MERGE_SEMANTICS = "SQLPORT002"
    QUALIFY = "SQLPORT003"
    TIMESTAMP_TZ = "SQLPORT004"
    IDENTIFIER_QUOTING = "SQLPORT005"
    NESTED_TYPES = "SQLPORT006"
    NULL_ORDERING = "SQLPORT007"


@dataclass(frozen=True)
class SqlDialectCapabilities:
    """Dialect facts used by portability checks — all sourced defaults."""

    dialect: str
    supports_qualify: bool
    supports_merge: bool
    merge_note: str = ""
    identifier_quote: str = '"'  # " | ` | []
    nulls_default: str = "last"  # ORDER BY ... ASC default
    timestamp_tz: str = "aware"  # aware | offset_only | naive
    nested_model: str = ""  # struct|variant|row|tuple|object|none
    exclusive_functions: tuple[str, ...] = ()


_DIALECTS: dict[str, SqlDialectCapabilities] = {
    "snowflake": SqlDialectCapabilities(
        "snowflake",
        supports_qualify=True,
        supports_merge=True,
        merge_note="matched/not-matched-by-source clauses",
        identifier_quote='"',
        nulls_default="last",
        timestamp_tz="aware",
        nested_model="variant",
        exclusive_functions=("IFF", "DATEADD", "DATEDIFF", "LATERAL FLATTEN", "TRY_CAST"),
    ),
    "bigquery": SqlDialectCapabilities(
        "bigquery",
        supports_qualify=True,
        supports_merge=True,
        identifier_quote="`",
        nulls_default="first",
        timestamp_tz="offset_only",
        nested_model="struct",
        exclusive_functions=(
            "SAFE_CAST",
            "FORMAT_DATE",
            "TIMESTAMP_DIFF",
            "UNNEST",
            "DATETIME_TRUNC",
            "ARRAY_AGG",
        ),
    ),
    "redshift": SqlDialectCapabilities(
        "redshift",
        supports_qualify=False,
        supports_merge=True,
        merge_note="simplified MERGE — no NOT MATCHED BY SOURCE",
        identifier_quote='"',
        nulls_default="last",
        timestamp_tz="aware",
        nested_model="none",
        exclusive_functions=("GETDATE", "SYSDATE", "DATEADD", "LISTAGG", "DECODE"),
    ),
    "trino": SqlDialectCapabilities(
        "trino",
        supports_qualify=False,
        supports_merge=True,
        merge_note="MERGE on supported connectors only (iceberg/delta)",
        identifier_quote='"',
        nulls_default="last",
        timestamp_tz="aware",
        nested_model="row",
        exclusive_functions=(
            "APPROX_DISTINCT",
            "DATE_ADD",
            "DATE_DIFF",
            "TRY_CAST",
            "ARBITRARY",
        ),
    ),
    "spark": SqlDialectCapabilities(
        "spark",
        supports_qualify=False,
        supports_merge=True,
        merge_note="MERGE only on Delta/Iceberg tables, not plain files",
        identifier_quote="`",
        nulls_default="first",
        timestamp_tz="naive",
        nested_model="struct",
        exclusive_functions=(
            "EXPLODE",
            "COLLECT_LIST",
            "LATERAL VIEW",
            "DATE_FORMAT",
            "FROM_UNIXTIME",
        ),
    ),
    "databricks": SqlDialectCapabilities(
        "databricks",
        supports_qualify=False,
        supports_merge=True,
        merge_note="MERGE on Delta tables; WHEN NOT MATCHED BY SOURCE supported",
        identifier_quote="`",
        nulls_default="first",
        timestamp_tz="naive",
        nested_model="struct",
        exclusive_functions=(
            "EXPLODE",
            "COLLECT_LIST",
            "LATERAL VIEW",
            "DATE_FORMAT",
            "FROM_UNIXTIME",
        ),
    ),
    "clickhouse": SqlDialectCapabilities(
        "clickhouse",
        supports_qualify=False,
        supports_merge=False,
        merge_note="no MERGE — use ReplacingMergeTree/CollapsingMergeTree engines",
        identifier_quote="`",
        nulls_default="last",
        timestamp_tz="aware",
        nested_model="tuple",
        exclusive_functions=(
            "TODATE",
            "ARRAYJOIN",
            "TOYYYYMM",
            "UNIQEXACT",
            "GROUPBY ARRAY",
        ),
    ),
}


def dialect_capabilities(dialect: str) -> SqlDialectCapabilities | None:
    return _DIALECTS.get(dialect.lower())


def known_dialects() -> tuple[str, ...]:
    return tuple(sorted(_DIALECTS))


@dataclass(frozen=True)
class SqlPortabilityFinding:
    check_id: str  # SQLPORT00N
    title: str
    severity: str  # warning | info
    message: str
    file: Path | None
    line: int | None
    evidence: str


_FN_RE = {
    name: re.compile(rf"\b{name}\s*\(", re.IGNORECASE)
    for dialect in _DIALECTS.values()
    for name in dialect.exclusive_functions
    if " " not in name
}
_QUALIFY_RE = re.compile(r"\bQUALIFY\b", re.IGNORECASE)
_MERGE_RE = re.compile(r"\bMERGE\s+INTO\b", re.IGNORECASE)
_TZ_RE = re.compile(
    r"\bTIMESTAMP_(LTZ|NTZ|TZ)\b|\bCONVERT_TIMEZONE\b|\bAT\s+TIME\s+ZONE\b"
    r"|\bWITH\s+TIME\s+ZONE\b",
    re.IGNORECASE,
)
_DQ_IDENT_RE = re.compile(r'"[A-Za-z_][A-Za-z0-9_]*"')
_BT_IDENT_RE = re.compile(r"`[A-Za-z_][A-Za-z0-9_]*`")
_ORDER_BY_RE = re.compile(r"\bORDER\s+BY\b", re.IGNORECASE)
_NULLS_RE = re.compile(r"\bNULLS\s+(FIRST|LAST)\b", re.IGNORECASE)
_NESTED_RE = re.compile(
    r"\bSTRUCT\b|\bVARIANT\b|\bOBJECT\b|\bROW\s*\(|\bNESTED\b|\bUNNEST\b"
    r"|\bSUPER\b|\bTUPLE\b",
    re.IGNORECASE,
)
_TARGET_FN_INDEX: dict[str, frozenset[str]] = {
    d.dialect: frozenset(f.upper() for f in d.exclusive_functions) for d in _DIALECTS.values()
}


def analyze_sql_portability(
    sql: str, source: str, target: str, *, file: Path | None = None
) -> list[SqlPortabilityFinding]:
    """Compare dialect features found in ``sql`` against the target."""
    src = _DIALECTS.get(source.lower())
    tgt = _DIALECTS.get(target.lower())
    if not sql.strip() or src is None or tgt is None or source == target:
        return []
    out: list[SqlPortabilityFinding] = []

    def _line_of(m: re.Match[str] | None) -> int | None:
        return sql.count("\n", 0, m.start()) + 1 if m else None

    # SQLPORT001 — source-exclusive function with no target support claim.
    for fn in src.exclusive_functions:
        pat = re.compile(rf"\b{re.escape(fn)}\b", re.IGNORECASE) if " " in fn else _FN_RE[fn]
        m = pat.search(sql)
        if m and fn.upper() not in _TARGET_FN_INDEX[tgt.dialect]:
            out.append(
                SqlPortabilityFinding(
                    PortabilityKind.DIALECT_FUNCTION.value,
                    "Dialect function without target equivalent",
                    "warning",
                    f"{fn}() is {src.dialect} dialect; no documented {tgt.dialect} "
                    "equivalent in the portability map — verify manually",
                    file,
                    _line_of(m),
                    m.group(0).rstrip("("),
                )
            )
    # SQLPORT002 — MERGE semantics differ.
    m = _MERGE_RE.search(sql)
    if m and (not tgt.supports_merge or tgt.merge_note != src.merge_note):
        detail = (
            f"{tgt.dialect} has no MERGE — {tgt.merge_note}"
            if not tgt.supports_merge
            else f"{tgt.dialect} MERGE differs: {tgt.merge_note or 'semantics review needed'}"
        )
        out.append(
            SqlPortabilityFinding(
                PortabilityKind.MERGE_SEMANTICS.value,
                "MERGE semantics differ on target",
                "warning",
                f"MERGE present; {detail}",
                file,
                _line_of(m),
                "MERGE INTO",
            )
        )
    # SQLPORT003 — QUALIFY unavailable on target.
    m = _QUALIFY_RE.search(sql)
    if m and not tgt.supports_qualify:
        out.append(
            SqlPortabilityFinding(
                PortabilityKind.QUALIFY.value,
                "QUALIFY unsupported on target",
                "warning",
                f"QUALIFY used but {tgt.dialect} has no QUALIFY — rewrite as a "
                "windowed subquery filter",
                file,
                _line_of(m),
                "QUALIFY",
            )
        )
    # SQLPORT004 — timestamp/timezone model differs.
    m = _TZ_RE.search(sql)
    if m and tgt.timestamp_tz != src.timestamp_tz:
        out.append(
            SqlPortabilityFinding(
                PortabilityKind.TIMESTAMP_TZ.value,
                "Timestamp/timezone model differs",
                "warning",
                f"explicit timezone semantics used; {src.dialect} is "
                f"{src.timestamp_tz}, {tgt.dialect} is {tgt.timestamp_tz}",
                file,
                _line_of(m),
                m.group(0),
            )
        )
    # SQLPORT005 — identifier quoting differs.
    src_quote = src.identifier_quote
    tgt_quote = tgt.identifier_quote
    if src_quote != tgt_quote:
        pat = _DQ_IDENT_RE if src_quote == '"' else _BT_IDENT_RE
        m = pat.search(sql)
        if m:
            out.append(
                SqlPortabilityFinding(
                    PortabilityKind.IDENTIFIER_QUOTING.value,
                    "Identifier quoting differs",
                    "info",
                    f"{src.dialect} quotes identifiers with {src_quote}; "
                    f"{tgt.dialect} expects {tgt_quote}",
                    file,
                    _line_of(m),
                    m.group(0),
                )
            )
    # SQLPORT006 — nested/semi-structured types remap non-trivially.
    m = _NESTED_RE.search(sql)
    if m and src.nested_model not in ("", tgt.nested_model):
        out.append(
            SqlPortabilityFinding(
                PortabilityKind.NESTED_TYPES.value,
                "Nested/semi-structured model differs",
                "warning",
                f"nested type syntax used; {src.dialect} models nested data as "
                f"{src.nested_model}, {tgt.dialect} as {tgt.nested_model or 'none'}",
                file,
                _line_of(m),
                m.group(0),
            )
        )
    # SQLPORT007 — NULL ordering defaults differ, ORDER BY without NULLS.
    if src.nulls_default != tgt.nulls_default:
        m = _ORDER_BY_RE.search(sql)
        if m and not _NULLS_RE.search(sql):
            out.append(
                SqlPortabilityFinding(
                    PortabilityKind.NULL_ORDERING.value,
                    "NULL ordering defaults differ",
                    "warning",
                    f"ORDER BY without explicit NULLS FIRST/LAST; {src.dialect} "
                    f"defaults nulls {src.nulls_default}, {tgt.dialect} defaults "
                    f"{tgt.nulls_default}",
                    file,
                    _line_of(m),
                    "ORDER BY",
                )
            )
    return out


def scan_project_sql(
    files: dict[Path, str], source: str, target: str
) -> list[SqlPortabilityFinding]:
    """Run portability analysis over .sql files (deterministic order)."""
    out: list[SqlPortabilityFinding] = []
    for path in sorted(files):
        if path.suffix.lower() != ".sql":
            continue
        out.extend(analyze_sql_portability(files[path], source, target, file=path))
    return out
