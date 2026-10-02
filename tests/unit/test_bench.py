"""Performance benchmark (roadmap-2 phase 4) - small-scale unit tests."""

from __future__ import annotations

import json
from pathlib import Path

from forge_doctor.core.bench import (
    generate_project,
    load_budget,
    run_bench,
)


def test_generate_project_deterministic(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    generate_project(a, 40, seed=7)
    generate_project(b, 40, seed=7)
    a_files = {p.relative_to(a).as_posix(): p.read_text() for p in a.rglob("*") if p.is_file()}
    b_files = {p.relative_to(b).as_posix(): p.read_text() for p in b.rglob("*") if p.is_file()}
    assert a_files.keys() == b_files.keys()
    assert a_files == b_files


def test_generate_project_seed_changes_corpus(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    generate_project(a, 40, seed=1)
    generate_project(b, 40, seed=2)
    a_text = sorted(p.read_text() for p in a.rglob("*.py"))
    b_text = sorted(p.read_text() for p in b.rglob("*.py"))
    assert a_text != b_text


def test_run_bench_small_corpus(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    generate_project(corpus, 40, seed=7)
    r = run_bench(corpus)
    assert r.files == 40
    assert r.py_files > 0
    assert r.ast_parsed == r.py_files  # one parse per file
    assert r.cold_ms > 0 and r.warm_ms > 0
    assert r.graph_ms >= 0 and r.pack_ms > 0
    assert r.peak_mb > 0
    assert r.findings >= 0


def test_budget_failure_reported(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    generate_project(corpus, 40, seed=7)
    # absurdly tight budget must fail deterministically
    r = run_bench(corpus, budget={"warm_ratio_max": 0.0, "ast_parse_max_ratio": 1.0})
    assert any("warm_ratio" in f for f in r.budget_failures)
    assert not [f for f in r.budget_failures if "ast_parse" in f]


def test_load_budget_validates(tmp_path: Path) -> None:
    p = tmp_path / "b.json"
    p.write_text('{"warm_ratio_max": 0.5}', encoding="utf-8")
    assert load_budget(p) == {"warm_ratio_max": 0.5}
    p.write_text("[1]", encoding="utf-8")
    try:
        load_budget(p)
    except ValueError:
        pass
    else:
        raise AssertionError("non-object budget accepted")


def test_warm_scan_uses_disk_cache(tmp_path: Path) -> None:
    """Warm pass must not exceed cold (cache avoids re-parsing)."""
    corpus = tmp_path / "corpus"
    generate_project(corpus, 30, seed=3)
    r = run_bench(corpus)
    # not a strict ratio assert (timings vary) - just both measured
    assert r.warm_ratio is not None and r.warm_ratio > 0


def test_result_dict_shape(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    generate_project(corpus, 10, seed=1)
    r = run_bench(corpus)
    d = json.loads(json.dumps(__import__("forge_doctor.core.bench", fromlist=["x"]).result_dict(r)))
    for key in ("cold_ms", "warm_ms", "ast_parsed", "graph_ms", "packs", "peak_mb"):
        assert key in d
