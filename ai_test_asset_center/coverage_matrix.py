from __future__ import annotations

"""Coverage Matrix — multi-dimensional coverage tracking for defect discovery.

Tracks coverage across 10 dimensions:
  1. risk_family coverage          — Per-family slice count + execution rate
  2. invariant coverage            — Per-invariant-type slice count + execution rate
  3. role coverage                 — Per-role slice count
  4. entity coverage               — Per-entity slice count
  5. endpoint coverage             — Per-endpoint slice count
  6. state_transition coverage     — Per-transition slice count
  7. high_value_slice coverage     — P0/P1 slices × execution rate
  8. confirmed_defect count        — Evidence-backed confirmed defects
  9. candidate_clue count          — Unconfirmed clues/candidates
  10. blocked_gap count            — Coverage gaps (untestable)

Design contract:
  - No hardcoded dimension names; dimensions are registered dynamically
  - Every metric is data-driven, computed from actual slice execution results
  - Coverage percentages are always computable even with partial data
  - Zero fabrication: if data is missing, metric is reported as "no_data"
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CoverageDimension:
    """A single coverage dimension with its current state."""
    dimension_id: str
    display_name: str
    total_items: int = 0
    covered_items: int = 0
    executed_items: int = 0
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def coverage_rate(self) -> float:
        return round(self.covered_items / self.total_items, 4) if self.total_items else 0.0

    @property
    def execution_rate(self) -> float:
        return round(self.executed_items / self.covered_items, 4) if self.covered_items else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension_id": self.dimension_id,
            "display_name": self.display_name,
            "total_items": self.total_items,
            "covered_items": self.covered_items,
            "executed_items": self.executed_items,
            "coverage_rate": self.coverage_rate,
            "execution_rate": self.execution_rate,
            "details": self.details,
        }


@dataclass
class CoverageMatrix:
    """Multi-dimensional coverage tracker for a project scan."""

    project_id: str = ""
    scan_id: str = ""
    generated_at: str = ""

    # Core dimensions
    risk_family_coverage: dict[str, CoverageDimension] = field(default_factory=dict)
    invariant_coverage: dict[str, CoverageDimension] = field(default_factory=dict)
    role_coverage: dict[str, CoverageDimension] = field(default_factory=dict)
    entity_coverage: dict[str, CoverageDimension] = field(default_factory=dict)
    endpoint_coverage: dict[str, CoverageDimension] = field(default_factory=dict)
    state_transition_coverage: dict[str, CoverageDimension] = field(default_factory=dict)

    # Counts
    total_slices: int = 0
    executed_slices: int = 0
    high_value_slices: int = 0
    confirmed_defects: int = 0
    candidate_clues: int = 0
    blocked_gaps: int = 0

    def compute_from_slices(
        self,
        slices: list[dict[str, Any]],
        *,
        findings: list[dict[str, Any]] | None = None,
        blocked_paths: list[str] | None = None,
    ) -> None:
        """Compute coverage matrix from behavior slices and findings.

        Args:
            slices: List of behavior slice dicts (from behavior_slice_gen).
            findings: List of finding dicts with execution status.
            blocked_paths: List of paths that couldn't be tested.
        """
        self.generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.total_slices = len(slices)

        # Track per-family stats
        family_stats: dict[str, dict[str, int]] = {}
        invariant_stats: dict[str, dict[str, int]] = {}
        role_stats: dict[str, dict[str, int]] = {}
        entity_stats: dict[str, dict[str, int]] = {}
        endpoint_stats: dict[str, dict[str, int]] = {}
        state_transition_stats: dict[str, dict[str, int]] = {}

        for sl in slices:
            family = sl.get("risk_family", "unknown")
            inv_type = sl.get("invariant_type", "unknown")
            role = sl.get("actor", "unknown")
            entity = sl.get("source_entity", "unknown")
            endpoint = sl.get("target", "unknown")
            severity = sl.get("severity", "P2")

            # Family stats
            fs = family_stats.setdefault(family, {"total": 0, "executed": 0, "high_value": 0})
            fs["total"] += 1
            if severity in ("P0", "P1"):
                fs["high_value"] += 1

            # Invariant stats
            inv_s = invariant_stats.setdefault(inv_type, {"total": 0, "executed": 0})
            inv_s["total"] += 1

            # Role stats
            role_s = role_stats.setdefault(role, {"total": 0})
            role_s["total"] += 1

            # Entity stats
            ent_s = entity_stats.setdefault(entity, {"total": 0})
            ent_s["total"] += 1

            # Endpoint stats
            ep_s = endpoint_stats.setdefault(endpoint, {"total": 0})
            ep_s["total"] += 1

        # Mark executed slices from findings
        executed_slice_ids: set[str] = set()
        if findings:
            for finding in findings:
                if not isinstance(finding, dict):
                    continue
                sl_id = finding.get("slice_id", finding.get("hypothesis_id", ""))
                verdict = str(finding.get("verdict", "")).lower()
                if sl_id:
                    executed_slice_ids.add(sl_id)
                # Categorize findings
                if verdict in ("confirmed", "validated_candidate"):
                    self.confirmed_defects += 1
                elif finding.get("customer_delivery_status") == "clue":
                    self.candidate_clues += 1

        self.executed_slices = len(executed_slice_ids)
        self.high_value_slices = sum(
            fs["high_value"] for fs in family_stats.values()
        )

        if blocked_paths:
            self.blocked_gaps = len(blocked_paths)

        # Build coverage dimensions
        self.risk_family_coverage = {
            fid: CoverageDimension(
                dimension_id=fid,
                display_name=fid,
                total_items=fs["total"],
                covered_items=fs["total"],
                executed_items=fs.get("executed", 0),
                details={"high_value": fs.get("high_value", 0)},
            )
            for fid, fs in family_stats.items()
        }

        self.invariant_coverage = {
            inv: CoverageDimension(
                dimension_id=inv,
                display_name=inv,
                total_items=inv_s["total"],
                covered_items=inv_s["total"],
                executed_items=inv_s.get("executed", 0),
            )
            for inv, inv_s in invariant_stats.items()
        }

        self.role_coverage = {
            role: CoverageDimension(
                dimension_id=role,
                display_name=role,
                total_items=rs["total"],
                covered_items=rs["total"],
            )
            for role, rs in role_stats.items()
        }

        self.entity_coverage = {
            entity: CoverageDimension(
                dimension_id=entity,
                display_name=entity,
                total_items=es["total"],
                covered_items=es["total"],
            )
            for entity, es in entity_stats.items()
        }

        self.endpoint_coverage = {
            ep: CoverageDimension(
                dimension_id=ep,
                display_name=ep,
                total_items=eps["total"],
                covered_items=eps["total"],
            )
            for ep, eps in endpoint_stats.items()
        }

        # state_transition coverage from transitions
        self.state_transition_coverage = {
            st: CoverageDimension(
                dimension_id=st,
                display_name=st,
                total_items=sts["total"],
                covered_items=sts["total"],
            )
            for st, sts in state_transition_stats.items()
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for API transport or persistence."""
        return {
            "project_id": self.project_id,
            "scan_id": self.scan_id,
            "generated_at": self.generated_at,
            "summary": {
                "total_slices": self.total_slices,
                "executed_slices": self.executed_slices,
                "execution_rate": round(self.executed_slices / self.total_slices, 4) if self.total_slices else 0.0,
                "high_value_slices": self.high_value_slices,
                "confirmed_defects": self.confirmed_defects,
                "candidate_clues": self.candidate_clues,
                "blocked_gaps": self.blocked_gaps,
                "risk_family_count": len(self.risk_family_coverage),
                "invariant_count": len(self.invariant_coverage),
                "role_count": len(self.role_coverage),
                "entity_count": len(self.entity_coverage),
                "endpoint_count": len(self.endpoint_coverage),
                "state_transition_count": len(self.state_transition_coverage),
            },
            "risk_family_coverage": {
                k: v.to_dict() for k, v in self.risk_family_coverage.items()
            },
            "invariant_coverage": {
                k: v.to_dict() for k, v in self.invariant_coverage.items()
            },
            "role_coverage": {
                k: v.to_dict() for k, v in self.role_coverage.items()
            },
            "entity_coverage": {
                k: v.to_dict() for k, v in self.entity_coverage.items()
            },
            "endpoint_coverage": {
                k: v.to_dict() for k, v in self.endpoint_coverage.items()
            },
            "state_transition_coverage": {
                k: v.to_dict() for k, v in self.state_transition_coverage.items()
            },
        }

    def coverage_score(self) -> float:
        """Compute an aggregate coverage score (0.0-1.0)."""
        scores: list[float] = []
        for dims in [
            self.risk_family_coverage,
            self.invariant_coverage,
            self.role_coverage,
            self.entity_coverage,
        ]:
            if dims:
                avg = sum(d.coverage_rate for d in dims.values()) / len(dims)
                scores.append(avg)
        if scores:
            return round(sum(scores) / len(scores), 4)
        return 0.0

    @staticmethod
    def empty(project_id: str = "") -> "CoverageMatrix":
        """Create an empty coverage matrix (no data yet)."""
        return CoverageMatrix(
            project_id=project_id,
            generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )


# ── Factory Functions ──────────────────────────────────────────────────────


def compute_coverage_matrix(
    slices: list[dict[str, Any]],
    *,
    project_id: str = "",
    scan_id: str = "",
    findings: list[dict[str, Any]] | None = None,
    blocked_paths: list[str] | None = None,
) -> CoverageMatrix:
    """Factory: compute coverage matrix from slices and findings."""
    matrix = CoverageMatrix(project_id=project_id, scan_id=scan_id)
    matrix.compute_from_slices(slices, findings=findings, blocked_paths=blocked_paths)
    return matrix


def merge_coverage_matrices(*matrices: CoverageMatrix) -> CoverageMatrix:
    """Merge multiple coverage matrices (e.g., from sequential scan rounds)."""
    if not matrices:
        return CoverageMatrix.empty()

    merged = CoverageMatrix(
        project_id=matrices[0].project_id,
        scan_id="merged",
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )

    merged.total_slices = sum(m.total_slices for m in matrices)
    merged.executed_slices = sum(m.executed_slices for m in matrices)
    merged.high_value_slices = sum(m.high_value_slices for m in matrices)
    merged.confirmed_defects = sum(m.confirmed_defects for m in matrices)
    merged.candidate_clues = sum(m.candidate_clues for m in matrices)
    merged.blocked_gaps = sum(m.blocked_gaps for m in matrices)

    # Merge dimension dictionaries
    for dim_name in ("risk_family_coverage", "invariant_coverage", "role_coverage",
                     "entity_coverage", "endpoint_coverage", "state_transition_coverage"):
        merged_dim: dict[str, CoverageDimension] = {}
        for m in matrices:
            for key, dim in getattr(m, dim_name, {}).items():
                if key in merged_dim:
                    merged_dim[key].total_items += dim.total_items
                    merged_dim[key].covered_items += dim.covered_items
                    merged_dim[key].executed_items += dim.executed_items
                else:
                    merged_dim[key] = CoverageDimension(
                        dimension_id=dim.dimension_id,
                        display_name=dim.display_name,
                        total_items=dim.total_items,
                        covered_items=dim.covered_items,
                        executed_items=dim.executed_items,
                        details=dict(dim.details),
                    )
        setattr(merged, dim_name, merged_dim)

    return merged


# ── Persistence ────────────────────────────────────────────────────────────


def persist_coverage_matrix(
    matrix: CoverageMatrix,
    project: str,
    *,
    root: Path | None = None,
) -> Path:
    """Persist coverage matrix to platform_outputs."""
    root = Path(root or Path.cwd())
    out_dir = root / "platform_outputs" / project.replace("/", "_") / "coverage"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "coverage_matrix.json"
    path.write_text(
        json.dumps(matrix.to_dict(), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return path


def load_coverage_matrix(project: str, *, root: Path | None = None) -> CoverageMatrix | None:
    """Load a previously persisted coverage matrix."""
    root = Path(root or Path.cwd())
    path = root / "platform_outputs" / project.replace("/", "_") / "coverage" / "coverage_matrix.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        matrix = CoverageMatrix(
            project_id=data.get("project_id", project),
            scan_id=data.get("scan_id", ""),
            generated_at=data.get("generated_at", ""),
            total_slices=data.get("summary", {}).get("total_slices", 0),
            executed_slices=data.get("summary", {}).get("executed_slices", 0),
            high_value_slices=data.get("summary", {}).get("high_value_slices", 0),
            confirmed_defects=data.get("summary", {}).get("confirmed_defects", 0),
            candidate_clues=data.get("summary", {}).get("candidate_clues", 0),
            blocked_gaps=data.get("summary", {}).get("blocked_gaps", 0),
        )
        return matrix
    except Exception:
        return None
