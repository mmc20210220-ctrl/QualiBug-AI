"""Concurrency final invariant becomes falsifiable from source declarations.

The concurrency family was structurally INDETERMINATE: no producer ever wrote
``invariant_held``, so no historical concurrency PASS proved anything. The
evaluator now computes it from the source-declared expression over the observed
after-values (final_state observer numerics). Only a structured comparison the
source declared is computed; everything else stays INDETERMINATE with a named
reason — a verdict is never guessed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_test_asset_center.assertion_dsl_base import evaluate_assertion  # noqa: E402


def _assertion(expression: dict) -> dict:
    return {
        "assertion_id": "conc-1",
        "kind": "concurrency_final_invariant",
        "property": {
            "template": "concurrent_final_invariant",
            "invariant_ref": "inv_stock_nonneg",
            "expression": expression,
            "field_rule_binding": {
                "rule_id": "inv_stock_nonneg",
                "typed_expression": expression,
            },
        },
    }


def _observations(**overrides: dict) -> dict:
    base = {
        "final_state": "observed",
        "dual_2xx": True,
        "after_values": {"available_qty": 5},
    }
    base.update(overrides)
    return base


def test_declared_bound_holds_passes() -> None:
    receipt = evaluate_assertion(
        _assertion({
            "operator": "GTE",
            "left": {"field": "available_qty"},
            "right": {"value": 0},
        }),
        observations=_observations(),
        campaign_id="campaign-conc",
        execution_id="execution-conc",
    )
    assert receipt["status"] == "PASS"
    assert receipt["actual"]["invariant_held"] is True
    assert receipt["actual"]["invariant_held_basis"] == "COMPUTED_FROM_SOURCE_INVARIANT"


def test_declared_bound_violated_is_a_concurrent_defect() -> None:
    """A race driving the declared bound negative is a VIOLATION, not a guess."""
    receipt = evaluate_assertion(
        _assertion({
            "operator": "GTE",
            "left": {"field": "available_qty"},
            "right": {"value": 0},
        }),
        observations=_observations(after_values={"available_qty": -1}),
        campaign_id="campaign-conc",
        execution_id="execution-conc",
    )
    assert receipt["status"] == "VIOLATION"
    assert receipt["actual"]["invariant_held"] is False
    assert receipt["actual"]["after_values"] == {"available_qty": -1}


def test_lte_and_eq_forms_evaluate() -> None:
    lte = evaluate_assertion(
        _assertion({
            "operator": "LTE",
            "left": {"field": "reserved_qty"},
            "right": {"field": "capacity"},
        }),
        observations=_observations(
            after_values={"reserved_qty": 80, "capacity": 100},
        ),
        campaign_id="campaign-conc",
        execution_id="execution-conc",
    )
    assert lte["status"] == "PASS"

    eq = evaluate_assertion(
        _assertion({
            "operator": "EQ",
            "left": {"field": "on_hand_qty"},
            "right": {"field": "booked_qty"},
        }),
        observations=_observations(
            after_values={"on_hand_qty": 3, "booked_qty": 3},
        ),
        campaign_id="campaign-conc",
        execution_id="execution-conc",
    )
    assert eq["status"] == "PASS"


def test_unstructured_invariant_stays_indeterminate() -> None:
    """Natural-language invariant: no structured comparison, no verdict."""
    receipt = evaluate_assertion(
        _assertion({
            "kind": "concurrency",
            "raw": "concurrent stock operations must not interfere",
            "operands": [],
        }),
        observations=_observations(),
        campaign_id="campaign-conc",
        execution_id="execution-conc",
    )
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "FINAL_INVARIANT_MISSING"
    assert (
        receipt["actual"]["invariant_held_missing_reason"]
        == "CONCURRENCY_INVARIANT_NOT_COMPARABLE"
    )


def test_missing_field_values_stay_indeterminate() -> None:
    receipt = evaluate_assertion(
        _assertion({
            "operator": "GTE",
            "left": {"field": "available_qty"},
            "right": {"value": 0},
        }),
        observations=_observations(after_values={"other_field": 1}),
        campaign_id="campaign-conc",
        execution_id="execution-conc",
    )
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "FINAL_INVARIANT_MISSING"
    assert (
        receipt["actual"]["invariant_held_missing_reason"]
        == "CONCURRENCY_INVARIANT_VALUES_MISSING"
    )
