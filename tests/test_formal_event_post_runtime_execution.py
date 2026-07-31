from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

# Installs the registered Event protocol, pre-cleanup observer and receipt bridges
# on the canonical experiment mainline before compilation/execution.
from ai_test_asset_center import discovery_runtime as _runtime_install  # noqa: F401
from ai_test_asset_center import behavior_ir as bir
from ai_test_asset_center import experiment_cleanup_executor as cleanup_executor
from ai_test_asset_center import experiment_executor as executor
from ai_test_asset_center import experiment_plan_executor as plan_executor
from ai_test_asset_center import experiment_runtime_support as runtime_support
from ai_test_asset_center import formal_event_surface as events
from ai_test_asset_center import sandbox_write_executor as sandbox
from ai_test_asset_center import sandbox_write_executor_base as sandbox_base
from ai_test_asset_center.experiment_compiler import compile_experiment_for_obligation
from ai_test_asset_center.observer_contracts_base import validate_observer_receipt
from ai_test_asset_center.source_event_contract_binding import bind_source_event_contracts
from ai_test_asset_center.source_event_obligation_binding import (
    compile_obligations_with_source_event,
)


def _source_ref(source_id: str, locator: str, kind: str) -> dict[str, str]:
    return {
        "source_id": source_id,
        "version": "1",
        "locator": locator,
        "kind": kind,
        "quote_hash": "event-post-runtime-source",
    }


def _event_contract() -> dict[str, Any]:
    return {
        "schema_version": "qualibug.formal-event-contract.v1",
        "signal_type": "formal_event_contract",
        "contract_id": "event_order_created_once_post",
        "title": "Order creation emits exactly one OrderCreated event",
        "source_refs": [
            _source_ref(
                "prd_orders_v1",
                "section=events;table=1;row=2",
                "formal_event_contract",
            )
        ],
        "operation_ref": "create_order",
        "actor_ref": "actor_admin",
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
        "trigger_body": {"sku": "SKU-1", "quantity": 1},
        "observer_requires_actor_token": False,
    }


def _operation(
    *,
    node_id: str,
    operation_id: str,
    method: str,
    path: str,
    request_example: dict[str, Any] | None = None,
) -> dict[str, Any]:
    write = method in {"POST", "PUT", "PATCH", "DELETE"}
    return bir._fact_node(
        node_id=node_id,
        typed_fields={
            "operation_id": operation_id,
            "service": "orders",
            "method": method,
            "path": path,
            "request_schema": {},
            "request_example": request_example or {},
            "response_schema": {},
            "parameters": [],
            "field_dictionary": [],
            "security": [],
            "summary": operation_id,
            "description": "",
            "tags": ["orders"],
            "side_effect_class": "write" if write else "read",
            "read_write": "write" if write else "read",
            "entity_refs": [],
            "affected_fields": [],
            "examples": [],
            "source_operation_refs": [operation_id],
        },
        source_refs=[
            _source_ref(
                "api_orders_v1",
                f"{method} {path}",
                "api_operation",
            )
        ],
        confidence=1.0,
        derivation="explicit",
        status="accepted",
    )


def _behavior_ir() -> dict[str, Any]:
    model = bir.empty_behavior_ir(
        project_id="formal-event-post-runtime",
        source_snapshot_hash="source-hash-event-post-runtime",
    )
    create = _operation(
        node_id="bir_op_create_order_post",
        operation_id="create_order",
        method="POST",
        path="/api/orders",
        request_example={"sku": "SKU-1", "quantity": 1},
    )
    list_orders = _operation(
        node_id="bir_op_list_orders_post",
        operation_id="list_orders",
        method="GET",
        path="/api/orders",
    )
    get_order = _operation(
        node_id="bir_op_get_order_post",
        operation_id="get_order",
        method="GET",
        path="/api/orders/{id}",
    )
    delete = _operation(
        node_id="bir_op_delete_order_post",
        operation_id="delete_order",
        method="DELETE",
        path="/api/orders/{id}",
    )
    actor = bir._fact_node(
        node_id="actor_admin",
        typed_fields={
            "role": "admin",
            "role_key": "admin",
            "account_ref": "admin@example.test",
            "tenant_scope": "all",
            "credential_secret_ref": (
                "secret_ref:test_accounts:admin@example.test"
            ),
            "account_status": "active",
            "allowed_resources": ["orders"],
            "allowed_actions": ["create", "read", "delete"],
            "denied_actions": [],
            "runtime_bound": True,
        },
        source_refs=[
            _source_ref("runtime_actors", "admin", "runtime_actor")
        ],
        confidence=1.0,
        derivation="runtime-observed",
        status="accepted",
    )
    compensation = bir._relation_node(
        relation_type="compensates",
        from_ref="bir_op_delete_order_post",
        to_ref="bir_op_create_order_post",
        operation_ref="bir_op_delete_order_post",
        actor_ref="actor_admin",
        preconditions=[],
        effects=[],
        source_refs=[
            _source_ref(
                "api_orders_v1",
                "cleanup:create_order",
                "cleanup",
            )
        ],
        confidence=1.0,
        derivation="explicit",
        status="accepted",
    )
    model["operations"] = [create, list_orders, get_order, delete]
    model["actors"] = [actor]
    model["relations"] = [compensation]
    model["model_id"] = bir._content_addressed_id(model)
    assert bir.validate_behavior_ir(
        model,
        require_explicit_relations=True,
    ) == []
    return model


def _compile_event_experiment() -> tuple[dict[str, Any], dict[str, Any]]:
    model, binding_receipt = bind_source_event_contracts(
        _behavior_ir(),
        {"event_formal_contracts": [_event_contract()]},
    )
    assert binding_receipt["status"] == "BOUND"
    obligations = compile_obligations_with_source_event(
        model,
        base_compile=lambda _model: {
            "schema_version": "qualibug.test-obligation-pack.v1",
            "obligations": [],
            "obligation_count": 0,
            "coverage_gaps": [],
            "by_family": {},
        },
    )
    event_rows = [
        row
        for row in obligations["obligations"]
        if row.get("risk_family") == events.RISK_FAMILY
    ]
    assert len(event_rows) == 1
    experiment = compile_experiment_for_obligation(
        event_rows[0],
        behavior_ir=model,
        environment_type="test",
        policy_version="event-post-runtime-test",
        available_adapters={"http_api", events.ADAPTER},
    )
    assert experiment["compile_receipt"]["status"] == "COMPILED"
    assert experiment["safety_contract"]["governed_write"] is True
    assert experiment["write_reversibility_proof"]["proof_status"] == "PROVEN"
    assert any(
        row.get("operation_ref") == "bir_op_delete_order_post"
        for row in experiment["cleanup_plan"]
    )
    assert any(
        row.get("observer_id") == events.OBSERVER_ID
        for row in experiment["observers"]
    )
    assert not experiment.get("disposable_fixture_contract")
    assert not (experiment.get("fixture_dag") or {}).get("nodes")
    return model, experiment


def test_compiled_post_event_runs_before_cleanup_and_restores_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model, experiment = _compile_event_experiment()
    state = {"created": False}
    calls: list[str] = []

    def fake_http(
        method: str,
        url: str,
        *,
        token: str = "",
        body: Any = None,
        **_: Any,
    ) -> dict[str, Any]:
        del token
        path = urlparse(url).path
        verb = str(method).upper()
        calls.append(f"{verb} {path}")
        if verb == "GET" and path == "/api/orders":
            rows = (
                [{"id": "order-1", "sku": "SKU-1", "quantity": 1}]
                if state["created"]
                else []
            )
            return {
                "status": 200,
                "body": {"items": rows},
                "headers": {},
                "duration_ms": 1,
                "error": "",
            }
        if verb == "POST" and path == "/api/orders":
            assert body == {"sku": "SKU-1", "quantity": 1}
            state["created"] = True
            return {
                "status": 201,
                "body": {
                    "id": "order-1",
                    "sku": "SKU-1",
                    "quantity": 1,
                },
                "headers": {},
                "duration_ms": 2,
                "error": "",
            }
        if verb == "GET" and path == "/api/orders/order-1":
            return {
                "status": 200 if state["created"] else 404,
                "body": (
                    {"id": "order-1", "sku": "SKU-1", "quantity": 1}
                    if state["created"]
                    else {}
                ),
                "headers": {},
                "duration_ms": 1,
                "error": "",
            }
        if verb == "DELETE" and path == "/api/orders/order-1":
            state["created"] = False
            return {
                "status": 204,
                "body": {},
                "headers": {},
                "duration_ms": 1,
                "error": "",
            }
        raise AssertionError(f"unexpected transport call: {verb} {path}")

    def event_poll(**_: Any) -> dict[str, Any]:
        calls.append("EVENT_POLL")
        return {
            "events": [],
            "poll_count": 2,
            "successful_polls": 2,
            "status_codes": [200, 200],
            "errors": [],
            "truncated": False,
            "observation_window_completed": True,
        }

    def allow_sandbox(**_: Any) -> tuple[bool, str]:
        return True, ""

    for module in (
        sandbox,
        sandbox_base,
        plan_executor,
        cleanup_executor,
        runtime_support,
        executor,
    ):
        monkeypatch.setattr(
            module,
            "sandbox_write_allowed",
            allow_sandbox,
            raising=False,
        )
        monkeypatch.setattr(
            module,
            "_http_request",
            fake_http,
            raising=False,
        )
    monkeypatch.setattr(
        events,
        "_qualibug_original_poll_before_event_total_count",
        event_poll,
    )

    result = executor.execute_one_experiment(
        experiment,
        behavior_ir=model,
        root=tmp_path,
        project="formal-event-post-runtime",
        base_url="https://sut.example.test",
        runtime_contract={
            "status": "approved",
            "environment_type": "test",
            "environment_kind": "test",
            "environment_ref": "event-post-test",
            "execution_mode": "approved_sandbox_write",
            "approved_base_url": "https://sut.example.test",
            "declared_adapters": ["http_api", events.ADAPTER],
            "is_production": False,
        },
        campaign_id="campaign-event-post-runtime",
        execution_id="execution-event-post-runtime",
        actor_tokens={
            "secret_ref:test_accounts:admin@example.test": "token-admin",
        },
    )

    assert state["created"] is False
    post_index = calls.index("POST /api/orders")
    event_index = calls.index("EVENT_POLL")
    delete_index = calls.index("DELETE /api/orders/order-1")
    assert post_index < event_index < delete_index

    event_receipts = [
        validate_observer_receipt(row)
        for row in result["observer_receipts"]
        if row.get("observer_id") == events.OBSERVER_ID
    ]
    assert len(event_receipts) == 1
    event_receipt = event_receipts[0]
    assert event_receipt["evidence"]["step_id"] == "treatment_1"
    event_evidence = event_receipt["evidence"][events.EVIDENCE_KEY]
    assert event_evidence["observation_phase"] == "pre_cleanup"
    assert event_evidence["observed_total_count"] == 0
    assert event_evidence["coverage_complete"] is True

    assert result["oracle_verdict"]["status"] == "VIOLATION"
    assert result["oracle_verdict"]["assertions"][0]["reason_code"] == (
        "EVENT_DELIVERY_COUNT_BELOW_MINIMUM"
    )
    equivalence = result["cleanup_equivalence_receipt"]
    assert equivalence["equivalence_status"] == "EQUIVALENT"
    assert result["environment_restored"] is True

    bundle = result["execution_receipt_bundle"]
    assert bundle["fixture_id"] == "NOT_APPLICABLE"
    assert bundle["fixture_provenance_receipt_ids"] == []
    assert bundle["complete"] is True
    assert bundle["validation_errors"] == []
    process_audit = bundle["process_step_audit"]
    assert process_audit["complete"] is True
    assert process_audit["step_evidence_scopes_complete"] is True
    scope = process_audit["evidence_scope_audit"]
    assert scope["complete"] is True
    assert scope["unbound_receipt_ids"] == []
    assert scope["broadcast_receipt_ids"] == []
    for key in (
        "observation",
        "oracle_invocation",
        "oracle_trace",
        "cleanup_execution",
        "cleanup_verification",
    ):
        owners = set(scope[key]["exact_owner_by_receipt"].values())
        assert owners == {"treatment_1"}

    finalization = result["execution_finalization_receipt"]
    assert finalization["true_completed"] is True
    assert finalization["derived_terminal_status"] == "TRUE_COMPLETED"
    assert result["lifecycle_state"] == "TRUE_COMPLETED"

    assert result["finding"] is not None
    assert result["finding"]["risk_family"] == events.RISK_FAMILY
    assert result["finding"]["category"] == events.ASSERTION_KIND
    assert result["finding"]["oracle"]["status"] == "VIOLATION"
