"""Persistence assertion kinds — closing the four-link chain for database defects.

``persistence_observer`` can measure database state, but an observer whose evidence no
assertion kind consumes produces evidence nobody judges: the chain still stops one link
short. These kinds close it.

The properties that matter most here are the refusals, not the verdicts:

* Every expectation is SOURCE-DECLARED. An inferred state enumeration would let the product
  flag a legitimate value as a defect; an inferred bound would manufacture violations out of
  ordinary data. Missing declaration means INDETERMINATE, never a guess.
* A PARTIAL reading never reports PASS. Truncated rows or a partially-failed multi-database
  topology means "nothing wrong in what we saw", which is not "nothing wrong".
* An unjudgeable value is INDETERMINATE, not a violation. A non-numeric value in a field
  declared numeric would otherwise seal as out-of-bounds — a fabricated defect.
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest

from ai_test_asset_center import assertion_dsl_base as adb
from ai_test_asset_center import observer_contracts_base as ocb
from ai_test_asset_center.assertion_dsl_base import SUPPORTED_KINDS, evaluate_assertion
from ai_test_asset_center.observer_contracts_base import OBSERVER_REGISTRY
from ai_test_asset_center.persistence_assertions import (
    KIND_FIELD_BOUND,
    KIND_STATE_ENUMERATION,
    RISK_FAMILY,
    install_persistence_surface,
)
from ai_test_asset_center.persistence_observer import EVIDENCE_KEY, OBSERVER_ID


def _unregister_persistence_risk_family() -> None:
    """Undo register_risk_family's descriptor writes so teardown restores the
    pre-install registry state. Without this the family survives in the by-family
    maps while its observer is popped, and any later registry check sees a family
    declaring an unusable observer."""
    from ai_test_asset_center import experiment_compiler_obligation as _eco
    from ai_test_asset_center import obligation_source_adapter as _osa
    from ai_test_asset_center.test_obligation import _RUNTIME_CANONICAL_FAMILIES

    _RUNTIME_CANONICAL_FAMILIES.pop(RISK_FAMILY, None)
    _osa._RELATION_TYPES_BY_FAMILY.pop(RISK_FAMILY, None)
    _osa._TEMPLATE_BY_FAMILY.pop(RISK_FAMILY, None)
    _osa._OBSERVERS_BY_FAMILY.pop(RISK_FAMILY, None)
    _eco._FAMILY_ASSERTION_KIND.pop(RISK_FAMILY, None)


@pytest.fixture()
def persistence_surface() -> Iterator[dict[str, str]]:
    installed = install_persistence_surface()
    yield installed
    for kind in (KIND_STATE_ENUMERATION, KIND_FIELD_BOUND):
        adb._REGISTERED_ASSERTION_EVALUATORS.pop(kind, None)
        adb._REGISTERED_KIND_EVIDENCE_KEYS.pop(kind, None)
        SUPPORTED_KINDS.discard(kind)
    OBSERVER_REGISTRY.pop(OBSERVER_ID, None)
    ocb._REGISTERED_OBSERVER_HANDLERS.pop(OBSERVER_ID, None)
    _unregister_persistence_risk_family()


def _observations(
    rows: list[dict[str, Any]],
    *,
    coverage_complete: bool = True,
    rows_truncated: bool = False,
    module: str = "order",
) -> dict[str, Any]:
    return {
        EVIDENCE_KEY: {
            "observations": [
                {"module": module, "rows": rows, "rows_truncated": rows_truncated}
            ]
        },
        "coverage_complete": coverage_complete,
    }


def _enumeration_assertion(**overrides: Any) -> dict[str, Any]:
    prop: dict[str, Any] = {
        "persistence_state_field": "lifecycle_state",
        "persistence_allowed_states": ["NEW", "CONFIRMED", "CLOSED"],
    }
    prop.update(overrides)
    return {"assertion_id": "a1", "kind": KIND_STATE_ENUMERATION, "property": prop}


def _bound_assertion(**overrides: Any) -> dict[str, Any]:
    prop: dict[str, Any] = {"persistence_bounded_field": "held_quantity", "persistence_min": 0}
    prop.update(overrides)
    return {"assertion_id": "b1", "kind": KIND_FIELD_BOUND, "property": prop}


# ── installation ordering ───────────────────────────────────────────────────

def test_install_registers_observer_before_kinds(persistence_surface) -> None:
    """register_assertion_kind refuses a kind whose evidence no observer produces.

    So the order is enforced by construction rather than left to the caller. Getting it
    backwards is exactly the failure mode that left three built-in kinds permanently
    indeterminate.
    """
    assert persistence_surface["observer"] == OBSERVER_ID
    assert OBSERVER_ID in OBSERVER_REGISTRY
    assert KIND_STATE_ENUMERATION in SUPPORTED_KINDS
    assert KIND_FIELD_BOUND in SUPPORTED_KINDS


def test_install_is_idempotent(persistence_surface) -> None:
    again = install_persistence_surface()
    assert again["observer"] == OBSERVER_ID
    assert again[KIND_STATE_ENUMERATION] == KIND_STATE_ENUMERATION


# ── state enumeration ───────────────────────────────────────────────────────

def test_all_legal_states_pass(persistence_surface) -> None:
    receipt = evaluate_assertion(
        _enumeration_assertion(),
        observations=_observations([
            {"lifecycle_state": "NEW"}, {"lifecycle_state": "CONFIRMED"},
        ]),
    )
    assert receipt["status"] == "PASS"
    assert receipt["actual"]["offending_row_count"] == 0


def test_state_outside_the_declared_enumeration_is_a_violation(persistence_surface) -> None:
    receipt = evaluate_assertion(
        _enumeration_assertion(),
        observations=_observations([
            {"lifecycle_state": "NEW"}, {"lifecycle_state": "GHOST_STATE"},
        ]),
    )
    assert receipt["status"] == "VIOLATION"
    assert receipt["actual"]["offending_row_count"] == 1
    assert receipt["actual"]["offending_rows"][0]["lifecycle_state"] == "GHOST_STATE"
    assert receipt["expected"]["allowed_states"] == ["CLOSED", "CONFIRMED", "NEW"]


def test_undeclared_enumeration_judges_nothing(persistence_surface) -> None:
    """Without a declared enumeration there is no way to tell an illegal state from a new one."""
    receipt = evaluate_assertion(
        _enumeration_assertion(persistence_allowed_states=[]),
        observations=_observations([{"lifecycle_state": "ANYTHING"}]),
    )
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "PERSISTED_STATE_ENUMERATION_NOT_DECLARED"


def test_undeclared_field_judges_nothing(persistence_surface) -> None:
    receipt = evaluate_assertion(
        _enumeration_assertion(persistence_state_field=""),
        observations=_observations([{"lifecycle_state": "NEW"}]),
    )
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "PERSISTED_STATE_FIELD_NOT_DECLARED"


@pytest.mark.parametrize(
    "kwargs,label",
    [
        ({"rows_truncated": True}, "row cap hit"),
        ({"coverage_complete": False}, "a database in the topology was not read"),
    ],
)
def test_partial_reading_never_reports_pass(persistence_surface, kwargs: dict, label: str) -> None:
    """"Nothing wrong in what we saw" is not "nothing wrong"."""
    receipt = evaluate_assertion(
        _enumeration_assertion(),
        observations=_observations([{"lifecycle_state": "NEW"}], **kwargs),
    )
    assert receipt["status"] == "INDETERMINATE", label
    assert receipt["reason_code"] == "PERSISTED_COVERAGE_INCOMPLETE"


def test_partial_reading_still_reports_a_violation_it_did_see(persistence_surface) -> None:
    """A found defect is real even when coverage was incomplete.

    Incomplete coverage must suppress PASS, not suppress evidence.
    """
    receipt = evaluate_assertion(
        _enumeration_assertion(),
        observations=_observations([{"lifecycle_state": "GHOST_STATE"}], coverage_complete=False),
    )
    assert receipt["status"] == "VIOLATION"


def test_zero_rows_is_not_a_pass(persistence_surface) -> None:
    receipt = evaluate_assertion(_enumeration_assertion(), observations=_observations([]))
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "PERSISTED_ROWS_NOT_OBSERVED"


def test_field_the_observer_did_not_read_is_a_mismatch_not_a_defect(persistence_surface) -> None:
    receipt = evaluate_assertion(
        _enumeration_assertion(),
        observations=_observations([{"some_other_column": "x"}]),
    )
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "PERSISTED_STATE_FIELD_NOT_OBSERVED"
    assert "some_other_column" in receipt["actual"]["observed_fields"]


def test_internal_module_marker_is_not_reported_as_a_column(persistence_surface) -> None:
    receipt = evaluate_assertion(
        _enumeration_assertion(),
        observations=_observations([{"some_other_column": "x"}]),
    )
    assert "_module" not in receipt["actual"]["observed_fields"]


# ── field bound ─────────────────────────────────────────────────────────────

def test_values_within_the_declared_bound_pass(persistence_surface) -> None:
    receipt = evaluate_assertion(
        _bound_assertion(),
        observations=_observations([{"held_quantity": 5}, {"held_quantity": 0}]),
    )
    assert receipt["status"] == "PASS"


def test_value_below_the_declared_minimum_is_a_violation(persistence_surface) -> None:
    receipt = evaluate_assertion(
        _bound_assertion(),
        observations=_observations([{"held_quantity": 5}, {"held_quantity": -3}]),
    )
    assert receipt["status"] == "VIOLATION"
    assert receipt["actual"]["offending_rows"][0]["held_quantity"] == -3


def test_value_above_the_declared_maximum_is_a_violation(persistence_surface) -> None:
    receipt = evaluate_assertion(
        _bound_assertion(persistence_min=None, persistence_max=10),
        observations=_observations([{"held_quantity": 11}]),
    )
    assert receipt["status"] == "VIOLATION"


def test_no_declared_bound_judges_nothing(persistence_surface) -> None:
    """A bound check with no bound is not a check."""
    receipt = evaluate_assertion(
        _bound_assertion(persistence_min=None, persistence_max=None),
        observations=_observations([{"held_quantity": -99}]),
    )
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "PERSISTED_BOUND_NOT_DECLARED"


def test_non_numeric_value_is_indeterminate_not_out_of_bounds(persistence_surface) -> None:
    """Reporting an unparseable value as out of bounds would be a fabricated defect."""
    receipt = evaluate_assertion(
        _bound_assertion(),
        observations=_observations([{"held_quantity": "not-a-number"}]),
    )
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "PERSISTED_FIELD_NOT_NUMERIC"


def test_multi_database_offenders_name_their_module(persistence_surface) -> None:
    """A violation in a multi-database topology must say WHICH database it came from."""
    observations = {
        EVIDENCE_KEY: {
            "observations": [
                {"module": "order_db", "rows": [{"held_quantity": 1}], "rows_truncated": False},
                {"module": "payment_db", "rows": [{"held_quantity": -7}], "rows_truncated": False},
            ]
        },
        "coverage_complete": True,
    }
    receipt = evaluate_assertion(_bound_assertion(), observations=observations)

    assert receipt["status"] == "VIOLATION"
    assert receipt["actual"]["offending_rows"][0]["module"] == "payment_db"
    assert set(receipt["actual"]["modules_observed"]) == {"order_db", "payment_db"}
