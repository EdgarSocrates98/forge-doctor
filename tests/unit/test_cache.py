from pathlib import Path

import pytest

from forge_doctor.core.cache import ScanCache


@pytest.fixture(autouse=True)
def _isolated_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the user cache at tmp so tests never touch the real one."""
    monkeypatch.setenv("FORGE_DOCTOR_CACHE_DIR", str(tmp_path / ".user-cache"))


def test_cache_round_trip(tmp_path: Path):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    cache = ScanCache(tmp_path)
    sha = cache.sha256(Path("a.py"))
    assert sha and len(sha) == 64

    assert cache.get(Path("a.py"), sha) is None
    assert cache.misses == 1
    cache.put(Path("a.py"), sha, {"imports": [["os", None, None, 1, False]]})
    cache.update_facts(Path("a.py"), {"spark_buckets": {"x": []}})
    cache.save()

    cache2 = ScanCache(tmp_path)
    facts = cache2.get(Path("a.py"), sha)
    assert facts is not None
    assert facts["imports"] == [["os", None, None, 1, False]]
    assert facts["spark_buckets"] == {"x": []}
    assert cache2.hits == 1


def test_cache_invalidation_on_change(tmp_path: Path):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    cache = ScanCache(tmp_path)
    sha = cache.sha256(Path("a.py"))
    cache.put(Path("a.py"), sha, {"v": 1})
    cache.save()

    f.write_text("x = 2\n", encoding="utf-8")
    cache2 = ScanCache(tmp_path)
    assert cache2.get(Path("a.py"), cache2.sha256(Path("a.py"))) is None


def test_cache_prune_and_clear(tmp_path: Path):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    cache = ScanCache(tmp_path)
    cache.put(Path("a.py"), cache.sha256(Path("a.py")), {"v": 1})
    cache.put(Path("gone.py"), "deadbeef", {"v": 2})
    cache.prune({"a.py"})
    assert set(cache._files) == {"a.py"}
    cache.save()
    assert ScanCache(tmp_path).clear() is True
    assert not cache.path.exists()


def test_cache_disabled_noop(tmp_path: Path):
    cache = ScanCache(tmp_path, enabled=False)
    cache.put(Path("a.py"), "x", {"v": 1})
    assert cache.get(Path("a.py"), "x") is None
    cache.save()
    assert not cache.path.exists()


def test_cache_lives_outside_target(tmp_path: Path):
    """Cache poisoning guard: storage is the user cache dir, not the repo."""
    from forge_doctor.core.cache import cache_root

    cache = ScanCache(tmp_path)
    assert cache.path == cache_root() / cache.path.name
    assert ".forge-doctor" not in cache.path.parts


def test_in_project_cache_not_trusted(tmp_path: Path):
    """A .forge-doctor/cache file inside the target is ignored, not loaded."""
    legacy = tmp_path / ".forge-doctor" / "cache"
    legacy.mkdir(parents=True)
    (legacy / "scan-cache.json").write_text(
        '{"format": 2, "files": {"a.py": {"sha": "x", "facts": {"imports": []}}}}',
        encoding="utf-8",
    )
    cache = ScanCache(tmp_path)
    assert cache._files == {}  # never loads the in-project file
    cache.clear()  # but clean removes it
    assert not (legacy / "scan-cache.json").exists()


def test_dep_change_invalidates_dependent(tmp_path: Path):
    """reader.py changes -> job.py's cached facts/buckets recompute."""
    from forge_doctor.analyzers.index import project_index
    from forge_doctor.core.context import ProjectContext, ScanOptions

    # Explicit opt-in: the test exercises the cache, so it must not depend
    # on the CI auto-off default (use_cache=None -> off when CI=true).
    options = ScanOptions(use_cache=True)

    (tmp_path / "reader.py").write_text(
        "from pyspark.sql import SparkSession\n"
        "def load_orders(spark):\n"
        '    return spark.read.parquet("/orders")\n',
        encoding="utf-8",
    )
    (tmp_path / "job.py").write_text(
        "from reader import load_orders\ndf = load_orders(spark)\ndf.collect()\n",
        encoding="utf-8",
    )
    ctx = ProjectContext(root=tmp_path, options=options)
    idx1 = project_index(ctx)
    assert idx1.modules[Path("job.py")].tree is not None
    cache = ctx.__dict__["_fd_scan_cache"]
    cache.save()

    # Second scan: job.py restored entirely from cache (sha unchanged).
    ctx2 = ProjectContext(root=tmp_path, options=options)
    idx2 = project_index(ctx2)
    job2 = idx2.modules[Path("job.py")]
    assert job2.fresh is False  # served from facts

    # Change a dep: job.py's sha is identical but its dep provenance broke.
    (tmp_path / "reader.py").write_text(
        "from pyspark.sql import SparkSession\n"
        "def load_orders(spark):\n"
        '    return spark.read.parquet("/v2")\n',
        encoding="utf-8",
    )
    ctx3 = ProjectContext(root=tmp_path, options=options)
    idx3 = project_index(ctx3)
    job3 = idx3.modules[Path("job.py")]
    assert job3.fresh is True  # re-parsed, not served from stale facts


def test_transitive_dep_semantics_invalidate(tmp_path: Path):
    """source -> reader -> job: changing source's semantics invalidates job
    even though reader.py's content (and sha) is unchanged."""
    from forge_doctor.analyzers.index import project_index
    from forge_doctor.core.context import ProjectContext, ScanOptions

    options = ScanOptions(use_cache=True)
    (tmp_path / "source.py").write_text(
        'def load(spark):\n    return spark.read.parquet("/orders")\n',
        encoding="utf-8",
    )
    (tmp_path / "reader.py").write_text(
        "from source import load\ndef read_orders(spark):\n    return load(spark)\n",
        encoding="utf-8",
    )
    (tmp_path / "job.py").write_text(
        "from reader import read_orders\ndf = read_orders(spark)\ndf.collect()\n",
        encoding="utf-8",
    )
    ctx = ProjectContext(root=tmp_path, options=options)
    project_index(ctx)
    ctx.__dict__["_fd_scan_cache"].save()

    ctx2 = ProjectContext(root=tmp_path, options=options)
    idx2 = project_index(ctx2)
    assert idx2.modules[Path("job.py")].fresh is False
    assert idx2.modules[Path("reader.py")].fresh is False

    # load() stops returning a DataFrame: reader's file bytes are identical,
    # but its exported semantics drifted -> both dependents re-parse.
    (tmp_path / "source.py").write_text(
        "def load(spark):\n    return []\n",
        encoding="utf-8",
    )
    ctx3 = ProjectContext(root=tmp_path, options=options)
    idx3 = project_index(ctx3)
    assert idx3.modules[Path("source.py")].fresh is True
    assert idx3.modules[Path("reader.py")].fresh is True
    assert idx3.modules[Path("job.py")].fresh is True


def test_dep_cosmetic_change_keeps_dependent_cached(tmp_path: Path):
    """Dep edit that doesn't move its export signature -> dependents still cached."""
    from forge_doctor.analyzers.index import project_index
    from forge_doctor.core.context import ProjectContext, ScanOptions

    options = ScanOptions(use_cache=True)
    (tmp_path / "reader.py").write_text(
        'def load_orders(spark):\n    return spark.read.parquet("/orders")\n',
        encoding="utf-8",
    )
    (tmp_path / "job.py").write_text(
        "from reader import load_orders\ndf = load_orders(spark)\ndf.collect()\n",
        encoding="utf-8",
    )
    ctx = ProjectContext(root=tmp_path, options=options)
    project_index(ctx)
    ctx.__dict__["_fd_scan_cache"].save()

    # Comment-only change: reader.py re-parses (its own sha changed) but its
    # export signature is identical, so job.py keeps its cached facts.
    with (tmp_path / "reader.py").open("a", encoding="utf-8") as fh:
        fh.write("\n# comment only\n")
    ctx2 = ProjectContext(root=tmp_path, options=options)
    idx2 = project_index(ctx2)
    assert idx2.modules[Path("reader.py")].fresh is True
    assert idx2.modules[Path("job.py")].fresh is False


def test_ci_disables_cache_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from forge_doctor.core.cache import scan_cache
    from forge_doctor.core.context import ProjectContext, ScanOptions

    monkeypatch.setenv("CI", "true")
    ctx = ProjectContext(root=tmp_path)  # use_cache unset
    assert scan_cache(ctx).enabled is False
    ctx2 = ProjectContext(root=tmp_path, options=ScanOptions(use_cache=True))
    assert scan_cache(ctx2).enabled is True  # explicit --cache wins
