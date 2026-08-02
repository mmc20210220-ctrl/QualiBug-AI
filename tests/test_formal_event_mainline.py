from __future__ import annotations

from typing import Any

import pytest

# The product composition installs stable event receipt identity, cardinality,
# reason-code and execution-outcome bridges on the one public runtime authority.
from ai_test_asset_center import discovery_runtime as _runtime_install  # noqa: F401
from ai_test_asset_center import formal_event_surface as events
from ai_test_asset_center.formal_event_pre_cleanup import (
    _pre_observe_event,
    install_formal_event_pre_cleanup_observer,
)


def _source_ref() -> dict[str, str]:
    return {
        "source_id": "prd_events_v1",
        "version": "1",
        "locator": "section=event-contracts;row=1",
        "kind": "formal_event_contract",
        "quote_hash": "event-contract-source-hash",
    }


def _event_contract() -> dict[str, Any]:
    return {
        "schema_version": "qualibug.formal-event-contract.v1",
        "signal_type": "formal_event_contract",
        "contract_id": "event_order_created_once",
        "title": "Trigger emits exactly one OrderCreated event",
        "source_refs": [_source_ref()],
        "operation_ref": "read_event_trigger",
        "actor_ref": "actor_public",
        "observer_path": "/test-observers/events",
        "events_path": "items",
        "event_id_field": "event_id",
        "event_type_field": "event_type",
        "correlation_field": "aggregate_id",
        "correlation_query_parameter": "aggregate_id",
        "correlation_source": {
            "location": "treatment_response",
            "path": "id",
        },
        "expected_event_type": "OrderCreated",
        "expected_min_count": 1,
        "expected_max_count": 1,
        "observation_window_ms": 1000,
        "poll_interval_ms": 100,
        "timestamp_field": "occurred_at",
        "observer_requires_actor_token": False,
    }


def _binding_identity() -> dict[str, Any]:
    return {
        "schema_version": "qualibug.formal-event-binding-identity-bridge.v1",
        "status": "BOUND",
        "event_contract_ref": "event_order_created_once",
        "implementation_binding_ref": "impl_binding_order_trigger",
        "action_surface_binding_ref": "action_surface_order_trigger",
        "observer_binding_ref": "observer_binding_order_created",
        "interface_id": "interface_order_trigger",
        "actor_ref": "actor_public",
        "scenario_ref": "scenario_order_created_event",
        "runtime_plan_ref": "runtime_plan_order_created_event",
        "runtime_materialization_ref": "runtime_materialization_order_created_event",
        "contract_field_binding_refs": ["contract_field_order_id"],
        "runtime_value_binding_refs": ["runtime_value_order_id"],
        "binding_authority": "enterprise_binding_identity_graph",
        "identity_reselection_allowed": False,
        "token_overlap_is_authoritative": False,
    }


def _assertion() -> dict[str, Any]:
    return {
        "assertion_id": "assert_order_created_event",
        "kind": events.ASSERTION_KIND,
        "property": {
            "template": events.PROTOCOL_TEMPLATE,
            "invariant_ref": "inv_order_created_event",
            "actor_ref": "actor_public",
            "event_contract_id": "event_order_created_once",
            "event_contract": _event_contract(),
            "formal_event_binding_identity": _binding_identity(),
        },
        "source_refs": [_source_ref()],
    }


def _observation(
    *,
    total: int,
    correlated: int,
    event_types: list[str],
    mismatched: list[str] | None = None,
    completed: bool = True,
) -> dict[str, Any]:
    return {
        "expected_min_count": 1,
        "expected_max_count": 1,
        "observed_total_count": total,
        "observed_correlated_count": correlated,
        "observed_event_types": event_types,
        "mismatched_event_types": list(mismatched or []),
        "observation_window_completed": completed,
        "coverage_complete": completed,
    }


def test_formal_event_surface_installs_additively() -> None:
    installed = events.install_formal_event_surface()
    from ai_test_asset_center.experiment_protocol_registry import (
        registered_family_protocols,
    )
    from ai_test_asset_center.observer_contracts_base import OBSERVER_REGISTRY
    from ai_test_asset_center.test_obligation import canonical_risk_families

    assert events.RISK_FAMILY in canonical_risk_families()
    assert events.OBSERVER_ID in OBSERVER_REGISTRY
    assert installed["protocol"] in set(registered_family_protocols())


def test_event_protocol_compiles_source_grounded_contract() -> None:
    events.install_formal_event_surface()
    from ai_test_asset_center.experiment_protocol_registry import (
        resolve_family_protocol,
        validate_registered_protocol_result,
    )

    protocol = resolve_family_protocol(
        events.RISK_FAMILY,
        events.PROTOCOL_TEMPLATE,
    )
    operation = {
        "id": "bir_op_read_event_trigger",
        "operation_id": "read_event_trigger",
        "source_operation_refs": ["read_event_trigger"],
        "method": "GET",
        "path": "/api/event-trigger",
        "raw_path": "/api/event-trigger",
    }
    compiled = protocol["compiler"]({
        "property_spec": {
            "template": events.PROTOCOL_TEMPLATE,
            "invariant_ref": "inv_order_created_event",
            "operation_ref": operation["id"],
            "actor_ref": "actor_public",
            "event_contract": _event_contract(),
            "formal_event_binding_identity": _binding_identity(),
        },
        "operation_ref": operation["id"],
        "operation": operation,
        "treatment_actor_ref": "actor_public",
    })
    experiment = validate_registered_protocol_result(
        compiled,
        registration=protocol,
    )

    assert experiment["status"] == "COMPILED"
    assert experiment["observers"][0]["observer_id"] == events.OBSERVER_ID
    assert experiment["treatment_plan"][0]["protocol_step"] == "event_trigger"
    assert experiment["assertion"]["kind"] == events.ASSERTION_KIND


def test_event_oracle_detects_delivery_count_violation() -> None:
    events.install_formal_event_surface()
    from ai_test_asset_center.assertion_dsl import evaluate_assertion
    from ai_test_asset_center.formal_event_verdict_reason_bridge import (
        classify_event_delivery_violation,
    )

    observation = _observation(
        total=0,
        correlated=0,
        event_types=[],
    )
    verdict = evaluate_assertion(
        _assertion(),
        observations={events.EVIDENCE_KEY: observation},
    )
    assert verdict["passed"] is False
    assert classify_event_delivery_violation(observation) == [
        "EVENT_DELIVERY_COUNT_BELOW_MINIMUM"
    ]


def test_event_oracle_detects_duplicate_unique_deliveries() -> None:
    events.install_formal_event_surface()
    from ai_test_asset_center.assertion_dsl import evaluate_assertion
    from ai_test_asset_center.formal_event_verdict_reason_bridge import (
        classify_event_delivery_violation,
    )

    observation = _observation(
        total=2,
        correlated=2,
        event_types=["OrderCreated", "OrderCreated"],
    )
    verdict = evaluate_assertion(
        _assertion(),
        observations={events.EVIDENCE_KEY: observation},
    )
    assert verdict["passed"] is False
    assert classify_event_delivery_violation(observation) == [
        "EVENT_DELIVERY_COUNT_ABOVE_MAXIMUM"
    ]


def test_event_oracle_indeterminate_when_window_not_completed() -> None:
    events.install_formal_event_surface()
    from ai_test_asset_center.assertion_dsl import evaluate_assertion
    from ai_test_asset_center.formal_event_verdict_reason_bridge import (
        classify_event_delivery_violation,
    )

    observation = _observation(
        total=1,
        correlated=0,
        event_types=["OrderCreated"],
        completed=False,
    )
    verdict = evaluate_assertion(
        _assertion(),
        observations={events.EVIDENCE_KEY: observation},
    )
    assert verdict["passed"] is None
    # Coverage-incomplete observations are never promoted to a final violation;
    # the runtime Oracle receipt owns the INDETERMINATE reason code.
    assert classify_event_delivery_violation(observation) == [
        "EVENT_DELIVERY_CORRELATION_MISMATCH",
        "EVENT_DELIVERY_COUNT_BELOW_MINIMUM",
    ]


def test_event_observer_runs_before_cleanup_and_finalizer_reuses_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events.install_formal_event_surface()
    install_formal_event_pre_cleanup_observer()

    from ai_test_asset_center.observer_contracts_base import (
        _REGISTERED_OBSERVER_HANDLERS,
        _receipt,
    )

    calls: list[str] = []

    def fake_handler(_envelope):
        calls.append("observe")
        return _receipt(
            observer_id=events.OBSERVER_ID,
            status="OBSERVED",
            evidence={
                events.EVIDENCE_KEY: {
                    "observed_correlated_count": 1,
                    "coverage_complete": True,
                    "observation_window_completed": True,
                }
            },
        )

    monkeypatch.setattr(events, "_event_observer_handler", fake_handler)
    exp = {
        "treatment_plan": [{
            "step_id": "treatment_1",
            "intent": "trigger_source_declared_event",
            "protocol_step": "event_trigger",
        }],
        "observers": [{"observer_id": events.OBSERVER_ID}],
        "assertions": [{"kind": events.ASSERTION_KIND, "property": {}}],
    }
    observations: dict = {}
    receipt = _pre_observe_event(
        exp=exp,
        observations=observations,
        campaign_id="campaign_event_1",
        execution_id="exec_event_1",
    )
    assert calls == ["observe"]
    assert receipt is not None
    assert receipt["evidence"]["step_id"] == "treatment_1"
    assert observations[events.EVIDENCE_KEY]["observation_phase"] == "pre_cleanup"

    reused = _REGISTERED_OBSERVER_HANDLERS[events.OBSERVER_ID]({
        "experiment": exp,
        "observations": observations,
        "assertion": exp["assertions"][0],
    })
    assert calls == ["observe"], "Finalizer must not poll the event endpoint a second time"
    assert reused["receipt_id"] == receipt["receipt_id"]
