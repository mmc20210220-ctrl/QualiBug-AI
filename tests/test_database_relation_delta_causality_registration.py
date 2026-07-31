from __future__ import annotations

from ai_test_asset_center.assertion_dsl_base import (
    _REGISTERED_ASSERTION_EVALUATORS,
    _REGISTERED_KIND_EVIDENCE_KEYS,
)
from ai_test_asset_center.database_relation_delta_causality_integrity import (
    evaluate_database_relation_causal_delta_with_integrity,
    install_database_relation_causal_delta_assertion,
    observe_operation_causality_with_integrity,
)
from ai_test_asset_center.database_relation_delta_causality_oracle import OBSERVER_ID
from ai_test_asset_center.database_relation_delta_causality_projection import (
    ASSERTION_KIND,
)
from ai_test_asset_center.observer_contracts_base import (
    OBSERVER_REGISTRY,
    _REGISTERED_OBSERVER_HANDLERS,
)


def test_existing_weak_registration_is_tightened_without_second_kind() -> None:
    install_database_relation_causal_delta_assertion()

    weak_evaluator = lambda envelope: {  # noqa: E731
        "passed": True,
        "reason_code": "",
        "expected": {},
        "actual": {},
    }
    weak_observer = lambda envelope: {}  # noqa: E731
    _REGISTERED_ASSERTION_EVALUATORS[ASSERTION_KIND] = weak_evaluator
    _REGISTERED_KIND_EVIDENCE_KEYS[ASSERTION_KIND] = ()
    _REGISTERED_OBSERVER_HANDLERS[OBSERVER_ID] = weak_observer
    OBSERVER_REGISTRY[OBSERVER_ID]["evidence_keys"] = ()

    result = install_database_relation_causal_delta_assertion()

    assert result == ASSERTION_KIND
    assert _REGISTERED_ASSERTION_EVALUATORS[ASSERTION_KIND] is (
        evaluate_database_relation_causal_delta_with_integrity
    )
    assert _REGISTERED_KIND_EVIDENCE_KEYS[ASSERTION_KIND] == (
        "approved_database_observer_phase_receipts",
        "approved_database_relation_phase_receipts",
        "operation_causality_transport_receipts",
    )
    assert _REGISTERED_OBSERVER_HANDLERS[OBSERVER_ID] is (
        observe_operation_causality_with_integrity
    )
    assert OBSERVER_REGISTRY[OBSERVER_ID]["evidence_keys"] == (
        "operation_causality_transport_receipts",
    )
