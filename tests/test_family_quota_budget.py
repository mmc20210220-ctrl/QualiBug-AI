# -*- coding: utf-8 -*-
"""Family-fair execution budget (distribution balance, task B).

Locks in: on top of operation-fair, every risk family present in the pool
keeps a minimum execution quota (default 1, configurable) — a large
authorization base can no longer push state/idempotency/conservation/
validation/privacy obligations out of the per-batch budget. The family set
comes from the obligation rows themselves (open family registry), never
hardcoded. Synthetic pools only — no benchmark material, no GT.
"""
import inspect

from ai_test_asset_center.safe_experiment_prioritizer import (
    prioritize_experiments,
)
from ai_test_asset_center import (
    _experiment_batch_executor_single_finding_mechanics as batch_core,
)


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


def _pool(
    families: list[str],
    per_family: int = 3,
    operations_per_family: int = 1,
) -> tuple[list, list]:
    obligations = []
    experiments = []
    for family_index, family in enumerate(families):
        for i in range(per_family):
            oid = f"obl_{family}_{i}"
            op_key = f"POST /api/module/{family_index}/op"
            obligations.append(_make_obligation(oid, op_key, risk_family=family))
            experiments.append(_make_experiment(f"exp_{oid}", oid))
    return obligations, experiments


class TestFamilyFairPrioritization:
    def test_every_family_inside_budget_when_budget_ge_family_count(self):
        """Budget == #families must cover every family (quota 1)."""
        families = ["authorization", "state", "idempotency", "conservation",
                    "validation", "privacy"]
        obligations, experiments = _pool(families, per_family=5)
        receipt = prioritize_experiments(
            experiments=experiments,
            obligations=obligations,
            behavior_ir={},
            budget=len(families),
        )
        covered = set(receipt["family_coverage"])
        assert covered == set(families)
        assert all(
            receipt["family_coverage"][family] >= 1 for family in families
        )

    def test_authorization_cannot_crowd_out_small_families(self):
        """Even with 100 authorization rows, one state row stays in budget."""
        obligations, experiments = _pool(
            ["authorization"], per_family=100, operations_per_family=10
        )
        obligations += [_make_obligation("obl_state_0", "POST /api/state/op",
                                         risk_family="state")]
        experiments.append(_make_experiment("exp_state_0", "obl_state_0"))
        receipt = prioritize_experiments(
            experiments=experiments,
            obligations=obligations,
            behavior_ir={},
            budget=2,
        )
        assert receipt["family_coverage"].get("state", 0) >= 1

    def test_family_quota_two(self):
        """With quota 2, each family keeps 2 rows inside a fitting budget."""
        families = ["authorization", "state"]
        obligations, experiments = _pool(families, per_family=5)
        receipt = prioritize_experiments(
            experiments=experiments,
            obligations=obligations,
            behavior_ir={},
            budget=4,
            family_quota=2,
        )
        assert receipt["family_quota"] == 2
        assert receipt["family_coverage"]["authorization"] == 2
        assert receipt["family_coverage"]["state"] == 2

    def test_family_tier_does_not_break_operation_fairness(self):
        """Union-bound budget (#ops + #families) covers ops AND families.

        The state family's top row lands on operation 0 (already covered by
        the authorization family row), so the two tiers need
        #ops + #families = 7 rows before every operation and every family is
        inside the budget — exactly what the executor floor guarantees.
        """
        operations = [f"POST /api/module/{n}/op" for n in range(6)]
        obligations, experiments = [], []
        for op_index, op_key in enumerate(operations):
            for i in range(2):
                oid = f"obl_auth_op{op_index}_{i}"
                obligations.append(_make_obligation(oid, op_key))
                experiments.append(_make_experiment(f"exp_{oid}", oid))
        # Give one operation a state-family obligation as well.
        obligations.append(_make_obligation(
            "obl_state_x", operations[0], risk_family="state"))
        experiments.append(_make_experiment("exp_state_x", "obl_state_x"))
        receipt = prioritize_experiments(
            experiments=experiments,
            obligations=obligations,
            behavior_ir={},
            budget=len(operations) + 2 - 1,
        )
        in_budget = [
            item for item in receipt["prioritized"] if item["within_budget"]
        ]
        covered_ops = {item["operation_key"] for item in in_budget}
        assert covered_ops == set(operations)
        assert receipt["family_coverage"].get("state", 0) >= 1

    def test_family_tier_wins_budget_in_redundant_corner(self):
        """With budget < #ops + #families, the family quota still holds.

        The family-fair tier occupies the leading positions by design, so a
        budget of 6 keeps the state family's quota even though one operation
        is deferred to a later batch round.
        """
        operations = [f"POST /api/module/{n}/op" for n in range(6)]
        obligations, experiments = [], []
        for op_index, op_key in enumerate(operations):
            for i in range(2):
                oid = f"obl_auth_op{op_index}_{i}"
                obligations.append(_make_obligation(oid, op_key))
                experiments.append(_make_experiment(f"exp_{oid}", oid))
        obligations.append(_make_obligation(
            "obl_state_x", operations[0], risk_family="state"))
        experiments.append(_make_experiment("exp_state_x", "obl_state_x"))
        receipt = prioritize_experiments(
            experiments=experiments,
            obligations=obligations,
            behavior_ir={},
            budget=len(operations),
        )
        assert receipt["family_coverage"].get("state", 0) >= 1
        assert receipt["family_coverage"].get("authorization", 0) >= 1

    def test_family_quota_is_configurable_but_min_one(self):
        obligations, experiments = _pool(["authorization"], per_family=3)
        receipt = prioritize_experiments(
            experiments=experiments,
            obligations=obligations,
            behavior_ir={},
            budget=5,
            family_quota=0,
        )
        assert receipt["family_quota"] == 1

    def test_within_budget_rows_still_score_ordered_within_tier(self):
        """The family tier itself is ordered by score, not family name."""
        obligations = [
            _make_obligation("obl_auth", "POST /api/a", risk_family="authorization"),
            _make_obligation("obl_con", "POST /api/b", risk_family="conservation"),
        ]
        experiments = [
            _make_experiment("exp_auth", "obl_auth"),
            _make_experiment("exp_con", "obl_con"),
        ]
        receipt = prioritize_experiments(
            experiments=experiments,
            obligations=obligations,
            behavior_ir={},
            budget=2,
        )
        assert receipt["prioritized"][0]["obligation_id"] == "obl_con"


class TestFamilyCoverageBudgetFloor:
    def test_floors_small_phase_budget_to_distinct_families(self):
        selected = [
            {"obligation_id": f"o{i}", "risk_family": f"family-{i}"}
            for i in range(5)
        ]
        assert batch_core._family_coverage_budget(selected, budget=1) == 5

    def test_union_bound_covers_families_and_operations(self):
        """With ops AND families, the floor is #ops + #families."""
        selected = [
            {"obligation_id": f"o{i}", "operation_key": f"OP-{i}",
             "risk_family": "authorization"}
            for i in range(6)
        ]
        selected.append({"obligation_id": "state", "operation_key": "OP-0",
                         "risk_family": "state"})
        assert batch_core._family_coverage_budget(selected, budget=1) == 8

    def test_respects_hard_cap(self):
        from ai_test_asset_center.small_scale_validation_gate import HARD_BUDGET_CAP

        selected = [
            {"obligation_id": f"o{i}", "risk_family": f"family-{i}"}
            for i in range(HARD_BUDGET_CAP + 100)
        ]
        assert (
            batch_core._family_coverage_budget(selected, budget=1)
            == HARD_BUDGET_CAP
        )

    def test_never_shrinks_larger_budget(self):
        selected = [
            {"obligation_id": f"o{i}", "risk_family": f"family-{i}"}
            for i in range(3)
        ]
        assert batch_core._family_coverage_budget(selected, budget=50) == 50

    def test_rows_without_family_do_not_raise(self):
        selected = [
            {"obligation_id": "a"},
            {"obligation_id": "b", "risk_family": "family-b"},
        ]
        assert batch_core._family_coverage_budget(selected, budget=1) == 1


class TestBatchExecutorWiring:
    def test_executor_wires_family_coverage_floor(self):
        src = inspect.getsource(batch_core.execute_selected_experiments)
        assert "_family_coverage_budget(" in src
        assert "family_execution_quota" in src

    def test_executor_still_applies_operation_coverage_floor(self):
        src = inspect.getsource(batch_core.execute_selected_experiments)
        assert "_operation_coverage_budget(" in src
        assert "HARD_BUDGET_CAP" in src
        assert "min(_budget, 200)" not in src

    def test_prioritizer_receives_family_quota(self):
        src = inspect.getsource(batch_core.execute_selected_experiments)
        assert "family_quota=_family_quota" in src
