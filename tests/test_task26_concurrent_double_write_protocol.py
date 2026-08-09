"""Task 26: same-experiment concurrent double-write protocol.

The concurrency family was structurally unfalsifiable: the barrier protocol
released control and treatment at the same moment, but its assertion
(``concurrency_final_invariant``) required an ``invariant_held`` evidence key no
observer writes, and the rule's boundary (非负/超卖) never reached the assertion.
These tests pin the four-link closure:

1. protocol compile — a concurrency obligation compiles a barrier pair with the
   ``concurrent_double_write`` assertion and the final_state/barrier_timeline
   observers, carrying the rule's own boundary (oversell marker or non-negative
   equation);
2. same-moment release — the evaluator requires BOTH arms' status codes and a
   released barrier timeline with two participants before any verdict;
3. verdict — dual 2xx + boundary break is a VIOLATION (oversell), dual 2xx +
   boundary held is PASS, a pair not both accepted is PASS (the target
   serialized/refused the second write), missing evidence is INDETERMINATE;
4. scheduler — barrier experiments with an unknown resource instance serialize
   interface-wide so two concurrent pairs never overlap across experiments.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_test_asset_center.assertion_dsl_base import evaluate_assertion  # noqa: E402
from ai_test_asset_center.experiment_compiler_base import compile_experiments  # noqa: E402
from ai_test_asset_center.experiment_batch_concurrent_scheduler import (  # noqa: E402
    partition_serial_groups,
    _write_group_key,
)


# ── protocol compile ─────────────────────────────────────────────────────────

def _ir() -> dict:
    return {
        "operations": [
            {
                "id": "op_inventory_deduct",
                "operation_id": "op_inventory_deduct",
                "method": "POST",
                "path": "/api/inventory/deduct",
                "summary": "扣减库存",
                "description": "下单时扣减 available_qty",
                "request_schema": {
                    "type": "object",
                    "properties": {
                        "sku": {"type": "string", "example": "SKU-1001"},
                        "quantity": {"type": "integer", "example": 1},
                    },
                    "required": ["sku", "quantity"],
                },
                "required_roles": ["buyer"],
                "request_example": {"sku": "SKU-1001", "quantity": 1},
            },
            {
                "id": "op_inventory_get",
                "operation_id": "op_inventory_get",
                "method": "GET",
                "path": "/api/inventory/{sku}",
                "description": "查询库存",
            },
        ],
        "actors": [
            {
                "id": "actor_buyer",
                "role": "buyer",
                "account_id": "buyer-01",
                "secret_ref": "secret://probe/buyer",
            },
        ],
        "relations": [
            {
                "relation_type": "permits",
                "actor_ref": "actor_buyer",
                "operation_ref": "op_inventory_deduct",
                "status": "accepted",
            }
        ],
    }


def _obligation(expression: dict, rule_id: str = "BR-37") -> dict:
    return {
        "obligation_id": f"obl_{rule_id}",
        "risk_family": "concurrency",
        "kind": "concurrency_oversell_race",
        "required_operations": ["op_inventory_deduct"],
        "required_actors": ["actor_buyer"],
        "required_observers": ["final_state", "barrier_timeline"],
        "property": {
            "template": "concurrent_final_invariant",
            "invariant_ref": rule_id,
            "expression": expression,
        },
        "source_refs": [
            {
                "kind": "business_rule",
                "locator": "BUSINESS_RULES.md:37",
                "quote": "同一个 SKU 在高并发下不得超卖。",
            }
        ],
    }


def _compile_one(expression: dict) -> dict:
    pack = compile_experiments(
        [_obligation(expression)],
        behavior_ir=_ir(),
        environment_type="development",
        policy_version="test",
    )
    assert pack["compiled_count"] == 1, pack.get("blocked_experiments")
    return pack["experiments"][0]


def test_protocol_compiles_concurrent_double_write_pair() -> None:
    exp = _compile_one({
        "raw": "同一个 SKU 在高并发下不得超卖",
        "operator": "",
        "operands": [],
    })
    control = exp["control_plan"][0]
    treatment = exp["treatment_plan"][0]
    # Same experiment, same operation, same body, same barrier group — the two
    # requests go to the SAME resource at the same moment.
    assert control["barrier_group"] == treatment["barrier_group"]
    assert control["barrier_participant"] == "control"
    assert treatment["barrier_participant"] == "treatment"
    assert control["protocol_step"] == "concurrent_write"
    assert control["operation_ref"] == treatment["operation_ref"]
    assert control["body"] == treatment["body"]
    observer_ids = {o["observer_id"] for o in exp["observers"]}
    assert {"final_state", "barrier_timeline"} <= observer_ids
    assertion = exp["assertions"][0]
    assert assertion["kind"] == "concurrent_double_write"
    # The rule's own oversell vocabulary reaches the assertion.
    assert assertion["oversell_projection"] is True


def test_protocol_carries_source_declared_non_negative_equation() -> None:
    exp = _compile_one({
        "raw": "available_qty、locked_qty 均不能为负数",
        "operator": "",
        "operands": [],
        "equation": {
            "operator": "non_negative",
            "terms": ["available_qty", "locked_qty"],
        },
    })
    assertion = exp["assertions"][0]
    assert assertion["kind"] == "concurrent_double_write"
    assert assertion["equation"]["operator"] == "non_negative"
    assert assertion["equation"]["terms"] == ["available_qty", "locked_qty"]
    assert assertion.get("oversell_projection") is None


def test_protocol_fail_closed_on_read_operation() -> None:
    ir = _ir()
    ir["operations"][0]["method"] = "GET"
    pack = compile_experiments(
        [_obligation({"raw": "并发读取不得超卖", "operator": ""})],
        behavior_ir=ir,
        environment_type="development",
        policy_version="test",
    )
    assert pack["compiled_count"] == 0
    codes = pack["block_reason_counts"]
    assert codes.get("BLOCKED_MISSING_OPERATION") == 1


# ── evaluator: same-moment release gates ─────────────────────────────────────

def _assertion(expression: dict | None = None, **extra: object) -> dict:
    return {
        "assertion_id": "conc-dw-1",
        "kind": "concurrent_double_write",
        "property": {
            "template": "concurrent_final_invariant",
            "invariant_ref": "BR-37",
            "expression": expression or {
                "raw": "同一个 SKU 在高并发下不得超卖",
                "operator": "",
                "operands": [],
            },
        },
        **extra,
    }


def _observations(**overrides: object) -> dict:
    base: dict = {
        "control_statuses": [200],
        "treatment_statuses": [200],
        "dual_2xx": True,
        "barrier_released": True,
        "participant_count": 2,
        "before_state": {"sku": "SKU-1001", "available_qty": 1, "locked_qty": 0},
        "after_state": {"sku": "SKU-1001", "available_qty": -1, "locked_qty": 2},
    }
    base.update(overrides)
    return base


def test_missing_dual_write_evidence_is_indeterminate() -> None:
    receipt = evaluate_assertion(
        _assertion(),
        observations=_observations(control_statuses=[], treatment_statuses=[]),
        campaign_id="c",
        execution_id="e",
    )
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "CONCURRENT_DUAL_WRITE_EVIDENCE_MISSING"


def test_missing_barrier_release_is_indeterminate() -> None:
    """Dual 2xx without a released two-participant barrier is not a race test."""
    receipt = evaluate_assertion(
        _assertion(),
        observations=_observations(barrier_released=False, participant_count=0),
        campaign_id="c",
        execution_id="e",
    )
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "CONCURRENT_RELEASE_EVIDENCE_MISSING"


def test_pair_not_both_accepted_is_pass() -> None:
    """One arm rejected: the target serialized/refused the second write."""
    receipt = evaluate_assertion(
        _assertion(),
        observations=_observations(
            treatment_statuses=[409],
            dual_2xx=False,
            after_state={"sku": "SKU-1001", "available_qty": 0, "locked_qty": 1},
        ),
        campaign_id="c",
        execution_id="e",
    )
    assert receipt["status"] == "PASS"
    assert receipt["actual"]["invariant_held_basis"] == (
        "CONCURRENT_PAIR_NOT_BOTH_ACCEPTED"
    )


# ── evaluator: oversell projection ───────────────────────────────────────────

def test_oversell_projection_violates_on_negative_after() -> None:
    """Dual acceptance + a non-negative quantity driven negative = oversell."""
    receipt = evaluate_assertion(
        _assertion(oversell_projection=True),
        observations=_observations(),
        campaign_id="c",
        execution_id="e",
    )
    assert receipt["status"] == "VIOLATION"
    assert receipt["reason_code"] == "CONCURRENT_BOUNDARY_VIOLATED"
    assert receipt["actual"]["invariant_held"] is False
    assert (
        receipt["actual"]["invariant_held_basis"]
        == "COMPUTED_FROM_OVERSELL_PROJECTION"
    )


def test_oversell_projection_passes_when_conserved() -> None:
    receipt = evaluate_assertion(
        _assertion(oversell_projection=True),
        observations=_observations(
            before_state={"sku": "SKU-1001", "available_qty": 5, "locked_qty": 0},
            after_state={"sku": "SKU-1001", "available_qty": 3, "locked_qty": 2},
        ),
        campaign_id="c",
        execution_id="e",
    )
    assert receipt["status"] == "PASS"
    assert receipt["actual"]["invariant_held"] is True


def test_oversell_projection_ignores_already_negative_fields() -> None:
    """A field already negative BEFORE the pair cannot prove a NEW oversell."""
    receipt = evaluate_assertion(
        _assertion(oversell_projection=True),
        observations=_observations(
            before_state={"sku": "SKU-1001", "adjustment": -4, "available_qty": 3},
            after_state={"sku": "SKU-1001", "adjustment": -7, "available_qty": 3},
        ),
        campaign_id="c",
        execution_id="e",
    )
    assert receipt["status"] == "PASS"


def test_oversell_projection_without_common_fields_is_indeterminate() -> None:
    receipt = evaluate_assertion(
        _assertion(oversell_projection=True),
        observations=_observations(
            before_state={"sku": "SKU-1001"},
            after_state={"sku": "SKU-1001", "note": "ok"},
        ),
        campaign_id="c",
        execution_id="e",
    )
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "FINAL_INVARIANT_MISSING"


def test_no_boundary_no_projection_is_indeterminate() -> None:
    """dual 2xx alone is never a verdict (insufficient_signal: dual_2xx_alone)."""
    receipt = evaluate_assertion(
        _assertion(),
        observations=_observations(),
        campaign_id="c",
        execution_id="e",
    )
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "FINAL_INVARIANT_MISSING"
    assert (
        receipt["actual"]["invariant_held_missing_reason"]
        == "CONCURRENCY_INVARIANT_NOT_COMPARABLE"
    )


# ── evaluator: source-declared boundaries ────────────────────────────────────

def test_non_negative_equation_violation() -> None:
    receipt = evaluate_assertion(
        _assertion(
            equation={
                "operator": "non_negative",
                "terms": ["available_qty", "locked_qty"],
            }
        ),
        observations=_observations(
            after_values={"available_qty": -1, "locked_qty": 2},
        ),
        campaign_id="c",
        execution_id="e",
    )
    assert receipt["status"] == "VIOLATION"
    assert receipt["reason_code"] == "CONCURRENT_BOUNDARY_VIOLATED"
    assert (
        receipt["actual"]["invariant_held_basis"]
        == "COMPUTED_FROM_NON_NEGATIVE_EQUATION"
    )


def test_non_negative_equation_pass() -> None:
    receipt = evaluate_assertion(
        _assertion(
            equation={
                "operator": "non_negative",
                "terms": ["available_qty"],
            }
        ),
        observations=_observations(
            after_values={"available_qty": 2, "locked_qty": 2},
        ),
        campaign_id="c",
        execution_id="e",
    )
    assert receipt["status"] == "PASS"


def test_structured_comparison_still_evaluates() -> None:
    receipt = evaluate_assertion(
        _assertion(
            expression={
                "operator": "GTE",
                "left": {"field": "available_qty"},
                "right": {"value": 0},
            }
        ),
        observations=_observations(after_values={"available_qty": -1}),
        campaign_id="c",
        execution_id="e",
    )
    assert receipt["status"] == "VIOLATION"
    assert (
        receipt["actual"]["invariant_held_basis"]
        == "COMPUTED_FROM_SOURCE_INVARIANT"
    )


def test_legacy_concurrency_final_invariant_computes_non_negative_equation() -> None:
    """Obligations compiled with the legacy kind become falsifiable too."""
    receipt = evaluate_assertion(
        {
            "assertion_id": "conc-1",
            "kind": "concurrency_final_invariant",
            "property": {
                "template": "concurrent_final_invariant",
                "invariant_ref": "BR-36",
                "expression": {
                    "raw": "available_qty 不能为负数",
                    "operator": "",
                    "operands": [],
                    "equation": {
                        "operator": "non_negative",
                        "terms": ["available_qty"],
                    },
                },
            },
        },
        observations={
            "final_state": "observed",
            "dual_2xx": True,
            "after_values": {"available_qty": -1},
        },
        campaign_id="c",
        execution_id="e",
    )
    assert receipt["status"] == "VIOLATION"
    assert receipt["actual"]["invariant_held"] is False
    assert (
        receipt["actual"]["invariant_held_basis"]
        == "COMPUTED_FROM_NON_NEGATIVE_EQUATION"
    )


# ── scheduler: barrier-pair grouping ─────────────────────────────────────────

def _barrier_experiment(oid: str, actor: str) -> dict:
    return {
        "obligation_id": oid,
        "control_plan": [{
            "step_id": "control_1",
            "actor_ref": actor,
            "operation_ref": "op_inventory_deduct",
            "protocol_step": "concurrent_write",
            "barrier_group": "barrier:op_inventory_deduct",
            "barrier_participant": "control",
            "method": "POST",
            "path": "/api/inventory/deduct",
            "path_template": "/api/inventory/deduct",
        }],
        "treatment_plan": [{
            "step_id": "treatment_1",
            "actor_ref": actor,
            "operation_ref": "op_inventory_deduct",
            "protocol_step": "concurrent_write",
            "barrier_group": "barrier:op_inventory_deduct",
            "barrier_participant": "treatment",
            "method": "POST",
            "path": "/api/inventory/deduct",
            "path_template": "/api/inventory/deduct",
        }],
        "actor_selection_contract": {
            "treatment_actor_ref": actor,
            "control_actor_ref": actor,
        },
    }


def test_scheduler_serializes_barrier_pairs_with_unknown_resource() -> None:
    """Two oversell pairs, different actors, same interface, unknown resource
    instance → one serial group: their race windows never overlap."""
    exp_a = _barrier_experiment("obl_a", "actor_a")
    exp_b = _barrier_experiment("obl_b", "actor_b")
    by_obl = {"obl_a": exp_a, "obl_b": exp_b}
    selected = [{"obligation_id": "obl_a"}, {"obligation_id": "obl_b"}]
    groups = partition_serial_groups(selected, by_obl, {"operations": []})
    assert len(groups) == 1
    assert {_g["obligation_id"] for _g in groups[0]} == {"obl_a", "obl_b"}


def test_scheduler_barrier_key_is_interface_wide() -> None:
    key_a = _write_group_key(
        _barrier_experiment("obl_a", "actor_a"), {}
    )
    key_b = _write_group_key(
        _barrier_experiment("obl_b", "actor_b"), {}
    )
    assert key_a == key_b == ("barrier", "POST /api/inventory/deduct")


def test_scheduler_known_resource_keeps_res_domain() -> None:
    exp = _barrier_experiment("obl_c", "actor_a")
    for plan in (exp["control_plan"], exp["treatment_plan"]):
        for step in plan:
            step["path"] = "/api/inventory/{sku}/deduct"
            step["path_template"] = "/api/inventory/{sku}/deduct"
    exp["runtime_bindings"] = {"sku": "SKU-1001"}
    key = _write_group_key(exp, {})
    assert key[0] == "res"
    assert key[2] == "SKU-1001"


def test_scheduler_non_barrier_write_keeps_actor_domain() -> None:
    exp = {
        "obligation_id": "obl_d",
        "treatment_plan": [{
            "step_id": "treatment_1",
            "actor_ref": "actor_a",
            "operation_ref": "op_inventory_deduct",
            "protocol_step": "conservation_write",
            "method": "POST",
            "path": "/api/inventory/deduct",
            "path_template": "/api/inventory/deduct",
        }],
        "actor_selection_contract": {"treatment_actor_ref": "actor_a"},
    }
    key = _write_group_key(exp, {})
    assert key == ("iface", "POST /api/inventory/deduct", "actor_a")
