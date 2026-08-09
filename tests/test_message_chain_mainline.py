"""Message-chain delivery consistency: four-link chain verification tests.

Covers the minimal offline chain only -- no scan, no full pytest suite:
* contract normalization and overlay admission (source-bound chains + runtime
  event surfaces as the degradation channel)
* Behavior IR binding (invariant + produces relation)
* obligation compilation into the registered message_chain_verification
  protocol and experiment compile (planning-level offline verification)
* observer evidence (duplicate delivery detection, ordering, chain-effect
  state readback) and evaluator verdicts
* pre-cleanup observation so cleanup cannot contaminate the chain window
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

# Imports the public runtime: installs the formal event surface AND the
# message-chain surface (observer + assertion kind + protocol + pre-cleanup).
from ai_test_asset_center import discovery_runtime as _runtime_install  # noqa: F401
from ai_test_asset_center import behavior_ir as bir
from ai_test_asset_center import formal_event_surface as events
from ai_test_asset_center import message_chain_surface as chain
from ai_test_asset_center import sandbox_write_executor as sandbox
from ai_test_asset_center.experiment_compiler import compile_experiment_for_obligation
from ai_test_asset_center.experiment_protocol_registry import (
    resolve_family_protocol,
    validate_registered_protocol_result,
)
from ai_test_asset_center.message_chain_binding import (
    bind_source_message_chains,
    compile_obligations_with_message_chain,
)
from ai_test_asset_center.message_chain_contract_overlay import (
    normalize_message_chain_contract,
    normalize_runtime_event_surface,
    overlay_message_chain_contracts_with_external_signals,
)
from ai_test_asset_center.observer_contracts_base import validate_observer_receipt


def _source_ref(source_id: str, locator: str) -> dict[str, str]:
    return {
        "source_id": source_id,
        "version": "1",
        "locator": locator,
        "kind": "formal_message_chain_contract",
        "quote_hash": "message-chain-source-hash",
    }


def _chain_contract(**overrides: Any) -> dict[str, Any]:
    row = {
        "schema_version": "qualibug.formal-message-chain-contract.v1",
        "signal_type": "message_chain_contract",
        "contract_id": "chain_payment_order_status",
        "title": "Payment callback advances order status to PAID",
        "event_name": "PaymentSucceeded",
        "source_refs": [_source_ref("prd_payments_v1", "section=events;row=1")],
        "operation_ref": "payment_callback",
        "actor_ref": "actor_gateway",
        "observer_path": "/test-observers/events",
        "events_path": "items",
        "event_id_field": "event_id",
        "event_type_field": "event_type",
        "correlation_field": "order_id",
        "correlation_query_parameter": "order_id",
        "correlation_source": {
            "location": "treatment_response",
            "path": "id",
        },
        "expected_min_count": 1,
        "expected_max_count": 1,
        "observation_window_ms": 2000,
        "poll_interval_ms": 100,
        "sequence_field": "seq",
        "timestamp_field": "occurred_at",
        "trigger_body": {"payment_id": "pay-1"},
        "consumers": [
            {
                "consumer_ref": "order-service",
                "effect": {
                    "surface": "http_state",
                    "readback": {
                        "path": "/api/orders/{correlation}/status",
                        "state_field": "status",
                        "expected_state": "PAID",
                        "previous_state": "PENDING",
                        "poll_until_ms": 2000,
                        "poll_interval_ms": 100,
                    },
                },
            }
        ],
        "ordering": {"expected_types": ["OrderCreated", "PaymentSucceeded"]},
    }
    row.update(overrides)
    return row


def _runtime_surface(**overrides: Any) -> dict[str, Any]:
    row = {
        "schema_version": "qualibug.runtime-event-surface.v1",
        "signal_type": "runtime_event_surface",
        "surface_id": "runtime_events_payments",
        "title": "Runtime event surface for payment triggers",
        "operation_ref": "payment_callback",
        "actor_ref": "actor_gateway",
        "observer_path": "/test-observers/events",
        "events_path": "items",
        "event_id_field": "event_id",
        "event_type_field": "event_type",
        "correlation_field": "order_id",
        "correlation_query_parameter": "order_id",
        "correlation_source": {
            "location": "treatment_response",
            "path": "id",
        },
        "expected_min_count": 1,
        "observation_window_ms": 2000,
        "poll_interval_ms": 100,
        "sequence_field": "seq",
        "consumers": [],
    }
    row.update(overrides)
    return row


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
            "service": "payments",
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
            "tags": ["payments"],
            "side_effect_class": "write" if write else "read",
            "read_write": "write" if write else "read",
            "entity_refs": [],
            "affected_fields": [],
            "examples": [],
            "source_operation_refs": [operation_id],
        },
        source_refs=[_source_ref("api_payments_v1", f"{method} {path}")],
        confidence=1.0,
        derivation="explicit",
        status="accepted",
    )


def _behavior_ir() -> dict[str, Any]:
    model = bir.empty_behavior_ir(
        project_id="message-chain-test",
        source_snapshot_hash="source-hash-message-chain",
    )
    callback = _operation(
        node_id="bir_op_payment_callback",
        operation_id="payment_callback",
        method="POST",
        path="/api/payments/callback",
        request_example={"payment_id": "pay-1"},
    )
    read_order = _operation(
        node_id="bir_op_read_order",
        operation_id="read_order",
        method="GET",
        path="/api/orders/{id}",
    )
    actor = bir._fact_node(
        node_id="actor_gateway",
        typed_fields={
            "role": "service",
            "role_key": "service",
            "account_ref": "gateway@example.test",
            "credential_secret_ref": "secret_ref:test_accounts:gateway@example.test",
            "account_status": "active",
            "runtime_bound": True,
        },
        source_refs=[_source_ref("runtime_actors", "gateway")],
        confidence=1.0,
        derivation="runtime-observed",
        status="accepted",
    )
    model["operations"] = [callback, read_order]
    model["actors"] = [actor]
    model["model_id"] = bir._content_addressed_id(model)
    assert bir.validate_behavior_ir(model, require_explicit_relations=True) == []
    return model


# ── 1. Contract normalization (event contract declaration) ──────────────────


def test_chain_contract_normalization_accepts_full_contract() -> None:
    contract, gaps = normalize_message_chain_contract(
        _chain_contract(),
        index=1,
    )
    assert gaps == []
    assert contract is not None
    assert contract["schema_version"] == "qualibug.formal-message-chain-contract.v1"
    assert contract["status"] == "accepted"
    assert contract["derivation"] == "explicit"
    assert contract["channel"] == "source_contract"
    assert contract["event_name"] == "PaymentSucceeded"
    assert contract["expected_min_count"] == 1
    assert contract["consumers"][0]["readback"]["expected_state"] == "PAID"
    assert contract["ordering"]["expected_types"] == [
        "OrderCreated",
        "PaymentSucceeded",
    ]


def test_chain_contract_requires_source_refs() -> None:
    contract, gaps = normalize_message_chain_contract(
        _chain_contract(source_refs=[]),
        index=1,
    )
    assert contract is None
    assert gaps[0]["reason_code"] == "FORMAL_CHAIN_SOURCE_REF_MISSING"


def test_chain_contract_requires_effect_readback() -> None:
    contract, gaps = normalize_message_chain_contract(
        _chain_contract(consumers=[{"consumer_ref": "order-service", "effect": {}}]),
        index=1,
    )
    assert contract is None
    assert gaps[0]["reason_code"] == "FORMAL_CHAIN_EFFECT_READBACK_MISSING"


def test_chain_contract_rejects_empty_ordering_declaration() -> None:
    contract, gaps = normalize_message_chain_contract(
        _chain_contract(ordering={"expected_types": []}),
        index=1,
    )
    assert contract is None
    assert gaps[0]["reason_code"] == "FORMAL_CHAIN_ORDERING_SPEC_MISSING"


def test_runtime_surface_normalization_needs_no_source_refs() -> None:
    surface, gaps = normalize_runtime_event_surface(_runtime_surface(), index=1)
    assert gaps == []
    assert surface is not None
    assert surface["schema_version"] == "qualibug.runtime-event-surface.v1"
    assert surface["derivation"] == "runtime-observed"
    assert surface["channel"] == "runtime_observation"
    assert surface["expected_max_count"] is None


# ── 2. Overlay admission ────────────────────────────────────────────────────


def test_overlay_admits_typed_external_signal_rows() -> None:
    asset: dict[str, Any] = {"coverage_gaps": []}
    context = {
        "external_signal_requests": [
            {
                "signal_type": "message_chain_contract",
                **{k: v for k, v in _chain_contract().items() if k != "signal_type"},
            },
            {
                "signal_type": "runtime_event_surface",
                **{k: v for k, v in _runtime_surface().items() if k != "signal_type"},
            },
        ]
    }
    merged, receipt = overlay_message_chain_contracts_with_external_signals(
        asset,
        campaign_context=context,
    )
    assert receipt["status"] == "OVERLAID"
    assert receipt["message_chain_admitted_count"] == 1
    assert receipt["runtime_event_surface_admitted_count"] == 1
    assert len(merged["message_chain_contracts"]) == 1
    assert len(merged["runtime_event_surfaces"]) == 1
    assert merged["coverage_gaps"] == []


def test_overlay_rejects_duplicate_chain_contract_id() -> None:
    raw = _chain_contract()
    context = {
        "message_chain_contracts": [dict(raw), dict(raw)],
    }
    merged, receipt = overlay_message_chain_contracts_with_external_signals(
        {"coverage_gaps": []},
        campaign_context=context,
    )
    assert receipt["message_chain_admitted_count"] == 1
    assert any(
        gap["reason_code"] == "FORMAL_CHAIN_CONTRACT_ID_DUPLICATE"
        for gap in merged["coverage_gaps"]
    )


# ── 3. Behavior IR binding ──────────────────────────────────────────────────


def test_bind_source_message_chains_creates_invariant_and_relation() -> None:
    model, receipt = bind_source_message_chains(
        _behavior_ir(),
        {"message_chain_contracts": [_chain_contract()]},
    )
    assert receipt["status"] == "BOUND"
    assert receipt["bound_invariant_count"] == 1
    invariants = [
        row
        for row in model["invariants"]
        if row.get("message_chain_contract_id") == "chain_payment_order_status"
    ]
    assert len(invariants) == 1
    invariant = invariants[0]
    assert invariant["expression"]["kind"] == "message_chain_consistency"
    assert invariant["event_actor_ref"] == "actor_gateway"
    assert invariant["message_chain"]["event_name"] == "PaymentSucceeded"
    relations = [
        row
        for row in model["relations"]
        if row.get("relation_type") == "produces"
        and row.get("to_ref") == invariant["id"]
    ]
    assert len(relations) == 1
    assert relations[0]["operation_ref"] == "bir_op_payment_callback"
    assert relations[0]["effects"][0]["kind"] == "message_chain_delivery"


def test_bind_source_message_chains_records_unresolved_operation_gap() -> None:
    contract = _chain_contract(operation_ref="missing_operation")
    model, receipt = bind_source_message_chains(
        _behavior_ir(),
        {"message_chain_contracts": [contract]},
    )
    assert receipt["status"] == "BLOCKED"
    assert receipt["coverage_gap_count"] == 1
    gap = model["coverage_gaps"][-1]
    assert gap["gap_type"] == "message_chain_contract_not_executable"
    assert gap["reason_code"] == "FORMAL_EVENT_OPERATION_NOT_FOUND"


def test_bind_source_message_chains_binds_runtime_surface_implicitly() -> None:
    model, receipt = bind_source_message_chains(
        _behavior_ir(),
        {"runtime_event_surfaces": [_runtime_surface()]},
    )
    assert receipt["status"] == "BOUND"
    assert receipt["runtime_observation_surface_count"] == 1
    invariant = [
        row
        for row in model["invariants"]
        if row.get("message_chain_contract_id") == "runtime_events_payments"
    ][0]
    assert invariant["derivation"] == "runtime-observed"
    assert invariant["message_chain"]["channel"] == "runtime_observation"


# ── 4. Obligation compilation + experiment compile (planning-level) ─────────


def _stub_base() -> dict[str, Any]:
    return {
        "schema_version": "qualibug.test-obligation-pack.v1",
        "obligations": [],
        "obligation_count": 0,
        "coverage_gaps": [],
        "by_family": {},
    }


def _compiled_chain_obligation() -> tuple[dict[str, Any], dict[str, Any]]:
    model, binding_receipt = bind_source_message_chains(
        _behavior_ir(),
        {"message_chain_contracts": [_chain_contract()]},
    )
    assert binding_receipt["status"] == "BOUND"
    obligations = compile_obligations_with_message_chain(
        model,
        base_compile=lambda _model: _stub_base(),
    )
    receipt = obligations["message_chain_obligation_receipt"]
    assert receipt["status"] == "COMPILED"
    chain_rows = [
        row
        for row in obligations["obligations"]
        if row.get("risk_family") == chain.RISK_FAMILY
    ]
    assert len(chain_rows) == 1
    assert chain_rows[0]["property"]["template"] == chain.PROTOCOL_TEMPLATE
    assert chain_rows[0]["required_observers"] == [chain.OBSERVER_ID]
    return model, chain_rows[0]


def test_chain_obligation_compiles_and_experiment_is_executable_offline() -> None:
    model, obligation = _compiled_chain_obligation()
    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=model,
        environment_type="test",
        policy_version="message-chain-test",
        available_adapters={"http_api", events.ADAPTER},
    )
    assert experiment["compile_receipt"]["status"] == "COMPILED"
    assert any(
        row.get("observer_id") == chain.OBSERVER_ID
        for row in experiment["observers"]
    )
    assert experiment["treatment_plan"][0]["protocol_step"] == "event_trigger"
    assertion = experiment["assertions"][0]
    assert assertion["kind"] == chain.ASSERTION_KIND
    assert assertion["property"]["message_chain"]["event_name"] == "PaymentSucceeded"


def test_chain_obligation_receipt_when_no_invariants() -> None:
    obligations = compile_obligations_with_message_chain(
        _behavior_ir(),
        base_compile=lambda _model: _stub_base(),
    )
    assert obligations["message_chain_obligation_receipt"]["status"] == "NOT_REQUESTED"
    assert obligations["message_chain_obligation_receipt"]["invariant_count"] == 0


def test_runtime_surface_obligation_marks_runtime_channel() -> None:
    model, _receipt = bind_source_message_chains(
        _behavior_ir(),
        {"runtime_event_surfaces": [_runtime_surface()]},
    )
    obligations = compile_obligations_with_message_chain(
        model,
        base_compile=lambda _model: _stub_base(),
    )
    receipt = obligations["message_chain_obligation_receipt"]
    assert receipt["status"] == "COMPILED"
    assert receipt["runtime_observation_obligation_count"] == 1
    row = [
        r for r in obligations["obligations"] if r.get("risk_family") == chain.RISK_FAMILY
    ][0]
    assert row["property"]["channel"] == "runtime_observation"
    assert row["property"]["derivation"] == "runtime-observed"


# ── 5. Registered protocol compile ──────────────────────────────────────────


def test_message_chain_protocol_compiles_source_grounded_contract() -> None:
    protocol = resolve_family_protocol(chain.RISK_FAMILY, chain.PROTOCOL_TEMPLATE)
    assert protocol is not None
    operation = {
        "id": "bir_op_payment_callback",
        "operation_id": "payment_callback",
        "source_operation_refs": ["payment_callback"],
        "method": "POST",
        "path": "/api/payments/callback",
        "raw_path": "/api/payments/callback",
        "request_example": {"payment_id": "pay-1"},
    }
    compiled = protocol["compiler"]({
        "property_spec": {
            "template": chain.PROTOCOL_TEMPLATE,
            "invariant_ref": "inv_chain_payment_order_status",
            "operation_ref": operation["id"],
            "actor_ref": "actor_gateway",
            "message_chain": _chain_contract(),
        },
        "operation_ref": operation["id"],
        "operation": operation,
        "treatment_actor_ref": "actor_gateway",
    })
    experiment = validate_registered_protocol_result(
        compiled,
        registration=protocol,
    )
    assert experiment["status"] == "COMPILED"
    assert experiment["observers"][0]["observer_id"] == chain.OBSERVER_ID
    assert experiment["treatment_plan"][0]["protocol_step"] == "event_trigger"
    assert experiment["assertion"]["kind"] == chain.ASSERTION_KIND


def test_message_chain_protocol_blocks_without_contract() -> None:
    protocol = resolve_family_protocol(chain.RISK_FAMILY, chain.PROTOCOL_TEMPLATE)
    operation = {
        "id": "bir_op_payment_callback",
        "operation_id": "payment_callback",
        "source_operation_refs": ["payment_callback"],
        "method": "GET",
        "path": "/api/payments/callback",
    }
    compiled = protocol["compiler"]({
        "property_spec": {
            "template": chain.PROTOCOL_TEMPLATE,
            "invariant_ref": "inv_missing",
            "operation_ref": operation["id"],
            "actor_ref": "actor_gateway",
        },
        "operation_ref": operation["id"],
        "operation": operation,
        "treatment_actor_ref": "actor_gateway",
    })
    assert compiled["status"] == "BLOCKED"
    assert compiled["reason_code"] == "BLOCKED_MISSING_BINDING"
    assert compiled["detail"] == "message_chain_contract_missing"


# ── 6. Evaluator verdicts ───────────────────────────────────────────────────


def _observation(**overrides: Any) -> dict[str, Any]:
    row = {
        "contract_id": "chain_payment_order_status",
        "channel": "source_contract",
        "event_name": "PaymentSucceeded",
        "expected_min_count": 1,
        "expected_max_count": 1,
        "delivery_count": 1,
        "correlated_delivery_count": 1,
        "duplicate_event_id_count": 0,
        "ordering": {
            "sequence_field": "seq",
            "timestamp_field": "occurred_at",
            "expected_types": ["OrderCreated", "PaymentSucceeded"],
            "violation": "",
            "observed_type_sequence": ["OrderCreated", "PaymentSucceeded"],
        },
        "effects": [{
            "consumer_ref": "order-service",
            "state_field": "status",
            "expected_state": "PAID",
            "previous_state": "PENDING",
            "observed_state": "PAID",
            "state_status": "reached",
        }],
        "observed_event_types": ["PaymentSucceeded"],
        "event_id_fingerprints": [],
        "timestamp_present_count": 1,
        "correlation_fingerprint": "fp",
        "poll_count": 3,
        "successful_poll_count": 3,
        "status_codes": [200],
        "observer_errors": [],
        "observation_window_completed": True,
        "coverage_complete": True,
    }
    row.update(overrides)
    return row


def _evaluate(observation: dict[str, Any]) -> dict[str, Any]:
    return chain._evaluate_message_chain({
        "observations": {chain.EVIDENCE_KEY: observation},
    })


def test_evaluator_passes_healthy_chain() -> None:
    verdict = _evaluate(_observation())
    assert verdict["passed"] is True
    assert verdict["reason_code"] == ""


def test_evaluator_indeterminate_on_incomplete_coverage() -> None:
    verdict = _evaluate(_observation(coverage_complete=False))
    assert verdict["passed"] is None
    assert verdict["reason_code"] == "EVENT_CHAIN_OBSERVATION_COVERAGE_INCOMPLETE"


def test_evaluator_detects_duplicate_delivery() -> None:
    verdict = _evaluate(_observation(duplicate_event_id_count=2))
    assert verdict["passed"] is False
    assert verdict["reason_code"] == "EVENT_CHAIN_DUPLICATE_DELIVERY"


def test_evaluator_detects_lost_event() -> None:
    verdict = _evaluate(_observation(delivery_count=0))
    assert verdict["passed"] is False
    assert verdict["reason_code"] == "EVENT_CHAIN_DELIVERY_BELOW_MINIMUM"


def test_evaluator_detects_ordering_violation() -> None:
    verdict = _evaluate(
        _observation(
            ordering={
                "sequence_field": "seq",
                "timestamp_field": "",
                "expected_types": [],
                "violation": "sequence_regression",
                "observed_type_sequence": ["PaymentSucceeded", "OrderCreated"],
            }
        )
    )
    assert verdict["passed"] is False
    assert verdict["reason_code"] == "EVENT_CHAIN_ORDERING_VIOLATION"


def test_evaluator_detects_effect_not_reached() -> None:
    verdict = _evaluate(
        _observation(
            effects=[{
                "consumer_ref": "order-service",
                "state_field": "status",
                "expected_state": "PAID",
                "previous_state": "PENDING",
                "observed_state": "PENDING",
                "state_status": "not_reached",
            }]
        )
    )
    assert verdict["passed"] is False
    assert verdict["reason_code"] == "EVENT_CHAIN_EFFECT_NOT_REACHED"


# ── 7. Observer handler (offline, monkeypatched transport) ──────────────────


def _fast_chain_contract(**overrides: Any) -> dict[str, Any]:
    """Chain contract with short windows and no ordering claim for offline tests."""
    row = _chain_contract(
        ordering=None,
        observation_window_ms=300,
        poll_interval_ms=100,
    )
    row["consumers"] = [
        {
            "consumer_ref": "order-service",
            "effect": {
                "surface": "http_state",
                "readback": {
                    "path": "/api/orders/{correlation}/status",
                    "state_field": "status",
                    "expected_state": "PAID",
                    "previous_state": "PENDING",
                    "poll_until_ms": 300,
                    "poll_interval_ms": 100,
                },
            },
        }
    ]
    row.update(overrides)
    return row


def _observer_envelope(
    tmp_path: Path,
    *,
    contract: dict[str, Any],
) -> dict[str, Any]:
    exp = {
        "schema_version": "qualibug.experiment.v1",
        "treatment_plan": [{"step_id": "treatment_1", "protocol_step": "event_trigger"}],
        "observers": [{"observer_id": chain.OBSERVER_ID}],
        "assertions": [{
            "kind": chain.ASSERTION_KIND,
            "property": {"message_chain": contract},
        }],
        "_observer_runtime_context": {
            "root": str(tmp_path),
            "project": "message-chain-test",
            "runtime_contract": {
                "status": "approved",
                "approved_base_url": "https://sut.example.test",
                "declared_adapters": ["http_api", events.ADAPTER],
                "environment_kind": "test",
                "is_production": False,
            },
        },
    }
    return {
        "experiment": exp,
        "observations": {
            "treatment_observation": {
                "body": {"id": "order-1", "status": "PENDING"},
                "governance_receipt": {},
            }
        },
        "assertion": exp["assertions"][0],
    }


def test_observer_records_duplicate_delivery_and_effect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_http(
        method: str,
        url: str,
        *,
        token: str = "",
        body: Any = None,
        **_: Any,
    ) -> dict[str, Any]:
        del token, body
        path = urlparse(url).path
        calls.append((str(method).upper(), path))
        if path == "/test-observers/events":
            # evt-1 is delivered twice inside one batch: duplicate delivery.
            return {
                "status": 200,
                "body": {
                    "items": [
                        {"event_id": "evt-1", "event_type": "PaymentSucceeded", "order_id": "order-1", "seq": 1},
                        {"event_id": "evt-1", "event_type": "PaymentSucceeded", "order_id": "order-1", "seq": 1},
                    ]
                },
                "headers": {},
                "duration_ms": 1,
                "error": "",
            }
        if path == "/api/orders/order-1/status":
            return {
                "status": 200,
                "body": {"status": "PAID"},
                "headers": {},
                "duration_ms": 1,
                "error": "",
            }
        return {"status": 404, "body": {}, "headers": {}, "duration_ms": 1, "error": "not_found"}

    monkeypatch.setattr(sandbox, "_http_request", fake_http)
    receipt = chain._message_chain_observer_handler(
        _observer_envelope(tmp_path, contract=_fast_chain_contract())
    )
    validate_observer_receipt(receipt)
    assert receipt["status"] == "OBSERVED"
    evidence = receipt["evidence"][chain.EVIDENCE_KEY]
    # One distinct event delivered; its duplicate copy is counted, never hidden.
    assert evidence["delivery_count"] == 1
    assert evidence["duplicate_event_id_count"] >= 1
    assert evidence["duplicate_event_id_fingerprints"]
    assert evidence["effects"][0]["state_status"] == "reached"
    assert evidence["effects"][0]["observed_state"] == "PAID"
    assert evidence["raw_event_payloads_included"] is False
    assert any(path == "/api/orders/order-1/status" for _, path in calls)
    verdict = chain._evaluate_message_chain({
        "observations": {chain.EVIDENCE_KEY: evidence},
    })
    assert verdict["passed"] is False
    assert verdict["reason_code"] == "EVENT_CHAIN_DUPLICATE_DELIVERY"


def test_observer_effect_not_reached_when_state_stays_previous(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_http(
        method: str,
        url: str,
        *,
        token: str = "",
        body: Any = None,
        **_: Any,
    ) -> dict[str, Any]:
        del token, body
        path = urlparse(url).path
        if path == "/test-observers/events":
            return {
                "status": 200,
                "body": {"items": []},
                "headers": {},
                "duration_ms": 1,
                "error": "",
            }
        if path == "/api/orders/order-1/status":
            return {
                "status": 200,
                "body": {"status": "PENDING"},
                "headers": {},
                "duration_ms": 1,
                "error": "",
            }
        return {"status": 404, "body": {}, "headers": {}, "duration_ms": 1, "error": "not_found"}

    monkeypatch.setattr(sandbox, "_http_request", fake_http)
    receipt = chain._message_chain_observer_handler(
        _observer_envelope(
            tmp_path,
            contract=_fast_chain_contract(expected_min_count=0),
        )
    )
    evidence = receipt["evidence"][chain.EVIDENCE_KEY]
    assert evidence["effects"][0]["state_status"] == "not_reached"
    verdict = _evaluate(evidence)
    assert verdict["passed"] is False
    assert verdict["reason_code"] == "EVENT_CHAIN_EFFECT_NOT_REACHED"


def test_observer_indeterminate_without_declared_adapter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_http(**_: Any) -> dict[str, Any]:
        raise AssertionError("transport must not be contacted")
        return {}

    monkeypatch.setattr(sandbox, "_http_request", fake_http)
    envelope = _observer_envelope(tmp_path, contract=_chain_contract())
    runtime = envelope["experiment"]["_observer_runtime_context"]["runtime_contract"]
    runtime["declared_adapters"] = ["http_api"]
    receipt = chain._message_chain_observer_handler(envelope)
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "EVENT_CHAIN_OBSERVER_ADAPTER_NOT_DECLARED"


# ── 8. Pre-cleanup observation ──────────────────────────────────────────────


def test_chain_observer_runs_before_cleanup_and_finalizer_reuses_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    chain.install_message_chain_surface()
    chain.install_message_chain_pre_cleanup_observer()

    from ai_test_asset_center import observer_contracts_base as observers

    calls: list[str] = []

    def fake_handler(_envelope: dict[str, Any]) -> dict[str, Any]:
        calls.append("observe")
        return observers._receipt(
            observer_id=chain.OBSERVER_ID,
            status="OBSERVED",
            evidence={
                chain.EVIDENCE_KEY: {
                    "delivery_count": 1,
                    "coverage_complete": True,
                    "observation_window_completed": True,
                }
            },
        )

    monkeypatch.setattr(chain, "_message_chain_observer_handler", fake_handler)
    exp = {
        "treatment_plan": [{
            "step_id": "treatment_1",
            "intent": "trigger_source_declared_event",
            "protocol_step": "event_trigger",
        }],
        "observers": [{"observer_id": chain.OBSERVER_ID}],
        "assertions": [{"kind": chain.ASSERTION_KIND, "property": {"message_chain": _chain_contract()}}],
    }
    observations: dict[str, Any] = {}
    receipt = chain._pre_observe_message_chain(
        exp=exp,
        observations=observations,
        campaign_id="campaign_chain_1",
        execution_id="exec_chain_1",
    )
    assert calls == ["observe"]
    assert receipt is not None
    assert receipt["evidence"]["step_id"] == "treatment_1"
    assert observations[chain.EVIDENCE_KEY]["observation_phase"] == "pre_cleanup"

    reused = observers._REGISTERED_OBSERVER_HANDLERS[chain.OBSERVER_ID]({
        "experiment": exp,
        "observations": observations,
        "assertion": exp["assertions"][0],
    })
    assert calls == ["observe"], "Finalizer must not observe the chain a second time"
    assert reused["receipt_id"] == receipt["receipt_id"]
