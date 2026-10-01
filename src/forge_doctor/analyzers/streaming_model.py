"""Streaming semantic model - platform-agnostic, evidence-only.

Stage 1 producer is Spark Structured Streaming: ``readStream`` /
``writeStream`` chains recovered from the AST index (never executed).
Calls are grouped into queries by stream-variable receiver; when no
receiver resolves, the file is the documented fallback grouping.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from forge_doctor.analyzers.index import CallSite, PyModuleIndex
    from forge_doctor.core.context import ProjectContext

_CACHE_ATTR = "_forge_doctor_streaming_model"

_STATEFUL_OPS = {
    "groupby",
    "window",
    "join",
    "dropduplicates",
    "dropduplicateswithinwatermark",
    "mapgroupswithstate",
    "flatmapgroupswithstate",
    "transformwithstate",
    "deduplicate",
}

_FORMAT_SOURCES = {
    "kafka": "kafka",
    "kinesis": "kinesis",
    "delta": "delta",
    "iceberg": "iceberg",
    "rate": "rate",
    "rat_microbatch": "rate",
    "cloudfiles": "files",  # Auto Loader
    "json": "files",
    "csv": "files",
    "avro": "files",
    "parquet": "files",
    "orc": "files",
    "text": "files",
    "socket": "files",
}

_SINK_FORMATS = {
    "kafka": "kafka",
    "delta": "delta",
    "iceberg": "iceberg",
    "parquet": "files",
    "json": "files",
    "csv": "files",
    "avro": "files",
    "orc": "files",
    "console": "console",
    "memory": "memory",
}

_DYNAMIC_HINTS = re.compile(r"\{|%s|\.format\(|datetime|uuid|environ", re.I)
_TEMP_PATH = re.compile(r"^(/tmp|/var/tmp|tmp/|\.|file:/tmp|s3[a-z0-9]*://[^/]*/tmp)", re.I)


@dataclass(frozen=True)
class StreamingQuery:
    """One detected streaming query (best-effort static grouping)."""

    file: Path
    line: int
    name: str  # queryName literal, receiver var, or "<unnamed>"
    engine: str  # spark_ss | unknown
    source: str  # kafka|kinesis|files|delta|iceberg|rate|custom|""
    source_detail: str  # format literal or option name
    source_identifier: str  # literal topic/table/path identifying the source
    sink: str  # kafka|files|delta|iceberg|console|memory|foreachBatch|""
    sink_identifier: str  # literal toTable()/start()/option target
    output_mode: str  # append|update|complete|""
    trigger_kind: str  # processingTime|availableNow|once|continuous|""
    trigger_arg: str
    checkpoint: str  # literal path or ""
    checkpoint_dynamic: bool
    watermark: str  # "column:delay" or ""
    stateful_ops: tuple[str, ...]
    foreach_batch: str  # handler name or "present"
    grouping: str  # receiver | file


@dataclass
class StreamingProjectModel:
    """All streaming queries + loose evidence in a project."""

    queries: list[StreamingQuery] = field(default_factory=list)

    @property
    def has_streaming(self) -> bool:
        return bool(self.queries)

    @property
    def sources(self) -> set[str]:
        return {q.source for q in self.queries if q.source}

    @property
    def sinks(self) -> set[str]:
        return {q.sink for q in self.queries if q.sink}


def _call_name(site: object) -> str:
    """Terminal op name; chained calls arrive inside-out in ``site.dotted``."""
    name: str = getattr(site, "name", "")
    if name.isidentifier():
        return name
    dotted: str = getattr(site, "dotted", "")
    return dotted.split("(", 1)[0].rsplit(".", 1)[-1].lower()


def _call_arg_text(site: CallSite) -> str:
    """First inline arg expression from the inside-out dotted rendering."""
    dotted = site.dotted
    idx = dotted.rfind("(")
    if idx == -1:
        return ""
    inner = dotted[idx + 1 :].rstrip(")").strip()
    return inner.split(",", 1)[0].strip()


def _kwarg(site: CallSite, name: str) -> str:
    for k, v in site.kwargs:
        if k.lower() == name.lower():
            return v
    return ""


def _option_value(site: CallSite, key: str) -> str | None:
    """``option("key", "v")`` -> v; ``option("key")`` w/ non-literal -> ""."""
    if not site.args or site.args[0].lower() != key.lower():
        return None
    return site.args[1] if len(site.args) > 1 else ""


def _trigger_of(site: CallSite) -> tuple[str, str]:
    for kind in ("processingTime", "availableNow", "once", "continuous", "realTime"):
        v = _kwarg(site, kind)
        if v:
            return kind, v
        if any(k.lower() == kind.lower() for k, _ in site.kwargs):
            return kind, "non-literal"
    arg = _call_arg_text(site)
    for kind in ("ProcessingTime", "AvailableNow", "Once", "Continuous", "RealTime"):
        if kind.lower() in arg.lower():
            return kind, arg
    return ("", "")


def _module_queries(module: PyModuleIndex, relative: Path) -> list[StreamingQuery]:
    names = [_call_name(s) for s in module.calls]
    stream_vars: set[str] = set()
    # fixpoint: v = <readStream expr> makes v a stream var; v = <expr on a
    # stream var> (withWatermark/groupBy/join) propagates streamness
    changed = True
    while changed:
        changed = False
        for a in module.assigns:
            expr = (a.value_call or "").lower() + " " + (a.value_root or "").lower()
            if a.target in stream_vars:
                continue
            if "readstream" in expr or any(
                re.search(rf"\b{re.escape(v.lower())}\b", expr) for v in stream_vars
            ):
                stream_vars.add(a.target)
                changed = True
    for a in module.assigns:
        if a.value_call and "writestream" in a.value_call.lower():
            root = (a.value_root or "").lower()
            if root and root not in stream_vars and "readstream" not in a.value_call.lower():
                stream_vars.add(root)
    has_stream = any(
        "readstream" in s.dotted.lower() or "writestream" in s.dotted.lower() for s in module.calls
    )
    if not has_stream:
        return []

    # union stream vars linked by assignment: `agg = orders.groupBy(...)`
    # means agg's writeStream chain and orders' readStream chain are one
    # logical query.
    parent = {v: v for v in stream_vars}

    def find(v: str) -> str:
        while parent[v] != v:
            parent[v] = parent[parent[v]]
            v = parent[v]
        return v

    for a in module.assigns:
        if a.target not in stream_vars:
            continue
        expr = (a.value_call or "") + " " + (a.value_root or "")
        src = next(
            (
                v
                for v in stream_vars
                if v != a.target and re.search(rf"\b{re.escape(v)}\b", expr, re.IGNORECASE)
            ),
            None,
        )
        if src:
            parent[find(a.target)] = find(src)

    # group call sites per canonical stream var; unresolvable chains fall
    # back to file. `spark.readStream...` chains (receiver "spark") belong to
    # the var of the nearest preceding `v = <readStream expr>` assignment.
    read_assigns = sorted(
        (a.line, find(a.target))
        for a in module.assigns
        if a.value_call and "readstream" in a.value_call.lower() and a.target in stream_vars
    )

    def _owner(site: CallSite, dotted: str) -> str:
        for v in stream_vars:
            if dotted.startswith(v.lower() + "."):
                return find(v)
        if site.receiver and site.receiver in stream_vars:
            return find(site.receiver)
        candidates = [t for ln, t in read_assigns if ln <= site.line]
        if candidates and "readstream" in dotted:
            return candidates[-1]
        return "*file*"

    groups: dict[str, list[tuple[CallSite, str]]] = {find(v): [] for v in stream_vars}
    groups["*file*"] = []
    for site, name in zip(module.calls, names, strict=True):
        dotted = site.dotted.lower()
        if (
            "readstream" in dotted
            or "writestream" in dotted
            or (site.receiver and site.receiver in stream_vars)
        ):
            groups.setdefault(_owner(site, dotted), []).append((site, name))

    queries: list[StreamingQuery] = []
    for key in sorted(groups):
        sites = groups[key]
        if not sites:
            continue
        queries.append(_build_query(sites, relative, key))

    return sorted(queries, key=lambda q: (q.file.as_posix(), q.line, q.name))


# option() keys that name a concrete endpoint, by side. Literal values
# only - the AST index drops non-literal args, so "" means unidentified.
_SOURCE_ID_KEYS = ("subscribe", "subscribePattern", "streamName", "table", "path")
_SINK_ID_KEYS = ("topic", "table", "path")


def _build_query(sites: list[tuple[CallSite, str]], relative: Path, key: str) -> StreamingQuery:
    source = source_detail = sink = ""
    source_identifier = sink_identifier = ""
    output_mode = trigger_kind = trigger_arg = ""
    checkpoint = ""
    checkpoint_dynamic = False
    watermark = ""
    foreach = ""
    qname = ""
    write_var = ""
    stateful: set[str] = set()
    first_line = min((s.line for s, _ in sites), default=0)

    for site, name in sites:
        dotted_l = site.dotted.lower()
        if "writestream" in dotted_l and not write_var:
            write_var = site.receiver or dotted_l.split(".", 1)[0]
        args = list(site.args)
        read_side = "readstream" in dotted_l or name in {"load", "readstream"}
        write_side = "writestream" in dotted_l

        if name == "format" and args:
            fmt = args[0].lower()
            if write_side:
                sink = _SINK_FORMATS.get(fmt, "custom")
            elif read_side:
                source = _FORMAT_SOURCES.get(fmt, "custom")
                source_detail = args[0]
        if name == "outputmode" and args:
            output_mode = args[0].lower()
        elif name == "outputmode" and site.kwargs:
            output_mode = site.kwargs[0][1].lower()
        if name == "trigger":
            kind, arg = _trigger_of(site)
            if kind:
                trigger_kind, trigger_arg = kind, arg
        if name == "queryname" and args:
            qname = args[0]
        if name == "withwatermark" and args:
            watermark = ":".join(args[:2])
        if name == "foreachbatch":
            sink = "foreachBatch"
            # handler is a Name expr - the index drops non-literal args,
            # so the handler name is not statically recoverable here.
            foreach = "present"
        if name in _STATEFUL_OPS:
            stateful.add(name)
        if name == "totable" and args:
            sink_identifier = args[0]
        if name == "start" and write_side and args:
            sink_identifier = sink_identifier or args[0]
        if name == "table" and read_side and args:
            source_identifier = source_identifier or args[0]
        # load("kafka") names a format; load("s3://...") names a path.
        if name == "load" and read_side and args and args[0].lower() not in _FORMAT_SOURCES:
            source_identifier = source_identifier or args[0]
        if name == "option":
            v = _option_value(site, "checkpointLocation")
            if v is not None:
                if v:
                    checkpoint = v
                else:
                    checkpoint_dynamic = True
            if read_side:
                for key in _SOURCE_ID_KEYS:
                    v = _option_value(site, key)
                    if v:
                        source_identifier = source_identifier or v
            if write_side:
                for key in _SINK_ID_KEYS:
                    v = _option_value(site, key)
                    if v:
                        sink_identifier = sink_identifier or v
        if name in {"option", "options"}:
            for arg in args:
                if "kafka" in arg.lower():
                    source = source or "kafka"
                    source_detail = source_detail or arg
                if "kinesis" in arg.lower():
                    source = source or "kinesis"
                    source_detail = source_detail or arg
        if name == "load" and read_side:
            source = source or (
                "files" if not args else _FORMAT_SOURCES.get(args[0].lower(), "custom")
            )
            source_detail = source_detail or (args[0] if args else "")

    if checkpoint and _DYNAMIC_HINTS.search(checkpoint):
        checkpoint_dynamic = True
    return StreamingQuery(
        file=relative,
        line=first_line,
        name=qname or write_var or (key if key != "*file*" else "<unnamed>"),
        engine="spark_ss",
        source=source,
        source_detail=source_detail,
        source_identifier=source_identifier,
        sink=sink,
        sink_identifier=sink_identifier,
        output_mode=output_mode,
        trigger_kind=trigger_kind,
        trigger_arg=trigger_arg,
        checkpoint=checkpoint,
        checkpoint_dynamic=checkpoint_dynamic,
        watermark=watermark,
        stateful_ops=tuple(sorted(stateful)),
        foreach_batch=foreach,
        grouping="receiver" if key != "*file*" else "file",
    )


def streaming_model(ctx: ProjectContext) -> StreamingProjectModel:
    """Build (once, memoized on ctx) the project's streaming model."""
    cached = getattr(ctx, _CACHE_ATTR, None)
    if cached is not None:
        return cast(StreamingProjectModel, cached)

    from forge_doctor.analyzers.index import project_index

    queries: list[StreamingQuery] = []
    index = project_index(ctx)
    for relative, module in sorted(index.modules.items(), key=lambda kv: kv[0].as_posix()):
        queries.extend(_module_queries(module, relative))

    model = StreamingProjectModel(queries=queries)
    setattr(ctx, _CACHE_ATTR, model)
    return model
