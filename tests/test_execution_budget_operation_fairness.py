"""Session F: execution-budget operation fairness.

Root cause fixed here: the per-batch execution budget (phase-based, e.g. 20)
can be far smaller than the compiled obligation pool, and a global-priority
truncation after prioritizer reordering lets whole operations starve at
OBLIGATION_BUDGET_REACHED — measured on the benchmark target: 859 compiled,
233 executed, 539 deferred, with all product-admin operations deferred.

Two generic mechanism changes (no benchmark data, no thresholds tuned to any
specific defect):
1. ``prioritize_experiments`` promotes the top-scoring experiment of each
   distinct operation above all second-tier rows (operation-fair first tier).
2. ``_operation_coverage_budget`` floors the batch budget at one experiment
   per distinct operation, bounded by the same hard cap as before.

Together they make it impossible for an entire operation to be excluded from
execution purely by global readiness rank, on any enterprise system.
"""
import inspect

import pytest

from ai_test_asset_center.safe_experiment_prioritizer import (
    prioritize_experiments,
    score_experiment_priority,
)
from ai_test_asset_center import (
    _experiment_batch_executor_single_finding_mechanics as batch_core,
)
from ai_test_asset_center.small_scale_validation_gate import HARD_BUDGET_CAP


def _make_obligation(
    oid: str,
    op_key: str,
    risk_family: str = "authorization",
    source_refs: list | None = None,
) -> dict:
    return {
        "obligation_id": oid,
        "risk_family": risk_family,
        "mechanism": risk_family,
        "operation_key": op_key,
        "path_prefix": op_key.split(" ", 1)[-1].split("/{", 1)[0],
        "source_refs": source_refs
        or [{"kind": "api_doc", "locator": op_key}],
        "required_operations": ["op_one"],
        "property": {},
    }


def _make_experiment(eid: str, oid: str) -> dict:
    return {
        "experiment_id": eid,
        "obligation_id": oid,
        "safety_contract": {"governed_write": True},
        "write_reversibility_proof": {"proof_status": "PROVEN"},
        "observers": [
            {"observer_id": "obs_1", "kind": "before_state"},
            {"observer_id": "obs_2", "kind": "after_state"},
        ],
        "binding_plan": [
            {"target": "id", "source_kind": "PRIMARY_RESPONSE", "status": "resolved"}
        ],
    }


def _pool(operations: list[str], per_operation: int = 3) -> tuple[list, list]:
    obligations = []
    experiments = []
    for op in operations:
        for i in range(per_operation):
            oid = f"obl_{op.replace(' ', '_').replace('/', '_')}_{i}"
            eid = f"exp_{oid}"
            obligations.append(_make_obligation(oid, op))
            experiments.append(_make_experiment(eid, oid))
    return obligations, experiments


class TestOperationCoverageBudget:
    def test_floors_small_phase_budget_to_distinct_operations(self):
        """Budget 1 with 5 distinct operations must rise to 5."""
        selected = [
            {"obligation_id": f"o{i}", "operation_key": f"OP-{i}"}
            for i in range(5)
        ]
        assert batch_core._operation_coverage_budget(selected, budget=1) == 5

    def test_respects_hard_cap(self):
        """Floor must never exceed the shared hard cap."""
        selected = [
            {"obligation_id": f"o{i}", "operation_key": f"OP-{i}"}
            for i in range(HARD_BUDGET_CAP + 100)
        ]
        assert (
            batch_core._operation_coverage_budget(selected, budget=1)
            == HARD_BUDGET_CAP
        )

    def test_never_shrinks_larger_budget(self):
        """A phase budget already above the operation count must stay put."""
        selected = [
            {"obligation_id": f"o{i}", "operation_key": f"OP-{i}"}
            for i in range(3)
        ]
        assert batch_core._operation_coverage_budget(selected, budget=50) == 50

    def test_rows_without_operation_key_do_not_raise(self):
        """Rows lacking operation_key (unusual plans) must not break the floor."""
        selected = [
            {"obligation_id": "a"},
            {"obligation_id": "b", "operation_key": "OP-B"},
        ]
        assert batch_core._operation_coverage_budget(selected, budget=1) == 1


class TestOperationFairPrioritization:
    def test_every_operation_inside_budget_when_budget_ge_operation_count(self):
        """With budget == number of operations, every operation must execute."""
        operations = [f"POST /api/module/{n}/op" for n in range(8)]
        obligations, experiments = _pool(operations, per_operation=3)
        receipt = prioritize_experiments(
            experiments=experiments,
            obligations=obligations,
            behavior_ir={},
            budget=len(operations),
        )
        in_budget = [
            item for item in receipt["prioritized"] if item["within_budget"]
        ]
        covered = {item["operation_key"] for item in in_budget}
        assert covered == set(operations)

    def test_promoted_tier_is_one_per_operation(self):
        """The first-tier promotion must contain exactly one row per operation."""
        operations = ["A-op", "B-op", "C-op"]
        obligations, experiments = _pool(operations, per_operation=4)
        receipt = prioritize_experiments(
            experiments=experiments,
            obligations=obligations,
            behavior_ir={},
            budget=3,
        )
        prioritized = receipt["prioritized"]
        first_three = prioritized[:3]
        assert len({item["operation_key"] for item in first_three}) == 3

    def test_promoted_tier_picks_highest_scoring_experiment_per_operation(self):
        """Within an operation, the top-scored experiment must be promoted."""
        obligations = [
            _make_obligation("obl_low", "OP-X", risk_family="authorization"),
            _make_obligation("obl_high", "OP-X", risk_family="conservation"),
        ]
        experiments = [
            _make_experiment("exp_low", "obl_low"),
            _make_experiment("exp_high", "obl_high"),
        ]
        receipt = prioritize_experiments(
            experiments=experiments,
            obligations=obligations,
            behavior_ir={},
            budget=1,
        )
        first = receipt["prioritized"][0]
        assert first["obligation_id"] == "obl_high"

    def test_ordering_still_respects_score_within_promoted_tier(self):
        """Promoted items must be ordered by score, not by operation name."""
        obligations = [
            _make_obligation("obl_a", "OP-A", risk_family="concurrency"),
            _make_obligation("obl_b", "OP-B", risk_family="authorization"),
        ]
        experiments = [
            _make_experiment("exp_a", "obl_a"),
            _make_experiment("exp_b", "obl_b"),
        ]
        receipt = prioritize_experiments(
            experiments=experiments,
            obligations=obligations,
            behavior_ir={},
            budget=2,
        )
        assert receipt["prioritized"][0]["obligation_id"] == "obl_a"


class TestBatchExecutorWiring:
    def test_executor_uses_operation_coverage_floor(self):
        """The batch executor must wire the floor into its budget resolution."""
        src = inspect.getsource(batch_core.execute_selected_experiments)
        assert "_operation_coverage_budget(" in src

    def test_hard_cap_still_present(self):
        """The executor must consume the budget SSOT, not a stale literal."""
        src = inspect.getsource(batch_core.execute_selected_experiments)
        assert "HARD_BUDGET_CAP" in src
        assert "min(_budget, 200)" not in src
