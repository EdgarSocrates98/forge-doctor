"""Forge Lab metrics - precision/recall/FP/FN and coverage roll-ups.

Turns :class:`~forge_doctor_data.core.lab.LabReport` comparisons into objective
quality numbers per domain and overall.  Semantics:

- **TP / missed** come from ``expected_findings`` matching.
- **FP candidates** are detected findings at WARNING+ severity that were
  not declared expected (INFO anchors describe surface, never FPs).
- **Forbidden hits** count separately — they are confirmed FPs.
- **FPR** = forbidden hits / forbidden declarations (the ground truth's
  declared negatives).
- **parser coverage** = .py modules with a parsed AST / total .py modules.
- **graph edge recall** = matched expected edges / expected edges.
- **capability accuracy** = correct evaluations / declared expectations.
- **root-cause recall** = matched cluster prefixes / expected chains.

All rates are ``None`` when the denominator is zero (nothing to measure).
"""

from __future__ import annotations

from dataclasses import dataclass

from forge_doctor_data.core.lab import LabReport, ScenarioReport


@dataclass
class MetricRow:
    """One row of the metrics table (a domain, a scenario, or TOTAL)."""

    name: str
    scenarios: int = 0
    expected_findings: int = 0
    detected_correct: int = 0
    missed: int = 0
    fp_candidates: int = 0
    forbidden_hits: int = 0
    forbidden_declared: int = 0
    py_files: int = 0
    py_parsed: int = 0
    expected_edges: int = 0
    edges_hit: int = 0
    expected_caps: int = 0
    caps_hit: int = 0
    expected_causes: int = 0
    causes_hit: int = 0

    @property
    def precision(self) -> float | None:
        denom = self.detected_correct + self.fp_candidates
        return self.detected_correct / denom if denom else None

    @property
    def recall(self) -> float | None:
        denom = self.detected_correct + self.missed
        return self.detected_correct / denom if denom else None

    @property
    def fpr(self) -> float | None:
        if not self.forbidden_declared:
            return None
        return self.forbidden_hits / self.forbidden_declared

    @property
    def parser_coverage(self) -> float | None:
        return self.py_parsed / self.py_files if self.py_files else None

    @property
    def graph_recall(self) -> float | None:
        return self.edges_hit / self.expected_edges if self.expected_edges else None

    @property
    def capability_accuracy(self) -> float | None:
        return self.caps_hit / self.expected_caps if self.expected_caps else None

    @property
    def root_cause_recall(self) -> float | None:
        return self.causes_hit / self.expected_causes if self.expected_causes else None


def _accumulate(row: MetricRow, report: ScenarioReport) -> None:
    row.scenarios += 1
    f = report.findings
    row.expected_findings += len(f.expected)
    row.detected_correct += len(f.expected) - len(f.missed)
    row.missed += len(f.missed)
    row.fp_candidates += report.stats.get("fp_candidates", 0)
    row.forbidden_hits += len(f.forbidden_hit)
    g = report.graph_edges
    row.expected_edges += len(g.expected)
    row.edges_hit += len(g.expected) - len(g.missed)
    c = report.capabilities
    row.expected_caps += len(c.expected)
    row.caps_hit += len(c.expected) - len(c.missed)
    rc = report.root_causes
    row.expected_causes += len(rc.expected)
    row.causes_hit += len(rc.expected) - len(rc.missed)
    row.py_files += report.stats.get("py_files", 0)
    row.py_parsed += report.stats.get("py_parsed", 0)


def compute_metrics(lab: LabReport, forbidden_declared: dict[str, int]) -> list[MetricRow]:
    """Per-domain roll-up plus a TOTAL row.

    ``forbidden_declared`` maps scenario-dir name → declared forbidden count
    (the report keeps only hits; denominators need the declarations).
    """
    rows: dict[str, MetricRow] = {}
    total = MetricRow(name="TOTAL")
    for rep in lab.reports:
        domain = rep.path.parent.name or "_root"
        row = rows.setdefault(domain, MetricRow(name=domain))
        _accumulate(row, rep)
        _accumulate(total, rep)
        declared = forbidden_declared.get(rep.path.name, 0)
        row.forbidden_declared += declared
        total.forbidden_declared += declared
    ordered = sorted(rows.values(), key=lambda r: r.name)
    ordered.append(total)
    return ordered


def forbidden_declarations(lab: LabReport) -> dict[str, int]:
    """scenario name → declared forbidden_findings count."""
    from forge_doctor_data.core.lab import load_ground_truth

    out: dict[str, int] = {}
    for rep in lab.reports:
        truth = load_ground_truth(rep.path / "expected.json")
        out[rep.path.name] = len(truth.forbidden_findings)
    return out
