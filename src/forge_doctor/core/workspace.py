"""WorkspaceModel: multi-repository platform intelligence.

Discovers sibling sub-projects under a workspace root, builds each
repository's ``DataPlatformGraph``, and merges them into a single
``WorkspaceModel`` whose graph adds ``repo:workspace:<name>`` entities and
cross-repository edges:

- ``repo:A --DEFINES--> E``     — A declares entity E in IaC/config
- ``repo:B --IMPLEMENTS--> E``  — B contains the code that implements E
- ``repo:C --INVOKES--> E``     — C's workflows invoke E (defined elsewhere)

Entity identifiers are canonical, so the same
``compute_job:glue:orders-etl`` declared in a terraform repo, implemented
in a glue-jobs repo, and invoked by an airflow-dags repo merges into one
node joined by three repo edges.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from forge_doctor.core.models import EvidenceKind
from forge_doctor.core.platform_graph import (
    DataPlatformGraph,
    Entity,
    EntityKind,
    Relationship,
    RelKind,
)

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext

_REPO_MARKERS = {
    "pyproject.toml",
    "databricks.yml",
    "airflow.cfg",
    "setup.py",
    "package.json",
    ".git",
}
_MARKER_SUFFIXES = (".tf", ".sql", ".tfvars")
_LANG_BY_SUFFIX = {
    ".py": "python",
    ".tf": "terraform",
    ".tfvars": "terraform",
    ".sql": "sql",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".json": "json",
    ".scala": "scala",
    ".ipynb": "notebook",
    ".sh": "shell",
}
_AWGLUE_HINTS = ("awsglue", "GlueContext", "pyspark.context", "Job(", "getResolvedOptions")
_NAME_RE = re.compile(r"[^a-z0-9]+")


def _norm_name(value: str) -> str:
    return _NAME_RE.sub("-", value.lower()).strip("-")


@dataclass(frozen=True)
class WorkspaceRepo:
    """One discovered sub-project in the workspace."""

    name: str
    path: str  # relative to workspace root, posix; "." for the root itself
    markers: tuple[str, ...]
    languages: tuple[str, ...]


@dataclass(frozen=True)
class CrossRepoLink:
    """A semantic link between a repository and a shared entity."""

    kind: RelKind
    source_repo: str
    target_repo: str  # "" when the entity is not defined in the workspace
    entity_id: str


@dataclass
class WorkspaceModel:
    """Merged platform model across all discovered repositories."""

    root: Path
    repositories: tuple[WorkspaceRepo, ...]
    graph: DataPlatformGraph
    links: tuple[CrossRepoLink, ...] = field(default_factory=tuple)

    @property
    def languages(self) -> tuple[str, ...]:
        seen: set[str] = set()
        for repo in self.repositories:
            seen.update(repo.languages)
        return tuple(sorted(seen))

    def repo_of(self, entity_id: str) -> str | None:
        """Repository that DEFINES the entity, if any."""
        for link in self.links:
            if link.kind is RelKind.DEFINES and link.entity_id == entity_id:
                return link.source_repo
        return None

    def summary(self) -> dict[str, int | str]:
        return {
            "root": str(self.root),
            "repositories": len(self.repositories),
            "entities": len(self.graph.entities()),
            "relationships": len(self.graph.relationships()),
            "cross_repo_links": len(self.links),
        }


def _is_marker(f: Path) -> bool:
    return f.name in _REPO_MARKERS or f.suffix in _MARKER_SUFFIXES


def discover_repos(root: Path, ctx: ProjectContext) -> tuple[WorkspaceRepo, ...]:
    """Group workspace files into sub-projects by nearest marker ancestor.

    A directory becomes a repository when it directly contains a marker
    file (``pyproject.toml``, ``*.tf``, ``databricks.yml``, ``.git``,
    ``dags/``...). The *outermost* marker directory wins, so nested
    ``terraform/modules/x`` stays inside the terraform repo. When no
    markers exist, the root itself is a single repository.
    """
    marker_dirs: dict[str, set[str]] = {}
    for f in ctx.files:
        posix = f.as_posix()
        parts = PurePosixPath(posix).parts[:-1]
        # "dags/" directory content is itself an airflow marker; the repo
        # is the directory *containing* dags/.
        if "dags" in parts:
            idx = parts.index("dags")
            owner = "/".join(parts[:idx]) or "."
            marker_dirs.setdefault(owner, set()).add("dags/")
        if not _is_marker(f):
            continue
        parent = str(PurePosixPath(posix).parent)
        marker_dirs.setdefault(parent, set()).add(f.name)

    outermost: dict[str, set[str]] = {}
    for d in sorted(marker_dirs, key=lambda p: (p.count("/"), p)):
        inside = any(o != d and d.startswith(o + "/") for o in outermost)
        if not inside:
            outermost[d] = marker_dirs[d]

    def _languages(prefix: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    _LANG_BY_SUFFIX[f.suffix]
                    for f in ctx.files
                    if f.suffix in _LANG_BY_SUFFIX and f.as_posix().startswith(prefix)
                }
            )
        )

    repos: list[WorkspaceRepo] = []
    for d, marks in outermost.items():
        rel = "." if d == "." else d
        prefix = "" if rel == "." else rel + "/"
        name = root.name if rel == "." else PurePosixPath(rel).name
        repos.append(
            WorkspaceRepo(
                name=name,
                path=rel,
                markers=tuple(sorted(marks)),
                languages=_languages(prefix),
            )
        )
    if not repos:
        repos.append(
            WorkspaceRepo(
                name=root.name,
                path=".",
                markers=(),
                languages=_languages(""),
            )
        )
    return tuple(sorted(repos, key=lambda r: r.path))


def _repo_id(name: str) -> str:
    return f"{EntityKind.REPO.value}:workspace:{name}"


def _repo_entity(repo: WorkspaceRepo) -> Entity:
    return Entity(
        kind=EntityKind.REPO,
        domain="workspace",
        identifier=repo.name,
        name=repo.name,
        file=Path(repo.path),
        attrs=(("path", repo.path),),
    )


def _glue_impl_names(ctx: ProjectContext) -> set[str]:
    """Normalized glue-job implementation names: py files using awsglue."""
    names: set[str] = set()
    for f in ctx.files:
        if f.suffix != ".py":
            continue
        text = ctx.read_text(f) or ""
        if any(h in text for h in _AWGLUE_HINTS):
            names.add(_norm_name(f.stem))
    return names


def build_workspace_model(root: Path, ctx: ProjectContext) -> WorkspaceModel:
    """Discover repos, build+merge per-repo graphs, derive cross-repo links."""
    return merge_repos(root, discover_repos(root, ctx))


def merge_repos(root: Path, repos: tuple[WorkspaceRepo, ...]) -> WorkspaceModel:
    """Shared merge: per-repo platform graphs plus ``repo:*`` entities and
    DEFINES/IMPLEMENTS/INVOKES cross-repo links. Used by both workspace
    discovery (marker-based) and fleet manifests (explicit paths)."""
    from forge_doctor.analyzers.platform_graph_builder import build_platform_graph
    from forge_doctor.core.context import ProjectContext

    merged = DataPlatformGraph()
    links: list[CrossRepoLink] = []
    impl_by_name: dict[str, str] = {}  # normalized job name -> repo name
    defines_target_repo: dict[str, str] = {}  # entity id -> repo that defines it
    invokes_seen: set[tuple[str, str]] = set()
    sub_graphs: list[tuple[WorkspaceRepo, DataPlatformGraph]] = []

    for repo in repos:
        merged.add_entity(_repo_entity(repo))
        repo_dir = root if repo.path == "." else root / repo.path
        sub = ProjectContext(root=repo_dir)
        g = build_platform_graph(sub)
        sub_graphs.append((repo, g))
        for ent in g.entities():
            merged.add_entity(ent)
        for stem in _glue_impl_names(sub):
            impl_by_name.setdefault(stem, repo.name)

    # pass 2: merge edges + repo-level DEFINES after every entity exists
    for repo, g in sub_graphs:
        for rel in g.relationships():
            merged.add_relationship(rel)
            if rel.kind is RelKind.DEFINES:
                dst_ent = merged.entity(rel.dst)
                if dst_ent is not None:
                    merged.add_relationship(
                        Relationship(
                            src=_repo_id(repo.name),
                            dst=rel.dst,
                            kind=RelKind.DEFINES,
                            evidence_kind=EvidenceKind.DERIVED,
                            attrs=(("repo", repo.name),),
                        )
                    )
                    defines_target_repo.setdefault(rel.dst, repo.name)
            elif rel.kind is RelKind.INVOKES:
                invokes_seen.add((repo.name, rel.dst))

    # IMPLEMENTS: glue code file whose normalized stem equals a defined job name.
    for dst, def_repo in sorted(defines_target_repo.items()):
        m = re.match(r"compute_job:glue:(.+)$", dst)
        if not m:
            continue
        impl_repo = impl_by_name.get(_norm_name(m.group(1)))
        if impl_repo and impl_repo != def_repo:
            merged.add_relationship(
                Relationship(
                    src=_repo_id(impl_repo),
                    dst=dst,
                    kind=RelKind.IMPLEMENTS,
                    evidence_kind=EvidenceKind.DERIVED,
                    attrs=(("evidence", "glue source file matches job name"),),
                )
            )
            links.append(CrossRepoLink(RelKind.IMPLEMENTS, impl_repo, def_repo, dst))

    # repo-level DEFINES links.
    for dst, repo_name in sorted(defines_target_repo.items()):
        links.append(CrossRepoLink(RelKind.DEFINES, repo_name, repo_name, dst))

    # INVOKES: caller repo invokes a platform entity. Internal ``task:*``
    # targets are orchestration plumbing inside the caller, not links.
    for caller, dst in sorted(invokes_seen):
        if dst.startswith(f"{EntityKind.TASK.value}:"):
            continue
        def_repo = defines_target_repo.get(dst, "")
        if def_repo == caller:
            continue
        merged.add_relationship(
            Relationship(
                src=_repo_id(caller),
                dst=dst,
                kind=RelKind.INVOKES,
                evidence_kind=EvidenceKind.DERIVED,
                attrs=(("evidence", "repo workflow invokes entity"),),
            )
        )
        links.append(CrossRepoLink(RelKind.INVOKES, caller, def_repo, dst))

    return WorkspaceModel(
        root=root,
        repositories=repos,
        graph=merged,
        links=tuple(links),
    )
