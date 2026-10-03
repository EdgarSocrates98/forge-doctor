"""Spec 247 — platform portfolio, duplication, complexity, learning."""

from __future__ import annotations

from pathlib import Path

from forge_doctor.core.platform_graph import (
    DataPlatformGraph,
    Entity,
    Relationship,
)
from forge_doctor.core.platform_graph import (
    EntityKind as K,
)
from forge_doctor.core.platform_graph import (
    RelKind as R,
)
from forge_doctor.core.portfolio import (
    DuplicationClass,
    DuplicationKind,
    LifecycleStatus,
    ValidatedOptimizationEvidence,
    answer_cross_cloud_concentration,
    answer_deprecated,
    answer_engines_per_workload,
    answer_team_cross_platform_deps,
    build_portfolio,
    cross_cloud_edges,
    duplication_signals,
    load_optimization_evidence,
    record_optimization_evidence,
    recurring_patterns,
)
from forge_doctor.core.workspace import WorkspaceModel, WorkspaceRepo


def _e(kind: K, domain: str, ident: str, **attrs: str) -> Entity:
    return Entity(
        kind=kind,
        domain=domain,
        identifier=ident,
        attrs=tuple(sorted(attrs.items())),
    )


def _model(g: DataPlatformGraph) -> WorkspaceModel:
    return WorkspaceModel(
        root=Path("."),
        repositories=(
            WorkspaceRepo("repoA", "repoA", ("pyproject.toml",), ("python",)),
            WorkspaceRepo("repoB", "repoB", ("pyproject.toml",), ("python",)),
        ),
        graph=g,
    )


def _dup_graph() -> DataPlatformGraph:
    g = DataPlatformGraph()
    g.add_entity(_e(K.STORAGE_LOCATION, "s3", "raw-orders"))
    g.add_entity(_e(K.STORAGE_LOCATION, "gcs", "raw-orders"))
    g.add_entity(_e(K.STREAM, "kinesis", "events"))
    g.add_entity(_e(K.STREAM, "kafka", "events"))
    g.add_entity(_e(K.WORKFLOW, "airflow", "etl"))
    g.add_entity(_e(K.WORKFLOW, "adf", "etl"))
    return g


class TestDuplication:
    def test_cross_cloud_copy(self) -> None:
        dups = duplication_signals(_dup_graph())
        cc = [d for d in dups if d.kind is DuplicationKind.CROSS_CLOUD_COPY]
        assert any(d.subject == "raw-orders" for d in cc)
        assert all(d.classification is DuplicationClass.OPPORTUNITY for d in cc)

    def test_dataset_across_platforms(self) -> None:
        dups = duplication_signals(_dup_graph())
        assert any(
            d.subject == "events" and d.kind is DuplicationKind.DATASET_ACROSS_PLATFORMS
            for d in dups
        )

    def test_workload_across_orchestrators(self) -> None:
        dups = duplication_signals(_dup_graph())
        assert any(
            d.subject == "etl" and d.kind is DuplicationKind.WORKLOAD_ACROSS_ORCHESTRATORS
            for d in dups
        )

    def test_no_signal_single_platform(self) -> None:
        g = DataPlatformGraph()
        g.add_entity(_e(K.STREAM, "kafka", "a"))
        g.add_entity(_e(K.STREAM, "kafka", "a2"))
        assert duplication_signals(g) == ()


class TestPortfolio:
    def test_build_portfolio_counts(self) -> None:
        p = build_portfolio(_model(_dup_graph()))
        assert p.complexity is not None
        assert "raw-orders" in p.logical_datasets
        assert "events" in p.logical_datasets
        assert p.complexity.copies >= 1
        assert p.complexity.cross_cloud_edges >= 0
        assert "events" in p.physical_representations
        assert len(p.physical_representations["events"]) == 2

    def test_owners_environments(self) -> None:
        g = DataPlatformGraph()
        g.add_entity(_e(K.TABLE, "snowflake", "t", owner="analytics", environment="prod"))
        p = build_portfolio(_model(g))
        assert "analytics" in p.teams
        assert "prod" in p.environments

    def test_engines_per_workload_answer(self) -> None:
        p = build_portfolio(_model(_dup_graph()))
        ans = answer_engines_per_workload(p)
        assert ans.get("etl") == ["adf", "airflow"]

    def test_deterministic_ordering(self) -> None:
        p1 = build_portfolio(_model(_dup_graph()))
        p2 = build_portfolio(_model(_dup_graph()))
        assert p1.to_dict() == p2.to_dict()


class TestLifecycle:
    def test_unknown_platform(self) -> None:
        from forge_doctor.core.portfolio import _lifecycle

        status, _ = _lifecycle("nonexistent_platform_xyz", "1.0")
        assert status is LifecycleStatus.UNKNOWN

    def test_lambda_eol_runtime(self) -> None:
        # lambda/runtimes.json ships an eol list — python3.8 is on it
        g = DataPlatformGraph()
        g.add_entity(_e(K.COMPUTE_JOB, "lambda", "f1", runtime="python3.8"))
        p = build_portfolio(_model(g))
        lam = next(t for t in p.platforms if t.platform == "lambda")
        assert lam.lifecycle_status is LifecycleStatus.EOL

    def test_lambda_active_runtime(self) -> None:
        g = DataPlatformGraph()
        g.add_entity(_e(K.COMPUTE_JOB, "lambda", "f1", runtime="python3.12"))
        p = build_portfolio(_model(g))
        lam = next(t for t in p.platforms if t.platform == "lambda")
        assert lam.lifecycle_status is not LifecycleStatus.EOL

    def test_deprecated_answer(self) -> None:
        g = DataPlatformGraph()
        g.add_entity(_e(K.COMPUTE_JOB, "lambda", "f1", runtime="python3.8"))
        p = build_portfolio(_model(g))
        assert any(t.platform == "lambda" for t in answer_deprecated(p))


class TestCrossCloud:
    def test_cross_cloud_edges(self) -> None:
        g = DataPlatformGraph()
        a = g.add_entity(_e(K.STREAM, "kinesis", "s"))
        b = g.add_entity(_e(K.TABLE, "bigquery", "t"))
        g.add_relationship(
            Relationship(src=a.id, dst=b.id, kind=R.PRODUCES, evidence_kind="declared")
        )
        edges = cross_cloud_edges(g)
        assert ("aws", "gcp", f"{a.id}->{b.id}") in edges

    def test_concentration_sorted(self) -> None:
        g = DataPlatformGraph()
        for i in range(3):
            a = g.add_entity(_e(K.STREAM, "kinesis", f"s{i}"))
            b = g.add_entity(_e(K.TABLE, "bigquery", f"t{i}"))
            g.add_relationship(
                Relationship(src=a.id, dst=b.id, kind=R.PRODUCES, evidence_kind="declared")
            )
        conc = answer_cross_cloud_concentration(g)
        assert conc[0] == ("aws", "gcp", 3)

    def test_team_deps(self) -> None:
        g = DataPlatformGraph()
        a = g.add_entity(_e(K.COMPUTE_JOB, "glue", "j", owner="teamA"))
        b = g.add_entity(_e(K.TABLE, "snowflake", "t", owner="teamB"))
        g.add_relationship(
            Relationship(src=a.id, dst=b.id, kind=R.DEPENDS_ON, evidence_kind="declared")
        )
        deps = answer_team_cross_platform_deps(g)
        assert deps == {"teamA": 1, "teamB": 1}


class TestRecurringPatterns:
    def test_shared_check_across_repos(self) -> None:
        pats = recurring_patterns({"a": ["AWS001", "PY001"], "b": ["AWS001"], "c": ["AWS001"]})
        assert len(pats) == 1
        assert pats[0].pattern == "AWS001"
        assert pats[0].count == 3
        assert pats[0].repos == ("a", "b", "c")

    def test_single_repo_no_pattern(self) -> None:
        assert recurring_patterns({"a": ["X1"], "b": ["Y1"]}) == ()


class TestOptimizationEvidence:
    def test_roundtrip(self, tmp_path: Path) -> None:
        ev = ValidatedOptimizationEvidence(
            candidate_family="partitioning",
            subject="fingerprint:abc",
            verdict="supported",
            accepted=True,
            recorded_at="2026-10-03T00:00:00Z",
            project=str(tmp_path),
            details={"metric": "duration_ms", "delta": -0.4},
        )
        record_optimization_evidence(tmp_path, ev)
        loaded = load_optimization_evidence(tmp_path)
        assert len(loaded) == 1
        assert loaded[0].candidate_family == "partitioning"
        assert loaded[0].scope == "project-local"
        assert loaded[0].accepted

    def test_empty(self, tmp_path: Path) -> None:
        assert load_optimization_evidence(tmp_path) == []
