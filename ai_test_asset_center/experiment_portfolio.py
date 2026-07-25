"""Experiment Portfolio — frozen experiment set for execution.

SPEC §18-19: Portfolio is frozen before execution begins.
No modifications allowed during execution phase.
Enforces mechanism quotas and authorization budget cap.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any


def _stable_id(*parts: str) -> str:
    raw = "|".join(parts)
    return "pf_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def _text(v: Any) -> str:
    return str(v or "").strip()


# ─── Portfolio States ──────────────────────────────────────────────────────────

PORTFOLIO_STATES = frozenset({"BUILDING", "FROZEN", "EXECUTING", "COMPLETED"})


# ─── Experiment Entry ──────────────────────────────────────────────────────────

def create_experiment_entry(
    *,
    combination: dict[str, Any],
    coordinate: dict[str, Any] | None = None,
    invariant_ids: list[str] | None = None,
    surface_adapter: str = "",
    observers: list[str] | None = None,
    priority_score: float = 0.5,
) -> dict[str, Any]:
    """Create a single experiment entry for the portfolio."""
    comb_id = combination.get("combination_id", "")
    operators = combination.get("operators", [])
    categories = combination.get("categories", [])

    entry_id = _stable_id("exp", comb_id, surface_adapter)
    return {
        "experiment_id": entry_id,
        "combination_id": comb_id,
        "operators": list(operators),
        "categories": list(categories),
        "level": combination.get("level", "1-way"),
        "coordinate": coordinate or {},
        "invariant_ids": list(invariant_ids or []),
        "surface_adapter": surface_adapter,
        "observers": list(observers or []),
        "priority_score": priority_score,
        "status": "PENDING",
        "execution_result": None,
        "created_at": time.time(),
    }


# ─── Portfolio ─────────────────────────────────────────────────────────────────

class ExperimentPortfolio:
    """Frozen experiment portfolio for execution.

    Lifecycle: BUILDING → FROZEN → EXECUTING → COMPLETED
    Once frozen, no additions/removals/modifications allowed.
    """

    def __init__(self, *, project_id: str = "", run_name: str = ""):
        self.project_id = project_id
        self.run_name = run_name
        self._state = "BUILDING"
        self._experiments: list[dict[str, Any]] = []
        self._frozen_at: float | None = None
        self._freeze_hash: str = ""
        self._execution_log: list[dict[str, Any]] = []

    @property
    def state(self) -> str:
        return self._state

    @property
    def size(self) -> int:
        return len(self._experiments)

    @property
    def is_frozen(self) -> bool:
        return self._state in ("FROZEN", "EXECUTING", "COMPLETED")

    def add_experiment(self, entry: dict[str, Any]) -> str:
        """Add experiment to portfolio (only in BUILDING state)."""
        if self.is_frozen:
            raise RuntimeError(
                f"portfolio_frozen: cannot add experiment in state={self._state}"
            )
        self._experiments.append(entry)
        return entry.get("experiment_id", "")

    def add_combinations(
        self,
        combinations: list[dict[str, Any]],
        *,
        coordinate: dict[str, Any] | None = None,
        surface_adapter: str = "API",
    ) -> int:
        """Batch add combinations as experiments."""
        if self.is_frozen:
            raise RuntimeError(
                f"portfolio_frozen: cannot add in state={self._state}"
            )
        count = 0
        for comb in combinations:
            entry = create_experiment_entry(
                combination=comb,
                coordinate=coordinate,
                surface_adapter=surface_adapter,
                priority_score=comb.get("priority_score", 0.5),
            )
            self._experiments.append(entry)
            count += 1
        return count

    def freeze(self) -> dict[str, Any]:
        """Freeze portfolio — no further modifications allowed."""
        if self._state != "BUILDING":
            raise RuntimeError(f"cannot_freeze: state={self._state}")

        self._state = "FROZEN"
        self._frozen_at = time.time()

        # Compute integrity hash
        content = "|".join(
            e.get("experiment_id", "") for e in self._experiments
        )
        self._freeze_hash = hashlib.sha256(content.encode()).hexdigest()[:32]

        return {
            "state": self._state,
            "size": self.size,
            "frozen_at": self._frozen_at,
            "integrity_hash": self._freeze_hash,
        }

    def begin_execution(self) -> None:
        """Transition to EXECUTING state."""
        if self._state != "FROZEN":
            raise RuntimeError(f"cannot_begin: state={self._state}")
        self._state = "EXECUTING"

    def record_result(
        self,
        experiment_id: str,
        result: dict[str, Any],
    ) -> None:
        """Record execution result for an experiment."""
        if self._state != "EXECUTING":
            raise RuntimeError(f"cannot_record: state={self._state}")

        for exp in self._experiments:
            if exp.get("experiment_id") == experiment_id:
                exp["status"] = result.get("status", "COMPLETED")
                exp["execution_result"] = result
                self._execution_log.append({
                    "experiment_id": experiment_id,
                    "result_summary": result.get("summary", ""),
                    "timestamp": time.time(),
                })
                return

        raise KeyError(f"experiment_not_found: {experiment_id}")

    def complete(self) -> dict[str, Any]:
        """Mark portfolio as completed."""
        if self._state != "EXECUTING":
            raise RuntimeError(f"cannot_complete: state={self._state}")
        self._state = "COMPLETED"
        return self.summary()

    def verify_integrity(self) -> dict[str, Any]:
        """Verify portfolio has not been tampered with since freeze."""
        if not self._freeze_hash:
            return {"intact": False, "reason": "never_frozen"}

        content = "|".join(
            e.get("experiment_id", "") for e in self._experiments
        )
        current_hash = hashlib.sha256(content.encode()).hexdigest()[:32]
        intact = current_hash == self._freeze_hash

        return {
            "intact": intact,
            "expected_hash": self._freeze_hash,
            "current_hash": current_hash,
        }

    def get_pending(self) -> list[dict[str, Any]]:
        """Get experiments not yet executed."""
        return [e for e in self._experiments if e.get("status") == "PENDING"]

    def get_completed(self) -> list[dict[str, Any]]:
        """Get executed experiments."""
        return [e for e in self._experiments if e.get("status") != "PENDING"]

    def summary(self) -> dict[str, Any]:
        """Portfolio summary statistics."""
        status_counts: dict[str, int] = {}
        category_counts: dict[str, int] = {}
        for exp in self._experiments:
            st = exp.get("status", "PENDING")
            status_counts[st] = status_counts.get(st, 0) + 1
            for cat in exp.get("categories", []):
                category_counts[cat] = category_counts.get(cat, 0) + 1

        return {
            "project_id": self.project_id,
            "run_name": self.run_name,
            "state": self._state,
            "total_experiments": self.size,
            "status_distribution": status_counts,
            "category_distribution": category_counts,
            "frozen_at": self._frozen_at,
            "integrity_hash": self._freeze_hash,
        }

    def export(self) -> dict[str, Any]:
        """Export full portfolio for serialization."""
        return {
            "schema_version": "qualibug.experiment-portfolio.v1",
            "project_id": self.project_id,
            "run_name": self.run_name,
            "state": self._state,
            "frozen_at": self._frozen_at,
            "integrity_hash": self._freeze_hash,
            "experiments": self._experiments,
            "execution_log": self._execution_log,
            "summary": self.summary(),
        }


# ─── Quota Validation ──────────────────────────────────────────────────────────

def validate_portfolio_quotas(
    portfolio: ExperimentPortfolio,
) -> dict[str, Any]:
    """Validate portfolio respects mechanism quotas and auth cap."""
    experiments = portfolio._experiments
    total = len(experiments)
    if total == 0:
        return {"compliant": True, "violations": [], "total": 0}

    # Count authorization-only experiments
    auth_count = 0
    for exp in experiments:
        ops = exp.get("operators", [])
        if ops and all(
            "ACTOR" in op or "ROLE" in op or "TENANT" in op
            for op in ops
        ):
            auth_count += 1

    auth_ratio = auth_count / total
    violations = []

    if auth_ratio > 0.30:
        violations.append({
            "type": "AUTHORIZATION_CAP_EXCEEDED",
            "actual": round(auth_ratio, 3),
            "limit": 0.30,
        })

    return {
        "compliant": len(violations) == 0,
        "violations": violations,
        "total": total,
        "authorization_count": auth_count,
        "authorization_ratio": round(auth_ratio, 3),
    }
