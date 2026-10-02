"""Bench adversarial cases: zero denominators, hostile budgets."""

from __future__ import annotations

import json
from pathlib import Path

from forge_doctor.core.bench import generate_project, load_budget, run_bench


def test_empty_dir_no_division_errors(tmp_path: Path) -> None:
    d = tmp_path / "empty"
    d.mkdir()
    r = run_bench(d, budget={"warm_ratio_max": 0.9, "ast_parse_max_ratio": 1.0})
    assert r.files == 0 and r.py_files == 0
    assert r.ast_ratio is None  # no .py files -> no denominator
    # budget on ratios with no denominators must not crash or lie
    assert not [f for f in r.budget_failures if "ast_parse" in f]


def test_generate_zero_files(tmp_path: Path) -> None:
    counts = generate_project(tmp_path / "z", 0, seed=1)
    assert counts == {"py": 0, "tf": 0, "sql": 0, "other": 0}


def test_budget_unknown_keys_ignored(tmp_path: Path) -> None:
    d = tmp_path / "p"
    d.mkdir()
    (d / "a.py").write_text("x=1\n", encoding="utf-8")
    r = run_bench(d, budget={"nonsense_key": 0.0001})
    assert r.budget_failures == []


def test_budget_malformed_json_raises(tmp_path: Path) -> None:
    p = tmp_path / "b.json"
    p.write_text("{oops", encoding="utf-8")
    try:
        load_budget(p)
    except json.JSONDecodeError:
        pass
    else:
        raise AssertionError("malformed budget accepted")


def test_budget_string_values_coerced(tmp_path: Path) -> None:
    p = tmp_path / "b.json"
    p.write_text('{"warm_ratio_max": "0.75"}', encoding="utf-8")
    assert load_budget(p) == {"warm_ratio_max": 0.75}
