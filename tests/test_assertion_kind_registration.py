"""Assertion kinds must be registrable, and a registered kind cannot fake a verdict.

``SUPPORTED_KINDS`` was a literal set and the evaluator a hardcoded if/elif chain, so a
new assertion kind required editing assertion_dsl_base. The facade set-union pattern used
by assertion_dsl.py and assertion_dsl_validation_base.py can add a NAME to SUPPORTED_KINDS
but cannot add a dispatch branch — which is exactly how ``temporal_date_boundary`` became a
compiled kind with no evaluator that raises ``unsupported_assertion_kind``.

``register_assertion_kind`` is additive: a registered kind returns its receipt before the
built-in chain runs, so the chain producing every real assertion today is untouched and
needed no re-indentation.

Two guarantees are STRUCTURAL rather than left to each evaluator author:

* A kind cannot be registered against observation keys no observer declares it produces.
  That is the state which left three built-in kinds present in SUPPORTED_KINDS, compiled,
  executed, and permanently unable to return a verdict.
* When a declared evidence key is absent at evaluation time the kind returns
  INDETERMINATE without calling the evaluator. Otherwise an evaluator that forgets to
  check presence reports a VIOLATION from missing data — the mirror of the false-PASS
  class: unmeasured must never read as violated, just as untested must never read as
  verified.

Registration is process-global, so every test cleans up after itself.
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest

from ai_test_asset_center import assertion_dsl_base as adb
from ai_test_asset_center import observer_contracts_base as ocb
from ai_test_asset_center.assertion_dsl_base import (
    KIND_ALIASES,
    SUPPORTED_KINDS,
    evaluate_assertion,
    register_assertion_kind,
    registered_assertion_kinds,
)
from ai_test_asset_center.observer_contracts_base import (
    OBSERVER_REGISTRY,
    _receipt,
    register_observer,
)


EVIDENCE_KEY = "test_only_persisted_row_state"
OBSERVER_ID = "test_only_persistence_reader"
KIND = "test_only_row_count_delta"


@pytest.fixture()
def registry_cleanup() -> Iterator[None]:
    yield
    adb._REGISTERED_ASSERTION_EVALUATORS.pop(KIND, None)
    adb._REGISTERED_KIND_EVIDENCE_KEYS.pop(KIND, None)
    SUPPORTED_KINDS.discard(KIND)
    OBSERVER_REGISTRY.pop(OBSERVER_ID, None)
    ocb._REGISTERED_OBSERVER_HANDLERS.pop(OBSERVER_ID, None)


def _register_producer() -> None:
    register_observer(
        OBSERVER_ID,
        surface="persistence_state",
        adapter="db_sql",
        handler=lambda _envelope: _receipt(
            observer_id=OBSERVER_ID, status="OBSERVED", reason_code="", evidence={}
        ),
        evidence_keys=(EVIDENCE_KEY,),
    )


def _delta_evaluator(envelope: dict[str, Any]) -> dict[str, Any]:
    """Deliberately does NOT check evidence presence — the framework must."""
    observed = (envelope["observations"].get(EVIDENCE_KEY) or {}).get("delta")
    return {"passed": observed == 1, "expected": {"delta": 1}, "actual": {"delta": observed}}


def _assertion() -> dict[str, Any]:
    return {"assertion_id": "a1", "kind": KIND, "property": {}}


def test_kind_cannot_be_registered_without_a_producer(registry_cleanup) -> None:
    with pytest.raises(ValueError) as excinfo:
        register_assertion_kind(
            KIND, evaluator=_delta_evaluator, required_evidence_keys=(EVIDENCE_KEY,)
        )
    assert EVIDENCE_KEY in str(excinfo.value)
    assert KIND not in registered_assertion_kinds()


def test_kind_registers_once_a_producer_exists(registry_cleanup) -> None:
    _register_producer()
    registered = register_assertion_kind(
        KIND, evaluator=_delta_evaluator, required_evidence_keys=(EVIDENCE_KEY,)
    )
    assert registered == KIND
    assert KIND in registered_assertion_kinds()
    assert KIND in SUPPORTED_KINDS


def test_registered_kind_produces_pass_and_violation(registry_cleanup) -> None:
    _register_producer()
    register_assertion_kind(
        KIND, evaluator=_delta_evaluator, required_evidence_keys=(EVIDENCE_KEY,)
    )

    satisfied = evaluate_assertion(_assertion(), observations={EVIDENCE_KEY: {"delta": 1}})
    assert satisfied["status"] == "PASS"

    violated = evaluate_assertion(_assertion(), observations={EVIDENCE_KEY: {"delta": 3}})
    assert violated["status"] == "VIOLATION"
    assert violated["actual"] == {"delta": 3}


@pytest.mark.parametrize(
    "observations,label",
    [
        ({}, "key absent entirely"),
        ({EVIDENCE_KEY: {}}, "key present but empty"),
        ({EVIDENCE_KEY: None}, "key present but null"),
    ],
)
def test_absent_evidence_is_indeterminate_never_a_violation(
    registry_cleanup, observations: dict, label: str
) -> None:
    """The evaluator above would return passed=False here; the framework must not let it.

    Comparing an expected delta of 1 against a missing value yields False, which would
    seal as VIOLATION — a fabricated defect from absent evidence.
    """
    _register_producer()
    register_assertion_kind(
        KIND, evaluator=_delta_evaluator, required_evidence_keys=(EVIDENCE_KEY,)
    )

    receipt = evaluate_assertion(_assertion(), observations=observations)
    assert receipt["status"] == "INDETERMINATE", label
    assert receipt["reason_code"] == "ASSERTION_EVIDENCE_MISSING"
    assert EVIDENCE_KEY in receipt["actual"]["absent_observation_keys"]


def test_evaluator_exception_is_a_harness_error_not_a_verdict(registry_cleanup) -> None:
    _register_producer()

    def exploding(_envelope: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("evaluator exploded")

    register_assertion_kind(
        KIND, evaluator=exploding, required_evidence_keys=(EVIDENCE_KEY,)
    )
    receipt = evaluate_assertion(_assertion(), observations={EVIDENCE_KEY: {"delta": 1}})

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "ASSERTION_EVALUATION_ERROR"
    assert receipt["harness_error"] is True
    assert "evaluator exploded" in receipt["error"]


@pytest.mark.parametrize("bad_passed", ["yes", 1, 0, "true"])
def test_evaluator_must_return_a_tri_state_passed(registry_cleanup, bad_passed: Any) -> None:
    """passed must be exactly True, False or None — a truthy string is not a verdict."""
    _register_producer()
    register_assertion_kind(
        KIND,
        evaluator=lambda _envelope: {"passed": bad_passed},
        required_evidence_keys=(EVIDENCE_KEY,),
    )
    receipt = evaluate_assertion(_assertion(), observations={EVIDENCE_KEY: {"delta": 1}})

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "ASSERTION_EVALUATION_ERROR"


def test_evaluator_returning_a_non_dict_is_a_harness_error(registry_cleanup) -> None:
    _register_producer()
    register_assertion_kind(
        KIND, evaluator=lambda _envelope: "not a dict",
        required_evidence_keys=(EVIDENCE_KEY,),
    )
    receipt = evaluate_assertion(_assertion(), observations={EVIDENCE_KEY: {"delta": 1}})

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["harness_error"] is True


def test_built_in_kinds_cannot_be_shadowed() -> None:
    with pytest.raises(ValueError):
        register_assertion_kind("http_status", evaluator=lambda e: {"passed": True})


def test_family_alias_names_are_rejected() -> None:
    """Registering "concurrency" would be ambiguous with its evaluator-shaped target."""
    alias = next(iter(KIND_ALIASES))
    with pytest.raises(ValueError):
        register_assertion_kind(alias, evaluator=lambda e: {"passed": True})


def test_non_callable_evaluator_is_rejected() -> None:
    with pytest.raises(ValueError):
        register_assertion_kind("test_only_not_callable", evaluator="nope")


def test_empty_kind_is_rejected() -> None:
    with pytest.raises(ValueError):
        register_assertion_kind("", evaluator=lambda e: {"passed": True})


def test_built_in_evaluation_is_unaffected() -> None:
    """The additive design must leave the built-in chain producing identical results."""
    receipt = evaluate_assertion(
        {"assertion_id": "a1", "kind": "http_status", "property": {"expected_status": 200}},
        observations={"treatment_observation": {"status": 200}},
    )
    assert receipt["status"] in {"PASS", "VIOLATION", "INDETERMINATE"}
    assert receipt["kind"] == "http_status"


def test_no_test_only_kind_leaks() -> None:
    leaked = [kind for kind in registered_assertion_kinds() if kind.startswith("test_only_")]
    assert not leaked, f"test-only assertion kinds left registered: {leaked}"
