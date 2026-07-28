from __future__ import annotations

import json

import pytest

from ai_test_asset_center import behavior_ir as bir
from ai_test_asset_center import formal_event_surface as events
from ai_test_asset_center.formal_event_pre_cleanup import (
    _pre_observe_event,
    install_formal_event_pre_cleanup_observer,
)
from ai_test_asset_center.scan_event_contract_external_signal import (
    overlay_scan_event_contracts_with_external_signals,
)
from ai_test_asset_center.source_event_contract_binding import (
    bind_source_event_contracts,
)
from ai_test_asset_center.source_event_obligation_binding import (
    compile_obligations_with_source_event,
)


def _contract() -> dict:
    return {
        "schema_version": "qualibug.formal-event-contract.v1",
        "signal_type": "formal_event_contract",
        "contract_id": "event_order_created_once",
        "title": "Order creation emits one OrderCreated event",
        "source_refs": [{
            "source_id": "prd_orders_v1",
            "version": "1",
            "locator": "section=events;table=1;row=2",
            "kind": "formal_event_contract",
            "quote_hash": "abc123",
        }],
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


def _source_ref(source_id: str, locator: str, kind: str) -> dict:
    return {
        "source_id": source_id,
        "version": "1",
        "locator": locator,
        "kind": kind,
        "quote_hash": "",
    }


def _ir() -> dict:
    model = bir.empty_behavior_ir(project_id="events-project", source_snapshot_hash="source-hash")
    create = bir._fact_node(
        node_id="bir_op_create_order",
        typed_fields={
            "operation_id": "create_order",
            "service": "orders",
            "method": "POST",
            "path": "/api/orders",
            "request_schema": {},
            "request_example": {"sku": "SKU-1", "quantity": 1},
            "response_schema": {},
            "parameters": [],
            "field_dictionary": [],
            "security": [],
            "summary": "Create order",
            "description": "",
            "tags": ["orders"],
            "side_effect_class": "write",
            "read_write": "write",
            "entity_refs": [],
            "affected_fields": [],
            "examples": [],
            "source_operation_refs": ["create_order"],
        },
        source_refs=[_source_ref("api_orders_v1", "POST /api/orders", "api_operation")],
        confidence=1.0,
        derivation="explicit",
        status="accepted",
    )
    delete = bir._fact_node(
        node_id="bir_op_delete_order",
        typed_fields={
            **{key: value for key, value in create.items() if key not in {
                "id", "source_refs", "confidence", "derivation", "status"
            }},
            "operation_id": "delete_order",
            "method": "DELETE",
            "path": "/api/orders/{id}",
            "request_example": {},
            "summary": "Delete order",
            "source_operation_refs": ["delete_order"],
        },
        source_refs=[_source_ref("api_orders_v1", "DELETE /api/orders/{id}", "api_operation")],
        confidence=1.0,
        derivation="explicit",
        status="accepted",
    )
    actor = bir._fact_node(
        node_id="actor_admin",
        typed_fields={
            "role": "admin",
            "role_key": "admin",
            "account_ref": "admin@example.test",
            "tenant_scope": "all",
            "credential_secret_ref": "secret_ref:test_accounts:admin@example.test",
            "account_status": "active",
            "allowed_resources": ["orders"],
            "allowed_actions": ["create", "delete"],
            "denied_actions": [],
            "runtime_bound": True,
        },
        source_refs=[_source_ref("runtime_actors", "admin", "runtime_actor")],
        confidence=1.0,
        derivation="runtime-observed",
        status="accepted",
    )
    compensation = bir._relation_node(
        relation_type="compensates",
        from_ref="bir_op_delete_order",
        to_ref="bir_op_create_order",
        operation_ref="bir_op_delete_order",
        actor_ref="actor_admin",
        preconditions=[],
        effects=[],
        source_refs=[_source_ref("api_orders_v1", "cleanup:create_order", "cleanup")],
        confidence=1.0,
        derivation="explicit",
        status="accepted",
    )
    model["operations"] = [create, delete]
    model["actors"] = [actor]
    model["relations"] = [compensation]
    model["model_id"] = bir._content_addressed_id(model)
    assert bir.validate_behavior_ir(model, require_explicit_relations=True) == []
    return model


def test_event_surface_registers_all_formal_links_and_static_adapter() -> None:
    installed = events.install_formal_event_surface()

    from ai_test_asset_center.adapter_capability import (
        ADAPTER_TO_CAPABILITY,
        ADAPTER_TO_OBSERVATION_SURFACE,
        DECLARATION_REQUIRED,
    )
    from ai_test_asset_center.assertion_dsl_base import registered_assertion_kinds
    from ai_test_asset_center.experiment_protocol_registry import registered_family_protocols
    from ai_test_asset_center.observer_contracts_base import OBSERVER_REGISTRY
    from ai_test_asset_center.test_obligation import canonical_risk_families

    assert installed["observer"] == events.OBSERVER_ID
    assert OBSERVER_REGISTRY[events.OBSERVER_ID]["adapter"] == events.ADAPTER
    assert events.ASSERTION_KIND in registered_assertion_kinds()
    assert events.RISK_FAMILY in canonical_risk_families()
    assert f"{events.RISK_FAMILY}:{events.PROTOCOL_TEMPLATE}" in registered_family_protocols()
    assert DECLARATION_REQUIRED[events.ADAPTER] == "runtime_contract.declared_adapters[]"
    assert ADAPTER_TO_OBSERVATION_SURFACE[events.ADAPTER] == "event_stream"
    assert ADAPTER_TO_CAPABILITY[events.ADAPTER] == "event_stream_read"


def test_only_explicitly_typed_external_signals_enter_event_overlay() -> None:
    ordinary = {
        "signal_type": "webhook_probe",
        "contract_id": "ordinary_signal",
        "source_refs": _contract()["source_refs"],
    }
    merged, receipt = overlay_scan_event_contracts_with_external_signals(
        {},
        campaign_context={
            "external_signal_requests": [ordinary, _contract()],
        },
    )

    assert [row["contract_id"] for row in merged["event_formal_contracts"]] == [
        "event_order_created_once"
    ]
    assert receipt["external_signal_request_count"] == 2
    assert receipt["typed_external_event_contract_count"] == 1
    assert receipt["contract_added_count"] == 1


def test_source_event_contract_binds_exact_operation_actor_and_relation() -> None:
    bound, receipt = bind_source_event_contracts(
        _ir(),
        {"event_formal_contracts": [_contract()]},
    )

    assert receipt["status"] == "BOUND"
    invariant = next(
        row for row in bound["invariants"]
        if row.get("event_contract_id") == "event_order_created_once"
    )
    assert invariant["operation_refs"] == ["bir_op_create_order"]
    assert invariant["event_actor_ref"] == "actor_admin"
    assert invariant["expression"]["kind"] == "event_delivery_contract"
    relation = next(
        row for row in bound["relations"]
        if row.get("to_ref") == invariant["id"]
    )
    assert relation["relation_type"] == "produces"
    assert relation["operation_ref"] == "bir_op_create_order"


def test_event_obligation_inherits_existing_cleanup_contract() -> None:
    events.install_formal_event_surface()
    bound, _ = bind_source_event_contracts(
        _ir(),
        {"event_formal_contracts": [_contract()]},
    )
    result = compile_obligations_with_source_event(
        bound,
        base_compile=lambda _model: {
            "schema_version": "qualibug.test-obligation-pack.v1",
            "obligations": [],
            "obligation_count": 0,
            "coverage_gaps": [],
            "by_family": {},
        },
    )

    rows = [
        row for row in result["obligations"]
        if row["risk_family"] == events.RISK_FAMILY
    ]
    assert len(rows) == 1
    obligation = rows[0]
    assert obligation["required_operations"] == ["bir_op_create_order"]
    assert obligation["required_actors"] == ["actor_admin"]
    assert obligation["required_observers"] == [events.OBSERVER_ID]
    assert obligation["property"]["template"] == events.PROTOCOL_TEMPLATE
    assert obligation["cleanup_requirement"]["required"] is True
    assert obligation["cleanup_requirement"]["operation_ref"] == "bir_op_delete_order"
    assert result["by_family"][events.RISK_FAMILY] == 1


def test_event_protocol_requires_complete_source_contract() -> None:
    compiled = events._compile_event_protocol({
        "property_spec": {
            "invariant_ref": "bir_event_inv",
            "actor_ref": "actor_admin",
            "event_contract": _contract(),
        },
        "operation": _ir()["operations"][0],
        "operation_ref": "bir_op_create_order",
        "treatment_actor_ref": "actor_admin",
    })
    assert compiled["status"] == "COMPILED"
    assert compiled["observers"] == [{"observer_id": events.OBSERVER_ID}]
    assert compiled["assertion"]["kind"] == events.ASSERTION_KIND
    assert compiled["treatment_plan"][0]["body"] == {
        "sku": "SKU-1",
        "quantity": 1,
    }

    invalid = _contract()
    invalid.pop("event_id_field")
    blocked = events._compile_event_protocol({
        "property_spec": {"event_contract": invalid},
        "operation": _ir()["operations"][0],
        "operation_ref": "bir_op_create_order",
        "treatment_actor_ref": "actor_admin",
    })
    assert blocked["status"] == "BLOCKED"
    assert blocked["reason_code"] == "BLOCKED_MISSING_BINDING"


def test_full_window_exactly_once_observation_is_receipted_without_raw_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(events, "_observer_token", lambda **_kwargs: "")
    monkeypatch.setattr(events, "_poll_event_endpoint", lambda **_kwargs: {
        "events": [{
            "event_id": "evt-secret-1",
            "event_type": "OrderCreated",
            "correlation": "order-secret-123",
            "timestamp_present": True,
        }],
        "poll_count": 3,
        "successful_polls": 3,
        "status_codes": [200, 200, 200],
        "errors": [],
        "truncated": False,
        "observation_window_completed": True,
    })
    exp = {
        "experiment_id": "exp_event_1",
        "execution_id": "exec_event_1",
        "_observer_runtime_context": {
            "root": "/tmp/qualibug",
            "project": "events-project",
            "runtime_contract": {
                "status": "approved",
                "approved_base_url": "http://localhost:8080",
                "declared_adapters": [events.ADAPTER],
            },
        },
    }
    assertion = {
        "kind": events.ASSERTION_KIND,
        "property": {
            "actor_ref": "actor_admin",
            "event_contract": _contract(),
        },
    }
    observations = {
        "treatment_observation": {
            "body": {"id": "order-secret-123"},
        },
    }
    receipt = events._event_observer_handler({
        "experiment": exp,
        "assertion": assertion,
        "property": assertion["property"],
        "observations": observations,
        "execution_id": "exec_event_1",
    })

    assert receipt["status"] == "OBSERVED"
    evidence = receipt["evidence"][events.EVIDENCE_KEY]
    assert evidence["observed_correlated_count"] == 1
    assert evidence["coverage_complete"] is True
    assert evidence["raw_event_payloads_included"] is False
    serialized = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
    assert "evt-secret-1" not in serialized
    assert "order-secret-123" not in serialized

    verdict = events._evaluate_event_delivery({
        "observations": {events.EVIDENCE_KEY: evidence},
    })
    assert verdict["passed"] is True

    duplicate = dict(evidence)
    duplicate["observed_correlated_count"] = 2
    duplicate["event_id_fingerprints"] = ["one", "two"]
    duplicate_verdict = events._evaluate_event_delivery({
        "observations": {events.EVIDENCE_KEY: duplicate},
    })
    assert duplicate_verdict["passed"] is False
    assert duplicate_verdict["actual"]["count"] == 2


def test_incomplete_event_observation_never_reports_pass() -> None:
    verdict = events._evaluate_event_delivery({
        "observations": {
            events.EVIDENCE_KEY: {
                "expected_event_type": "OrderCreated",
                "expected_min_count": 1,
                "expected_max_count": 1,
                "observed_correlated_count": 1,
                "observed_event_types": ["OrderCreated"],
                "mismatched_event_types": [],
                "observation_window_completed": False,
                "coverage_complete": False,
            }
        },
    })
    assert verdict["passed"] is None
    assert verdict["reason_code"] == "EVENT_OBSERVATION_COVERAGE_INCOMPLETE"


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
    assert observations[events.EVIDENCE_KEY]["observation_phase"] == "pre_cleanup"

    reused = _REGISTERED_OBSERVER_HANDLERS[events.OBSERVER_ID]({
        "experiment": exp,
        "observations": observations,
        "assertion": exp["assertions"][0],
    })
    assert calls == ["observe"], "Finalizer must not poll the event endpoint a second time"
    assert reused["receipt_id"] == receipt["receipt_id"]
