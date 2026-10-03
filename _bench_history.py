"""Spec-241 benchmark: history record/read/baseline at 10k/100k/1M."""

import time
from pathlib import Path

from forge_doctor.core.execution_history import (
    baseline_for,
    build_series,
    iter_samples,
    record_executions,
)
from forge_doctor.core.execution_model import (
    ExecutionStatus,
    QueryExecution,
)


def make(n: int) -> list[QueryExecution]:
    return [
        QueryExecution(
            execution_id=f"e{i}",
            engine="spark",
            query_fingerprint=f"fp{i % 500}",
            start_time=1700000000000 + i * 1000,
            duration_ms=100.0 + (i % 37),
            bytes_read=1e8 + i,
            status=ExecutionStatus.COMPLETED,
        )
        for i in range(n)
    ]


def main() -> None:
    root = Path(".pytest_tmp/history-bench")
    import shutil

    shutil.rmtree(root, ignore_errors=True)
    for n in (10_000, 100_000, 1_000_000):
        execs = make(n)
        t0 = time.perf_counter()
        path = record_executions(root, execs, label=f"b{n}")
        t1 = time.perf_counter()
        count = sum(1 for _ in iter_samples(root, kind="production"))
        t2 = time.perf_counter()
        series = build_series_from_samples(count) if False else None
        print(
            f"n={n:>7}: record={t1 - t0:.2f}s read_all={t2 - t1:.2f}s "
            f"({(t1 - t0) / n * 1e6:.0f}us/sample write)"
        )
    # baseline over the full recorded history
    t0 = time.perf_counter()
    from forge_doctor.core.execution_model import QueryExecution as Q

    execs = [
        Q(
            execution_id=s.execution_id,
            engine=s.engine,
            query_fingerprint=s.fingerprint,
            start_time=s.timestamp,
            duration_ms=s.duration_ms,
            bytes_read=s.bytes_read,
        )
        for s in iter_samples(root, kind="production")
    ]
    series = build_series(execs)
    t1 = time.perf_counter()
    for s in series.values():
        baseline_for(s)
    t2 = time.perf_counter()
    print(
        f"series+baselines over {len(execs)} samples / {len(series)} series: "
        f"build={t1 - t0:.2f}s baselines={t2 - t1:.2f}s"
    )


if __name__ == "__main__":
    main()
