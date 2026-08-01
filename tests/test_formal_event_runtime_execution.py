from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

# Importing the public runtime installs the formal Event surface and its additive
# execution/receipt/verdict bridges on the one V12 experiment authority.
from ai_test_asset_center import discovery_runtime as _runtime_install  # noqa: F401
from ai_test_asset_center import experiment_executor as executor
from ai_test_asset_center import experiment_plan_executor as plan_executor
from ai_test_asset_center import formal_event_surface as events
from ai_test_asset_center.formal_event_binding_evidence_projection import (
    project_formal_event_binding_evidence,
)
from ai_test_asset_center.formal_evidence_projection import project_formal_evidence
from ai_test_asset_center.observer_contracts_base import validate_observer_receipt


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


def _behavior_ir(*, method: str = "GET") -> dict[str, Any]:
    return {
        "actors": [{
            "id": "actor_public",
            "role": "public",
            "status": "accepted",
        }],
        "operations": [{
            "id": "bir_op_read_event_trigger",
            "operation_id": "read_event_trigger",
            "source_operation_refs": ["read_event_trigger"],
            "method": method,
            "path": "/api/event-trigger",
            "raw_path": "/api/event-trigger",
            "request_example": {} if method != "GET" else None,
        }],
    }


def _experiment(*, method: str = "GET", governed_write: bool = False) -> dict[str, Any]:
    contract = _event_contract()
    identity = _binding_identity()
    return {
        "schema_version": "qualibug.experiment.v1",
        "experiment_id": "exp_formal_event_runtime",
        "obligation_id": "obl_formal_event_runtime",
        "risk_family": events.RISK_FAMILY,
        "protocol_id": f"{events.RISK_FAMILY}:{events.PROTOCOL_TEMPLATE}",
        "compile_receipt": {
            "status": "COMPILED",
            "reason_code": "",
            "detail": "source-grounded formal event test fixture",
        },
        "compiled_adapters": ["http_api", events.ADAPTER],
        "control_plan": [],
        "treatment_plan": [{
            "step_id": "treatment_1",
            "actor_ref": "actor_public",
            "operation_ref": "bir_op_read_event_trigger",
            "intent": "trigger_source_declared_event",
            "protocol_step": "event_trigger",
            **({"body": {"request_id": "request-1"}} if method != "GET" else {}),
        }],
        "observers": [{
            "observer_id": events.OBSERVER_ID,
            "surface": events.SURFACE,
            "adapter": events.ADAPTER,
        }],
        "assertions": [{
            "assertion_id": "assert_order_created_event",
            "kind": events.ASSERTION_KIND,
            "property": {
                "template": events.PROTOCOL_TEMPLATE,
                "invariant_ref": "inv_order_created_event",
                "actor_ref": "actor_public",
                "event_contract_id": contract["contract_id"],
                "event_contract": contract,
                "formal_event_binding_identity": identity,
            },
            "source_refs": [_source_ref()],
        }],
        "fixture_dag": {"nodes": [], "setup_order": []},
        "binding_plan": [],
        "cleanup_plan": [],
        "safety_contract": {
            "governed_write": governed_write,
            "cleanup_not_required": not governed_write,
        },
        "source_refs": [_source_ref()],
    }


def _runtime_contract() -> dict[str, Any]:
    return {
        "status": "approved",
        "approved_base_url": "https://sut.example.test",
        "declared_adapters": ["http_api", events.ADAPTER],
        "environment_kind": "test",
        "is_production": False,
    }


def _event(
    event_id: str,
    *,
    event_type: str = "OrderCreated",
    correlation: str = "order-1",
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "event_type": event_type,
        "correlation": correlation,
        "timestamp_present": True,
    }


_CASES = [
    pytest.param(
        "missing", [], True, [],
        "EVENT_DELIVERY_COUNT_BELOW_MINIMUM", "VIOLATION", True,
        id="missing-event",
    ),
    pytest.param(
        "above_max", [_event("evt-1"), _event("evt-2")], True, [],
        "EVENT_DELIVERY_COUNT_ABOVE_MAXIMUM", "VIOLATION", True,
        id="duplicate-unique-deliveries",
    ),
    pytest.param(
        "wrong_type", [_event("evt-1", event_type="OrderUpdated")], True, [],
        "EVENT_DELIVERY_TYPE_MISMATCH", "VIOLATION", True,
        id="wrong-event-type",
    ),
    pytest.param(
        "correlation_mismatch", [_event("evt-1", correlation="order-other")], True, [],
        "EVENT_DELIVERY_CORRELATION_MISMATCH", "VIOLATION", True,
        id="correlation-mismatch",
    ),
    pytest.param(
        "timeout", [_event("evt-1")], False, ["observation_timeout"],
        "EVENT_OBSERVATION_COVERAGE_INCOMPLETE", "INDETERMINATE", False,
        id="observation-timeout",
    ),
]


@pytest.mark.parametrize(
    (
        "case_name",
        "polled_events",
        "window_completed",
        "observer_errors",
        "expected_reason",
        "expected_oracle_status",
        "expect_finding",
    ),
    _CASES,
)
def test_formal_event_executor_to_oracle_finding_and_evidence_chain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case_name: str,
    polled_events: list[dict[str, Any]],
    window_completed: bool,
    observer_errors: list[str],
    expected_reason: str,
    expected_oracle_status: str,
    expect_finding: bool,
) -> None:
    def trigger_request(**kwargs: Any) -> dict[str, Any]:
        return {
            "method": kwargs["method"],
            "path": kwargs["path"],
            "status_code": 200,
            "body": {"id": "order-1", "triggered": True},
            "headers": {},
            "duration_ms": 3,
            "error": "",
        }

    def poll_authority(**_: Any) -> dict[str, Any]:
        return {
            "events": list(polled_events),
            "poll_count": 2,
            "successful_polls": 1,
            "status_codes": [200],
            "errors": list(observer_errors),
            "truncated": False,
            "observation_window_completed": window_completed,
        }

    monkeypatch.setattr(plan_executor, "_run_http_step", trigger_request)
    monkeypatch.setattr(
        events,
        "_qualibug_original_poll_before_event_total_count",
        poll_authority,
    )

    result = executor.execute_one_experiment(
        _experiment(),
        behavior_ir=_behavior_ir(),
        root=tmp_path,
        project="formal-event-runtime",
        base_url="https://sut.example.test",
        runtime_contract=_runtime_contract(),
        campaign_id="campaign-event-runtime",
        execution_id=f"execution-{case_name}",
        actor_tokens={},
    )

    assert len(result["steps"]) == 1
    assert result["steps"][0]["phase"] == "treatment"
    assert result["steps"][0]["status_code"] == 200

    event_receipts = [
        receipt
        for receipt in result["observer_receipts"]
        if receipt.get("observer_id") == events.OBSERVER_ID
    ]
    assert len(event_receipts) == 1
    receipt = validate_observer_receipt(event_receipts[0])
    evidence = receipt["evidence"][events.EVIDENCE_KEY]
    identity = receipt["evidence"]["formal_event_binding_identity"]

    assert evidence["observation_phase"] == "pre_cleanup"
    assert evidence["observed_total_count"] == len(polled_events)
    assert evidence["raw_event_payloads_included"] is False
    assert identity["status"] == "BOUND"
    assert identity["observer_binding_ref"] == "observer_binding_order_created"
    assert identity["runtime_plan_ref"] == "runtime_plan_order_created_event"

    if case_name == "correlation_mismatch":
        assert evidence["observed_total_count"] == 1
        assert evidence["observed_correlated_count"] == 0

    oracle = result["oracle_verdict"]
    assert oracle["status"] == expected_oracle_status
    assert len(oracle["assertions"]) == 1
    assert oracle["assertions"][0]["reason_code"] == expected_reason
    assert (result["finding"] is not None) is expect_finding
    assert result["finding_created"] is expect_finding
    if expect_finding:
        assert result["finding"]["category"] == events.ASSERTION_KIND
        assert result["finding"]["risk_family"] == events.RISK_FAMILY
        assert result["finding"]["oracle"]["status"] == "VIOLATION"
    else:
        assert result["finding_filter_reason"] == "oracle_indeterminate"

    projected = project_formal_event_binding_evidence(
        project_formal_evidence({
            "experiment_execution": {
                "results": {result["obligation_id"]: result},
            },
        })
    )
    assert projected["formal_event_binding_evidence_receipt"]["status"] == "PROJECTED"
    assert projected["formal_event_binding_evidence_receipt"]["new_findings_created"] == 0
    assert len(projected["evidence_graphs"]) == 1
    graph = projected["evidence_graphs"][0]
    assert graph["formal_event_binding_identity_projected"] is True
    assert graph["coverage"]["observer_receipt_count"] == 1
    assert graph["coverage"]["oracle_present"] is True
    node_types = {node["node_type"] for node in graph["nodes"]}
    assert {
        "experiment_execution",
        "observer_receipt",
        "contract_oracle_verdict",
        "implementation_binding",
        "action_surface_binding",
        "observer_binding",
        "runtime_plan",
        "runtime_materialization",
    }.issubset(node_types)
