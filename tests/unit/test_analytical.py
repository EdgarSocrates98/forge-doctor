"""Analytical engines: shared model, CH/PIN/DRU checks, CLI (spec 219)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from forge_doctor.analyzers.analytical_model import analytical_model
from forge_doctor.analyzers.sql_ast import SQLGLOT_AVAILABLE
from forge_doctor.checks.analytical import CHECKS
from forge_doctor.cli.app import app
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.models import Confidence

runner = CliRunner()
needs_sqlglot = pytest.mark.skipif(not SQLGLOT_AVAILABLE, reason="sqlglot not installed")


def _ctx(tmp_path: Path) -> ProjectContext:
    return ProjectContext(root=tmp_path)


def _findings(tmp_path: Path) -> dict[str, str]:
    ctx = _ctx(tmp_path)
    return {f.check_id: f.message for c in CHECKS for f in c.run(ctx)}


_CH_DDL = """
CREATE TABLE events (id UInt64, ts DateTime) ENGINE = MergeTree()
ORDER BY ts PARTITION BY toYYYYMM(ts);
"""


# ---------------------------------------------------------------------------
# Model — ClickHouse


@needs_sqlglot
def test_clickhouse_table_parsed(tmp_path: Path) -> None:
    (tmp_path / "ddl.sql").write_text(_CH_DDL, encoding="utf-8")
    model = analytical_model(_ctx(tmp_path))
    assert model.has_evidence
    t = model.tables_of("clickhouse")[0]
    assert t.name == "events"
    assert t.table_engine.lower() == "mergetree"
    assert "ts" in t.order_by
    assert "toYYYYMM" in t.partition_by


@needs_sqlglot
def test_mysql_engine_not_attributed(tmp_path: Path) -> None:
    """ENGINE=InnoDB/MyISAM is MySQL, not ClickHouse."""
    (tmp_path / "m.sql").write_text(
        "CREATE TABLE t (id INT) ENGINE=InnoDB;\nCREATE TABLE u (id INT) ENGINE=MyISAM;\n",
        encoding="utf-8",
    )
    assert not analytical_model(_ctx(tmp_path)).has_evidence


@needs_sqlglot
def test_engine_without_parens_still_clickhouse(tmp_path: Path) -> None:
    (tmp_path / "d.sql").write_text(
        "CREATE TABLE t (id UInt64) ENGINE = MergeTree ORDER BY id;\n",
        encoding="utf-8",
    )
    model = analytical_model(_ctx(tmp_path))
    assert model.tables_of("clickhouse")


def test_no_evidence_empty(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text('{"app": "x"}', encoding="utf-8")
    assert not analytical_model(_ctx(tmp_path)).has_evidence


# ---------------------------------------------------------------------------
# Model — Pinot / Druid


def test_pinot_table_and_schema(tmp_path: Path) -> None:
    (tmp_path / "t.table.json").write_text(
        json.dumps(
            {
                "tableName": "orders",
                "tableType": "REALTIME",
                "segmentsConfig": {"retentionTimeValue": "7"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "s.schema.json").write_text(
        json.dumps(
            {
                "schemaName": "orders",
                "dimensionFieldSpecs": [{"name": "d1", "dataType": "STRING"}],
                "metricFieldSpecs": [{"name": "m1", "dataType": "DOUBLE"}],
            }
        ),
        encoding="utf-8",
    )
    model = analytical_model(_ctx(tmp_path))
    t = model.tables_of("pinot")[0]
    assert t.name == "orders" and t.table_type == "REALTIME"
    assert model.pinot_schemas[0].dims == ("d1",)
    assert model.pinot_schemas[0].metrics == ("m1",)


def test_generic_json_not_attributed(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name": "x", "scripts": {}}', encoding="utf-8")
    assert not analytical_model(_ctx(tmp_path)).has_evidence


def test_druid_ingestion_spec(tmp_path: Path) -> None:
    (tmp_path / "ingest.json").write_text(
        json.dumps(
            {
                "ingestionSpec": {
                    "dataSchema": {
                        "dataSource": "events",
                        "metricsSpec": [{"name": "c"}],
                        "dimensionsSpec": {"dimensions": ["a", "b"]},
                        "granularitySpec": {"rollup": True},
                    },
                    "ioConfig": {"type": "kafka"},
                    "tuningConfig": {"partitionsSpec": {"type": "hashed"}},
                }
            }
        ),
        encoding="utf-8",
    )
    model = analytical_model(_ctx(tmp_path))
    t = model.tables_of("druid")[0]
    assert t.name == "events"
    assert t.prop("rollup") == "true"
    assert t.prop("partitionsSpec") == "true"


# ---------------------------------------------------------------------------
# Checks — ClickHouse


@needs_sqlglot
def test_ch001_missing_order_by(tmp_path: Path) -> None:
    (tmp_path / "d.sql").write_text(
        "CREATE TABLE t (id UInt64) ENGINE = MergeTree() PARTITION BY id;\n",
        encoding="utf-8",
    )
    assert "CH001" in _findings(tmp_path)


@needs_sqlglot
def test_ch001_with_order_by_quiet(tmp_path: Path) -> None:
    (tmp_path / "d.sql").write_text(_CH_DDL, encoding="utf-8")
    assert "CH001" not in _findings(tmp_path)


@needs_sqlglot
def test_ch002_replicated_no_keeper(tmp_path: Path) -> None:
    (tmp_path / "d.sql").write_text(
        "CREATE TABLE r (id UInt64) ENGINE = ReplicatedMergeTree('/p','r') ORDER BY id;\n",
        encoding="utf-8",
    )
    assert "CH002" in _findings(tmp_path)


@needs_sqlglot
def test_ch002_keeper_config_quiet(tmp_path: Path) -> None:
    (tmp_path / "d.sql").write_text(
        "CREATE TABLE r (id UInt64) ENGINE = ReplicatedMergeTree('/p','r') ORDER BY id;\n",
        encoding="utf-8",
    )
    (tmp_path / "keeper_config.xml").write_text("<zookeeper><node/></zookeeper>")
    assert "CH002" not in _findings(tmp_path)


@needs_sqlglot
def test_ch003_distributed_missing_local(tmp_path: Path) -> None:
    (tmp_path / "d.sql").write_text(
        "CREATE TABLE dist AS x ENGINE = Distributed('c','db','ghost_local');\n",
        encoding="utf-8",
    )
    found = _findings(tmp_path)
    assert "CH003" in found
    assert "ghost_local" in found["CH003"]


@needs_sqlglot
def test_ch003_local_defined_quiet(tmp_path: Path) -> None:
    (tmp_path / "d.sql").write_text(
        "CREATE TABLE loc (id UInt64) ENGINE = MergeTree() ORDER BY id;\n"
        "CREATE TABLE dist AS loc ENGINE = Distributed('c','db','loc');\n",
        encoding="utf-8",
    )
    assert "CH003" not in _findings(tmp_path)


@needs_sqlglot
def test_ch004_kafka_no_dedupe(tmp_path: Path) -> None:
    (tmp_path / "d.sql").write_text(
        "CREATE TABLE k (s String) ENGINE = Kafka SETTINGS "
        "kafka_broker_list='b:1', kafka_topic_list='t';\n",
        encoding="utf-8",
    )
    assert "CH004" in _findings(tmp_path)


@needs_sqlglot
def test_ch004_kafka_with_replacing_ok(tmp_path: Path) -> None:
    (tmp_path / "d.sql").write_text(
        "CREATE TABLE k (s String) ENGINE = Kafka SETTINGS "
        "kafka_broker_list='b:1', kafka_topic_list='t';\n"
        "CREATE TABLE dedup (s String) ENGINE = ReplacingMergeTree() ORDER BY s;\n",
        encoding="utf-8",
    )
    assert "CH004" not in _findings(tmp_path)


# ---------------------------------------------------------------------------
# Checks — Pinot


def _pinot_project(tmp_path: Path) -> None:
    (tmp_path / "orders.table.json").write_text(
        json.dumps(
            {
                "tableName": "orders",
                "tableType": "REALTIME",
                "segmentsConfig": {"schemaName": "orders"},
                "tableIndexConfig": {"noDictionaryColumns": ["device_id"]},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "orders.schema.json").write_text(
        json.dumps(
            {
                "schemaName": "orders",
                "dimensionFieldSpecs": [
                    {"name": "device_id", "dataType": "STRING", "cardinality": 99999}
                ],
            }
        ),
        encoding="utf-8",
    )


def test_pin001_realtime_no_retention(tmp_path: Path) -> None:
    _pinot_project(tmp_path)
    assert "PIN001" in _findings(tmp_path)


def test_pin001_retention_ok(tmp_path: Path) -> None:
    _pinot_project(tmp_path)
    tbl = json.loads((tmp_path / "orders.table.json").read_text())
    tbl["segmentsConfig"]["retentionTimeValue"] = "7"
    tbl["segmentsConfig"]["retentionTimeUnit"] = "DAYS"
    (tmp_path / "orders.table.json").write_text(json.dumps(tbl))
    assert "PIN001" not in _findings(tmp_path)


def test_pin002_filtered_highcard_no_index(tmp_path: Path) -> None:
    _pinot_project(tmp_path)
    (tmp_path / "q.sql").write_text(
        "SELECT * FROM orders WHERE device_id = 'x';\n", encoding="utf-8"
    )
    found = _findings(tmp_path)
    assert "PIN002" in found
    assert "device_id" in found["PIN002"]


def test_pin002_unfiltered_dim_quiet(tmp_path: Path) -> None:
    _pinot_project(tmp_path)
    (tmp_path / "q.sql").write_text("SELECT * FROM orders;\n", encoding="utf-8")
    assert "PIN002" not in _findings(tmp_path)


def test_pin002_indexed_dim_quiet(tmp_path: Path) -> None:
    _pinot_project(tmp_path)
    tbl = json.loads((tmp_path / "orders.table.json").read_text())
    tbl["tableIndexConfig"]["invertedIndexColumns"] = ["device_id"]
    (tmp_path / "orders.table.json").write_text(json.dumps(tbl))
    (tmp_path / "q.sql").write_text(
        "SELECT * FROM orders WHERE device_id = 'x';\n", encoding="utf-8"
    )
    assert "PIN002" not in _findings(tmp_path)


def test_pin003_groupby_heavy_no_startree(tmp_path: Path) -> None:
    _pinot_project(tmp_path)
    export = tmp_path / "pinot" / "queries.json"
    export.parent.mkdir()
    export.write_text(
        json.dumps(
            [
                {"query": "SELECT a FROM orders GROUP BY a", "executionMillis": 10},
                {"query": "SELECT b FROM orders GROUP BY b", "executionMillis": 20},
            ]
        ),
        encoding="utf-8",
    )
    ctx = _ctx(tmp_path)
    res = [f for c in CHECKS for f in c.run(ctx) if f.check_id == "PIN003"]
    assert res
    assert res[0].confidence == Confidence.MEDIUM


def test_pin003_few_groupbys_quiet(tmp_path: Path) -> None:
    _pinot_project(tmp_path)
    export = tmp_path / "pinot" / "queries.json"
    export.parent.mkdir()
    export.write_text(
        json.dumps([{"query": "SELECT a FROM orders GROUP BY a"}]),
        encoding="utf-8",
    )
    assert "PIN003" not in _findings(tmp_path)


# ---------------------------------------------------------------------------
# Checks — Druid


_DRUID_NO_PART = {
    "ingestionSpec": {
        "dataSchema": {
            "dataSource": "events",
            "dimensionsSpec": {"dimensions": ["a", "b", "c"]},
            "metricsSpec": [{"name": "m"}],
            "granularitySpec": {"rollup": False},
        },
        "ioConfig": {"type": "kafka"},
        "tuningConfig": {},
    }
}


def test_dru001_no_partitionspec(tmp_path: Path) -> None:
    (tmp_path / "i.json").write_text(json.dumps(_DRUID_NO_PART), encoding="utf-8")
    assert "DRU001" in _findings(tmp_path)


def test_dru001_partitioned_ok(tmp_path: Path) -> None:
    spec = json.loads(json.dumps(_DRUID_NO_PART))
    spec["ingestionSpec"]["tuningConfig"]["partitionsSpec"] = {"type": "hashed"}
    (tmp_path / "i.json").write_text(json.dumps(spec), encoding="utf-8")
    assert "DRU001" not in _findings(tmp_path)


def test_dru002_rollup_disabled_wide(tmp_path: Path) -> None:
    (tmp_path / "i.json").write_text(json.dumps(_DRUID_NO_PART), encoding="utf-8")
    assert "DRU002" in _findings(tmp_path)


def test_dru002_rollup_enabled_quiet(tmp_path: Path) -> None:
    spec = json.loads(json.dumps(_DRUID_NO_PART))
    spec["ingestionSpec"]["dataSchema"]["granularitySpec"]["rollup"] = True
    (tmp_path / "i.json").write_text(json.dumps(spec), encoding="utf-8")
    assert "DRU002" not in _findings(tmp_path)


# ---------------------------------------------------------------------------
# CLI


@needs_sqlglot
def test_analytical_inspect_cli(tmp_path: Path) -> None:
    (tmp_path / "d.sql").write_text(_CH_DDL, encoding="utf-8")
    res = runner.invoke(app, ["analytical", "inspect", str(tmp_path)])
    assert res.exit_code == 0
    assert "clickhouse" in res.output
    assert "events" in res.output


def test_analytical_inspect_empty(tmp_path: Path) -> None:
    res = runner.invoke(app, ["analytical", "inspect", str(tmp_path)])
    assert res.exit_code == 0
