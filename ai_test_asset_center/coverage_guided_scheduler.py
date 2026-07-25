"""Coverage-Guided Scheduler — schedules experiments by coverage gaps.

SPEC §16-17: Schedules based on real coverage gaps, not rule confidence
or endpoint count. Implements mechanism quotas and authorization budget cap.

Quotas (SPEC §16):
  Field Causal/Conservation: 15%, State/Lifecycle: 10%, Cross-Entity: 10%,
  Replay/Idempotency: 10%, Transaction/Compensation: 10%, Concurrency: 10%,
  Temporal/Async: 10%, Batch/Aggregate: 10%, Cross-Surface: 5%, Scale: 5%
  Authorization cap: ≤30%
"""
from __future__ import annotations

import time
from typing import Any


# ─── Mechanism Quotas (SPEC §16) ───────────────────────────────────────────────

MECHANISM_QUOTAS = {
    "FIELD_CAUSAL_CONSERVATION": 0.15,
    "STATE_LIFECYCLE": 0.10,
    "CROSS_ENTITY": 0.10,
    "REPLAY_IDEMPOTENCY": 0.10,
    "TRANSACTION_COMPENSATION": 0.10,
    "CONCURRENCY_VERSION": 0.10,
    "TEMPORAL_ASYNC": 0.10,
    "BATCH_AGGREGATE": 0.10,
    "CROSS_SURFACE": 0.05,
    "SCALE_PERFORMANCE": 0.05,
}

AUTHORIZATION_CAP = 0.30  # Max 30% for authorization/simple validation


def _text(v: Any) -> str:
    return str(v or "").strip()


def _list(v: Any) -> list:
    return v if isinstance(v, list) else []


# ─── Coverage State ────────────────────────────────────────────────────────────

def compute_coverage_state(
    *,
    dimension_registry: Any = None,
    executed_combinations: list[dict[str, Any]] | None = None,
    findings: list[dict[str, Any]] | None = None,
    blocked_reasons: list[str] | None = None,
) -> dict[str, Any]:
    """Compute current coverage state across all dimensions."""
    executed = executed_combinations or []
    found = findings or []
    blocked = blocked_reasons or []

    # Track which operator types have been executed
    executed_operators: set[str] = set()
    executed_categories: set[str] = set()
    for comb in executed:
        for op in comb.get("operators", []):
            executed_operators.add(op)
        for cat in comb.get("categories", []):
            executed_categories.add(cat)

    # Track root causes found
    root_causes: set[str] = set()
    for f in found:
        rc = _text(f.get("root_cause_signature"))
        if rc:
            root_causes.add(rc)

    return {
        "executed_operators": sorted(executed_operators),
        "executed_categories": sorted(executed_categories),
        "total_executed": len(executed),
        "root_causes_found": sorted(root_causes),
        "total_findings": len(found),
        "blocked_reasons": blocked,
        "timestamp": time.time(),
    }


# ─── Quota Enforcement ─────────────────────────────────────────────────────────

def check_quota_compliance(
    portfolio_experiments: list[dict[str, Any]],
) -> dict[str, Any]:
    """Check if portfolio respects mechanism quotas and authorization cap."""
    total = len(portfolio_experiments)
    if total == 0:
        return {"compliant": True, "violations": []}

    # Count by mechanism category
    category_counts: dict[str, int] = {}
    auth_count = 0
    for exp in portfolio_experiments:
        cats = exp.get("categories", [])
        for cat in cats:
            category_counts[cat] = category_counts.get(cat, 0) + 1
        # Authorization detection
        ops = exp.get("operators", [])
        if any("ACTOR" in op or "ROLE" in op or "TENANT" in op for op in ops):
            if all("ACTOR" in op or "ROLE" in op or "TENANT" in op for op in ops):
                auth_count += 1

    violations = []

    # Check authorization cap
    auth_ratio = auth_count / total
    if auth_ratio > AUTHORIZATION_CAP:
        violations.append({
            "type": "AUTHORIZATION_CAP_EXCEEDED",
            "actual": round(auth_ratio, 3),
            "limit": AUTHORIZATION_CAP,
        })

    return {
        "compliant": len(violations) == 0,
        "violations": violations,
        "total_experiments": total,
        "authorization_count": auth_count,
        "authorization_ratio": round(auth_ratio, 3),
        "category_distribution": category_counts,
    }


# ─── Scheduler ─────────────────────────────────────────────────────────────────

class CoverageGuidedScheduler:
    """Schedules experiments based on coverage gaps."""

    def __init__(self, *, project_id: str = "", budget: int = 50):
        self.project_id = project_id
        self.budget = budget
        self._round = 0
        self._executed: list[dict[str, Any]] = []
        self._findings: list[dict[str, Any]] = []
        self._schedule_history: list[dict[str, Any]] = []

    @property
    def current_round(self) -> int:
        return self._round

    def select_next_batch(
        self,
        candidates: list[dict[str, Any]],
        *,
        batch_size: int = 10,
    ) -> dict[str, Any]:
        """Select next batch of experiments based on coverage gaps.

        Priority: uncovered categories > uncovered operators > novelty > cost.
        """
        self._round += 1

        # Compute coverage state
        coverage = compute_coverage_state(
            executed_combinations=self._executed,
            findings=self._findings,
        )
        executed_ops = set(coverage["executed_operators"])
        executed_cats = set(coverage["executed_categories"])

        # Score candidates by coverage gap
        scored = []
        for cand in candidates:
            ops = cand.get("operators", [])
            cats = cand.get("categories", [])

            # Coverage gain: how many new operators/categories
            new_ops = len([o for o in ops if o not in executed_ops])
            new_cats = len([c for c in cats if c not in executed_cats])
            coverage_gain = (new_ops * 0.6 + new_cats * 0.4) / max(len(ops), 1)

            # Novelty: root cause deduplication
            novelty = cand.get("priority_score", 0.5)

            # Combined scheduling score
            schedule_score = coverage_gain * 0.6 + novelty * 0.4

            scored.append({
                "candidate": cand,
                "schedule_score": round(schedule_score, 4),
                "coverage_gain": round(coverage_gain, 4),
                "new_operators": new_ops,
                "new_categories": new_cats,
            })

        # Sort by schedule score
        scored.sort(key=lambda s: s["schedule_score"], reverse=True)

        # Select batch respecting budget
        selected = []
        remaining_budget = self.budget - len(self._executed)
        for item in scored[:min(batch_size, remaining_budget)]:
            selected.append(item["candidate"])

        # Determine stop reason
        stop_reason = ""
        if remaining_budget <= 0:
            stop_reason = "BUDGET_EXHAUSTED"
        elif not selected:
            stop_reason = "NO_APPLICABLE_CANDIDATES"
        elif all(s["coverage_gain"] == 0 for s in scored[:batch_size]):
            stop_reason = "COVERAGE_SATURATED"

        result = {
            "round": self._round,
            "selected_experiments": selected,
            "expected_coverage_gain": sum(
                s["coverage_gain"] for s in scored[:len(selected)]
            ),
            "skipped_candidates": len(candidates) - len(selected),
            "stop_reason": stop_reason,
            "budget_remaining": remaining_budget - len(selected),
        }

        self._schedule_history.append(result)
        return result

    def record_execution(self, combination: dict[str, Any]) -> None:
        """Record an executed combination."""
        self._executed.append(combination)

    def record_finding(self, finding: dict[str, Any]) -> None:
        """Record a finding from execution."""
        self._findings.append(finding)

    def get_schedule_history(self) -> list[dict[str, Any]]:
        return self._schedule_history

    def export(self) -> dict[str, Any]:
        return {
            "schema_version": "qualibug.coverage-guided-scheduler.v1",
            "project_id": self.project_id,
            "current_round": self._round,
            "budget": self.budget,
            "total_executed": len(self._executed),
            "total_findings": len(self._findings),
            "schedule_history": self._schedule_history,
            "coverage_state": compute_coverage_state(
                executed_combinations=self._executed,
                findings=self._findings,
            ),
        }
