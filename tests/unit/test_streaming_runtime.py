"""Streaming runtime diagnostics + new adapters tests (Phase 9)."""

from __future__ import annotations

import json
from pathlib import Path

from forge_doctor_data.analyzers.runtime_evidence import ingest_artifact
from forge_doctor_data.analyzers.streaming_runtime import diagnose_progress, parse_progress
from forge_doctor_data.core.delivery import derive_delivery


def _progress(batch: int, **over) -> dict:
    base = {
        "id": "q1",
        "name": "s",
        "batchId": batch,
        "numInputRows": 10,
        "inputRowsPerSecond": 10.0,
        "processedRowsPerSecond": 20.0,
        "durationMs": {"triggerExecution": 500, "walCommit": 100, "commit": 50},
        "sink": {"description": "x"},
    }
    base.update(over)
    return base


def _write(tmp_path: Path, name: str, doc: dict) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def test_parse_progress(tmp_path: Path) -> None:
    p = _write(tmp_path, "b0.json", _progress(0))
    b = parse_progress(p)
    assert b is not None
    assert b.batch_id == 0
    assert b.input_rps == 10.0 and b.proc_rps == 20.0
    assert b.duration_ms == 650.0


def test_parse_progress_rejects_garbage(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("not json", encoding="utf-8")
    assert parse_progress(p) is None


def test_parse_progress_rejects_non_progress(tmp_path: Path) -> None:
    p = _write(tmp_path, "other.json", {"foo": 1})
    assert parse_progress(p) is None


def test_rate_imbalance_diagnosed(tmp_path: Path) -> None:
    paths = [
        _write(
            tmp_path,
            f"b{i}.json",
            _progress(i, inputRowsPerSecond=50.0, processedRowsPerSecond=10.0),
        )
        for i in range(3)
    ]
    report = diagnose_progress(paths)
    codes = {d.code for d in report.diagnoses}
    assert "SRATE001" in codes


def test_balanced_stream_clean(tmp_path: Path) -> None:
    paths = [_write(tmp_path, f"b{i}.json", _progress(i)) for i in range(3)]
    report = diagnose_progress(paths)
    assert all(d.code != "SRATE001" for d in report.diagnoses)


def test_state_growth_diagnosed(tmp_path: Path) -> None:
    paths = [
        _write(
            tmp_path,
            f"b{i}.json",
            _progress(i, stateOperators=[{"operatorName": "s", "numRowsTotal": 100 * (i + 1)}]),
        )
        for i in range(4)
    ]
    report = diagnose_progress(paths)
    assert any(d.code == "SSTATE002" for d in report.diagnoses)


def test_kafka_lag_diagnosed(tmp_path: Path) -> None:
    doc = _progress(0)
    doc["sources"] = [
        {
            "description": "KafkaV2",
            "startOffset": {"t": {"0": 0}},
            "latestOffset": {"t": {"0": 500}},
        }
    ]
    paths = [_write(tmp_path, "b0.json", doc)]
    report = diagnose_progress(paths)
    assert any(d.code == "SKFK005" and "500" in d.message for d in report.diagnoses)


def test_checkpoint_instability(tmp_path: Path) -> None:
    paths = []
    for i, wal in enumerate([100, 100, 2500, 100]):
        paths.append(
            _write(
                tmp_path,
                f"b{i}.json",
                _progress(i, durationMs={"triggerExecution": 500, "walCommit": wal}),
            )
        )
    report = diagnose_progress(paths)
    assert any(d.code == "SCKPT004" for d in report.diagnoses)


def test_watermark_lag(tmp_path: Path) -> None:
    doc = _progress(0)
    doc["eventTime"] = {"watermark": "2026-10-02T10:00:00.000Z", "max": "2026-10-02T10:10:00.000Z"}
    paths = [_write(tmp_path, "b0.json", doc)]
    report = diagnose_progress(paths)
    assert any(d.code == "SWM003" for d in report.diagnoses)


def test_unparsed_artifacts_reported(tmp_path: Path) -> None:
    p = tmp_path / "junk.txt"
    p.write_text("junk", encoding="utf-8")
    report = diagnose_progress([p])
    assert report.unparsed == ["junk.txt"]
    assert not report.batches


# --- derive_delivery truth table -------------------------------------------


def test_delivery_no_checkpoint() -> None:
    s = derive_delivery(source="kafka", checkpoint=False, sink="delta")
    assert s.level == "at-most-once"


def test_delivery_checkpoint_plus_txn_sink() -> None:
    s = derive_delivery(source="kafka", checkpoint=True, sink="delta")
    assert s.level == "effectively-once" and s.certainty == "derived"


def test_delivery_checkpoint_non_txn_sink() -> None:
    s = derive_delivery(source="kafka", checkpoint=True, sink="console")
    assert s.level == "at-least-once"


def test_delivery_kafka_sink() -> None:
    s = derive_delivery(source="kafka", checkpoint=True, sink="kafka")
    assert s.level == "at-least-once"


def test_delivery_idempotent_foreachbatch() -> None:
    s = derive_delivery(source="kafka", checkpoint=True, sink="foreachBatch", idempotent_sink=True)
    assert s.level == "effectively-once"


def test_delivery_flink_exactly_once_claim() -> None:
    s = derive_delivery(
        source="kafka",
        checkpoint=True,
        engine="flink",
        sink="kafka",
        commit_mode="EXACTLY_ONCE",
        idempotent_sink=True,
    )
    assert s.level == "exactly-once-claim" and s.certainty == "claimed"


def test_delivery_flink_at_least_once_mode() -> None:
    s = derive_delivery(
        source="kafka",
        checkpoint=True,
        engine="flink",
        sink="kafka",
        commit_mode="AT_LEAST_ONCE",
    )
    assert s.level == "at-least-once"


def test_delivery_non_replayable_source() -> None:
    s = derive_delivery(source="socket", checkpoint=True, sink="delta")
    assert s.level == "at-least-once"


def test_delivery_auto_commit_at_most_once() -> None:
    s = derive_delivery(source="kafka", checkpoint=False, sink="x", auto_commit=True)
    assert s.level == "at-most-once"


def test_delivery_missing_evidence_not_fabricated() -> None:
    s = derive_delivery(source="kafka", checkpoint=True, sink="")
    assert s.certainty == "unknown"
    s2 = derive_delivery(source="", checkpoint=True, sink="delta")
    assert s2.certainty == "unknown"


def test_delivery_basis_lists_inputs() -> None:
    s = derive_delivery(source="kafka", checkpoint=True, sink="delta")
    basis = " ".join(s.basis)
    assert "source:kafka" in basis and "checkpoint:present" in basis and "sink:delta" in basis


# --- runtime adapters --------------------------------------------------------


def test_flink_checkpoint_adapter(tmp_path: Path) -> None:
    doc = {
        "checkpoints": {
            "counts": {"completed": 9, "failed": 1, "total": 10},
            "history": [
                {"id": 1, "status": "COMPLETED", "end_to_end_duration": 500},
                {"id": 2, "status": "FAILED", "end_to_end_duration": 0},
            ],
        }
    }
    p = _write(tmp_path, "ckpt.json", doc)
    m = ingest_artifact(p)
    assert m.source == "flink_checkpoints"
    assert m.identifiers["checkpoints_failed"] == "1"
    assert any(e.code == "CheckpointFailed" for e in m.errors)
    assert any(ex.state == "failed" for ex in m.executions)


def test_stream_metrics_adapter(tmp_path: Path) -> None:
    doc = {
        "metrics": [
            {"name": "MillisBehindLatest", "value": 30000, "unit": "ms", "stream": "s1"},
            {"name": "Unrelated", "value": 5},
        ]
    }
    p = _write(tmp_path, "m.json", doc)
    m = ingest_artifact(p)
    assert m.source == "stream_metrics"
    names = {x.name for x in m.metrics}
    assert "MillisBehindLatest" in names and "Unrelated" not in names
