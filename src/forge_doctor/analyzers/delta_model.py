"""Delta Lake deep model: protocol features, DML ops, maintenance ops,
CDF, schema evolution, liquid clustering, streaming delta endpoints.

Sources: Spark call-sites (``.format("delta")``, ``delta.tables`` API,
``spark.sql`` strings), ``.sql`` files (MERGE/OPTIMIZE/VACUUM/CLUSTER BY),
writer options and ``delta.*`` table properties. Offline only.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forge_doctor.analyzers.index import project_index
from forge_doctor.core.context import ProjectContext

# feature name -> triggering tokens/properties (lowercased match)
_FEATURES = {
    "deletion_vectors": ("deletionvector", "delta.deletionvectors"),
    "column_mapping": ("columnmapping", "delta.columnmapping"),
    "cdf": (
        "enablechangedatafeed",
        "enablechangefeed",
        "readchangefeed",
        "table_changes",
        "deltachangefeed",
    ),
    "liquid_clustering": ("clusterby", "liquid.clustering", "delta.liquidclustering"),
    "schema_evolution": ("mergeschema", "automerge", "overwriteschema", "allowsourceevolution"),
    "identity_columns": ("generated always as identity", "delta.identitycolumns"),
}

_DELTA_METHODS = {
    "merge": "merge",
    "update": "update",
    "delete": "delete",
    "vacuum": "vacuum",
    "optimize": "optimize",
    "restore": "restore",
    "history": "history",
    "generatesymlinkformatmanifest": "manifest",
    "converttodelta": "convert",
    "clone": "clone",
}

_SQL_OPS = {
    "merge": re.compile(r"\bMERGE\s+INTO\b", re.IGNORECASE),
    "optimize": re.compile(r"\bOPTIMIZE\b", re.IGNORECASE),
    "vacuum": re.compile(r"\bVACUUM\b", re.IGNORECASE),
    "restore": re.compile(r"\bRESTORE\s+(?:TABLE|VERSION\b|TIMESTAMP\b)", re.IGNORECASE),
    "create_using_delta": re.compile(r"USING\s+DELTA\b", re.IGNORECASE),
    "cluster_by": re.compile(r"\bCLUSTER\s+BY\b", re.IGNORECASE),
    "cdf_read": re.compile(r"\btable_changes\s*\(", re.IGNORECASE),
    "tblproperties": re.compile(r"\bTBLPROPERTIES\b", re.IGNORECASE),
    "update": re.compile(r"\bUPDATE\s+\w+", re.IGNORECASE),
    "delete": re.compile(r"\bDELETE\s+FROM\b", re.IGNORECASE),
}

# op -> regex capturing the table the statement acts on
_SQL_TARGET = {
    "merge": re.compile(r"\bMERGE\s+INTO\s+([\w.`]+)", re.IGNORECASE),
    "update": re.compile(r"\bUPDATE\s+([\w.`]+)", re.IGNORECASE),
    "delete": re.compile(r"\bDELETE\s+FROM\s+([\w.`]+)", re.IGNORECASE),
    "optimize": re.compile(r"\bOPTIMIZE\s+([\w.`]+)?", re.IGNORECASE),
    "vacuum": re.compile(r"\bVACUUM\s+([\w.`]+)?", re.IGNORECASE),
    "restore": re.compile(r"\bRESTORE\s+(?:TABLE\s+)?([\w.`]+)", re.IGNORECASE),
    "create_using_delta": re.compile(
        r"CREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+([\w.`]+)[^;]*?USING\s+DELTA",
        re.IGNORECASE | re.DOTALL,
    ),
}

_DELTA_TABLE_RE = re.compile(r"DeltaTable\.(forName|forPath)\s*\(\s*[\"']([^\"']+)")

# Definitive delta evidence. Generic SQL DML (MERGE/UPDATE/DELETE/OPTIMIZE/
# VACUUM) is only attributed to Delta when one of these tokens appears
# anywhere in the project - otherwise the statement may target Iceberg or a
# plain catalog table and would be a false positive.
_DELTA_SIGNAL_RE = re.compile(
    r"using\s+delta|deltatable|format\(\s*[\"']delta|spark\.databricks\.delta"
    r"|io\.delta|delta\.tables|table_changes\s*\(",
    re.IGNORECASE,
)

# SQL ops that are self-evidencing (the syntax only exists for Delta).
_SELF_EVIDENT_OPS = frozenset({"create_using_delta", "cdf_read"})


@dataclass(frozen=True)
class DeltaOp:
    """One Delta operation evidence (merge/update/optimize/vacuum/...)."""

    op: str
    file: Path
    line: int
    target: str = ""
    source: str = "code"  # code|sql|option


@dataclass
class DeltaProjectModel:
    tables: set[str] = field(default_factory=set)
    ops: list[DeltaOp] = field(default_factory=list)
    features: set[str] = field(default_factory=set)
    properties: set[str] = field(default_factory=set)  # raw delta.* property keys
    protocol_reader: int = 0
    protocol_writer: int = 0
    delta_reads: int = 0
    delta_writes: int = 0
    cdf_sources: int = 0
    streaming_delta: int = 0
    has_delta: bool = False

    def op_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for op in self.ops:
            out[op.op] = out.get(op.op, 0) + 1
        return out


def _feature_scan(text: str) -> set[str]:
    low = text.lower()
    return {f for f, tokens in _FEATURES.items() if any(t in low for t in tokens)}


def _delta_props(text: str) -> set[str]:
    return set(re.findall(r"delta\.[a-zA-Z_]+", text))


def _protocol(text: str) -> tuple[int, int]:
    reader = writer = 0
    m = re.search(r"minreaderversion\"?['\"]?\s*[:=,]\s*['\"]?(\d+)", text, re.IGNORECASE)
    if m:
        reader = int(m.group(1))
    m = re.search(r"minwriterversion\"?['\"]?\s*[:=,]\s*['\"]?(\d+)", text, re.IGNORECASE)
    if m:
        writer = int(m.group(1))
    return reader, writer


def delta_model(ctx: ProjectContext) -> DeltaProjectModel:
    """Fuse Delta evidence (code/sql/options) into one model."""
    model = DeltaProjectModel()
    index = project_index(ctx)

    # Project-level delta signal: without it, generic SQL DML is unattributed
    # (Iceberg/plain tables support MERGE/DELETE too) — gates false positives.
    delta_signal = any(
        _DELTA_SIGNAL_RE.search(ctx.read_text(relative) or "")
        for relative in sorted(ctx.files)
        if relative.suffix.lower() in (".py", ".sql", ".scala", ".ipynb")
    )

    # ---- .sql files: DML + maintenance ops, DDL, TBLPROPERTIES ----
    for relative in sorted(ctx.files):
        if relative.suffix.lower() != ".sql":
            continue
        text = ctx.read_text(relative) or ""
        for op_name, rex in _SQL_OPS.items():
            if op_name not in _SELF_EVIDENT_OPS and not delta_signal:
                continue
            for m in rex.finditer(text):
                tgt_rex = _SQL_TARGET.get(op_name)
                tgt = ""
                if tgt_rex is not None:
                    if op_name == "create_using_delta":
                        # the table name precedes the USING DELTA match
                        tm = next(
                            (
                                c
                                for c in tgt_rex.finditer(text)
                                if c.start() <= m.start() <= c.end()
                            ),
                            None,
                        )
                    else:
                        tm = tgt_rex.search(text, m.start())
                    if tm and tm.group(1):
                        tgt = tm.group(1).strip("`")
                model.ops.append(
                    DeltaOp(
                        op=op_name,
                        file=relative,
                        line=text.count("\n", 0, m.start()) + 1,
                        target=tgt,
                        source="sql",
                    )
                )
                if tgt:
                    model.tables.add(tgt)
        model.features |= _feature_scan(text)
        model.properties |= _delta_props(text)
        r, w = _protocol(text)
        model.protocol_reader = max(model.protocol_reader, r)
        model.protocol_writer = max(model.protocol_writer, w)
        # USING DELTA -> a table definition exists
        for m in re.finditer(
            r"CREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+([\w.`]+)", text, re.IGNORECASE
        ):
            model.tables.add(m.group(1).strip("`"))

    # ---- Python: delta reads/writes, DeltaTable APIs, spark.sql strings ----
    for module in index.modules.values():
        delta_bound: set[str] = set()
        for assign in module.assigns:
            if assign.value_call and "deltatable" in assign.value_call.lower():
                delta_bound.add(assign.target.split(".")[0])
        if module.tree is not None:
            for node in ast.walk(module.tree):
                # DeltaTable.forName/forPath targets + method ops
                if isinstance(node, ast.Call):
                    dotted = _attr_dotted(node.func)
                    lit_args = [
                        a.value
                        for a in node.args
                        if isinstance(a, ast.Constant) and isinstance(a.value, str)
                    ]
                    receiver = dotted.split(".")[0]
                    method_last = dotted.rsplit(".", 1)[-1].lower()
                    if "deltatable" in dotted.lower() or (
                        receiver in delta_bound and method_last in _DELTA_METHODS
                    ):
                        method = method_last
                        if method in ("forname", "forpath") and lit_args:
                            model.tables.add(lit_args[0].strip("`"))
                        if method in _DELTA_METHODS:
                            model.ops.append(
                                DeltaOp(
                                    op=_DELTA_METHODS[method],
                                    file=module.file,
                                    line=node.lineno,
                                    source="code",
                                )
                            )
                    if dotted.endswith("spark.sql") and lit_args:
                        for op_name, rex in _SQL_OPS.items():
                            if op_name not in _SELF_EVIDENT_OPS and not delta_signal:
                                continue
                            if rex.search(lit_args[0]):
                                model.ops.append(
                                    DeltaOp(
                                        op=op_name,
                                        file=module.file,
                                        line=node.lineno,
                                        source="sql",
                                    )
                                )
                        model.features |= _feature_scan(lit_args[0])
                        model.properties |= _delta_props(lit_args[0])
                        r, w = _protocol(lit_args[0])
                        model.protocol_reader = max(model.protocol_reader, r)
                        model.protocol_writer = max(model.protocol_writer, w)
                    # .format("delta") / .option(...) reads+writes
                    if dotted.endswith(".format") and lit_args and lit_args[0].lower() == "delta":
                        # disambiguate read vs write via the chain prefix
                        chain_root = dotted.split(".")[0]
                        if re.search(r"(write|save|saveastable)", dotted):
                            model.delta_writes += 1
                        else:
                            model.delta_reads += 1
                        _ = chain_root
                    if dotted.endswith((".option", ".options")) and lit_args:
                        model.features |= _feature_scan(" ".join(lit_args))
                    # spark.conf.set("spark.databricks.delta.*")
                    if dotted.endswith((".set", ".conf")) and lit_args:
                        joined = " ".join(lit_args)
                        model.features |= _feature_scan(joined)
                        model.properties |= _delta_props(joined)

    # streaming delta endpoints (structured streaming sources/sinks)
    from forge_doctor.analyzers.streaming_model import streaming_model

    sm = streaming_model(ctx)
    model.streaming_delta = sum(1 for q in sm.queries if q.source == "delta" or q.sink == "delta")
    for q in sm.queries:
        ident = q.source_identifier if q.source == "delta" else ""
        if q.sink == "delta" and q.sink_identifier:
            ident = q.sink_identifier
        if ident and not ident.startswith(("s3", "hdfs", "/", "dbfs")):
            model.tables.add(ident)

    model.has_delta = bool(
        model.tables or model.ops or model.features or model.delta_reads or model.delta_writes
    )
    model.tables = set(sorted(model.tables))
    return model


def _attr_dotted(node: Any) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))
