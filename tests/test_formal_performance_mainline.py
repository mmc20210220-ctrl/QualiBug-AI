from __future__ import annotations

import json

from ai_test_asset_center import behavior_ir as bir
from ai_test_asset_center import formal_performance_surface as performance
from ai_test_asset_center.scan_performance_contract_overlay import (
    overlay_scan_performance_contracts,
)
from ai_test_asset_center.source_performance_contract_binding import (
    bind_source_performance_contracts,
)
from ai_test_asset_center.source_performance_obligation_binding import (
    compile_obligations_with_source_performance,
)


def _source_ref(source_id: str, locator: str, kind: str) -> dict:
    return {
        "source_id": source_id,
        "version": "1",
        "locator": locator,
        "kind": kind,
        "quote_hash": "",
    }


def _contract() -> dict:
    return {
        "schema_version": "qualibug.formal-performance-contract.v1",
        "signal_type": "formal_performance_contract",
        "contract_id": "perf_order_detail_p95",
        "title": "Order detail P95 stays within 250ms",
        "source_refs": [{
            "source_id": "nfr_orders_v1",
            "version": "1",
            "locator": "section=NFR;table=latency;row=order-detail",
            "kind": "formal_performance_contract",
            "quote_hash": "nfr-hash",
        }],
        "operation_ref": "get_order",
        "actor_ref": "actor_admin",
        "sample_count": 5,
        "warmup_count": 1,
        "percentile": "p95",
        "max_latency_ms": 250,
        "max_error_rate": 0.0,
        "expected_status_class": 2,
    }


def _ir(*, method: str = "GET") -> dict:
    model = bir.empty_behavior_ir(
        project_id="performance-project",
        source_snapshot_hash="performance-source",
    )
    operation = bir._fact_node(
        node_id="bir_op_get_order",
        typed_fields={
            "operation_id": "get_order",
            "service": "orders",
            "method": method,
            "path": "/api/orders/{id}",
            "request_schema": {},
            "request_example": {},
            "response_schema": {},
            "parameters": ["id"],
            "field_dictionary": [],
            "security": [],
            "summary": "Get one order",
            "description": "",
            "tags": ["orders"],
            "side_effect_class": "read" if method == "GET" else "write",
            "read_write": "read" if method == "GET" else "write",
            "entity_refs": [],
            "affected_fields": [],
            "examples": [],
            "source_operation_refs": ["get_order"],
        },
        source_refs=[_source_ref("api_orders_v1", f"{method} /api/orders/{{id}}", "api_operation")],
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
            "allowed_actions": ["read"],
            "denied_actions": [],
            "runtime_bound": True,
        },
        source_refs=[_source_ref("runtime_actors", "admin", "runtime_actor")],
        confidence=1.0,
        derivation="runtime-observed",
        status="accepted",
    )
    model["operations"] = [operation]
    model["actors"] = [actor]
    model["model_id"] = bir._content_addressed_id(model)
    assert bir.validate_behavior_ir(model, require_explicit_relations=True) == []
    return model


def test_performance_surface_registers_all_formal_links() -> None:
    installed = performance.install_formal_performance_surface()

    from ai_test_asset_center.assertion_dsl_base import registered_assertion_kinds
    from ai_test_asset_center.experiment_protocol_registry import registered_family_protocols
    from ai_test_asset_center.observer_contracts_base import OBSERVER_REGISTRY
    from ai_test_asset_center.test_obligation import canonical_risk_families

    assert installed["observer"] == performance.OBSERVER_ID
    assert OBSERVER_REGISTRY[performance.OBSERVER_ID]["adapter"] == "http_api"
    assert OBSERVER_REGISTRY[performance.OBSERVER_ID]["surface"] == "http_latency_series"
    assert performance.ASSERTION_KIND in registered_assertion_kinds()
    assert performance.RISK_FAMILY in canonical_risk_families()
    assert (
        f"{performance.RISK_FAMILY}:{performance.PROTOCOL_TEMPLATE}"
        in registered_family_protocols()
    )


def test_only_explicitly_typed_latency_signals_enter_overlay() -> None:
    ordinary = {
        "signal_type": "metrics_dashboard",
        "contract_id": "ordinary_metrics",
        "source_refs": _contract()["source_refs"],
    }
    merged, receipt = overlay_scan_performance_contracts(
        {},
        campaign_context={
            "external_signal_requests": [ordinary, _contract()],
        },
    )

    assert [row["contract_id"] for row in merged["performance_formal_contracts"]] == [
        "perf_order_detail_p95"
    ]
    assert receipt["typed_external_contract_count"] == 1
    assert receipt["contract_added_count"] == 1
    assert receipt["telemetry_inferred_as_contract"] is False


def test_latency_contract_binds_exact_read_operation_and_actor() -> None:
    bound, receipt = bind_source_performance_contracts(
        _ir(),
        {"performance_formal_contracts": [_contract()]},
    )

    assert receipt["status"] == "BOUND"
    invariant = next(
        row for row in bound["invariants"]
        if row.get("performance_contract_id") == "perf_order_detail_p95"
    )
    assert invariant["operation_refs"] == ["bir_op_get_order"]
    assert invariant["performance_actor_ref"] == "actor_admin"
    assert invariant["expression"]["kind"] == "latency_budget_contract"
    relation = next(
        row for row in bound["relations"]
        if row.get("from_ref") == invariant["id"]
    )
    assert relation["relation_type"] == "observes"
    assert relation["operation_ref"] == "bir_op_get_order"


def test_write_operation_is_not_admitted_as_first_latency_increment() -> None:
    bound, receipt = bind_source_performance_contracts(
        _ir(method="POST"),
        {"performance_formal_contracts": [_contract()]},
    )

    assert receipt["bound_invariant_count"] == 0
    assert receipt["reason_counts"] == {
        "FORMAL_PERFORMANCE_GET_OR_HEAD_REQUIRED": 1
    }
    assert not any(row.get("performance_contract_id") for row in bound["invariants"])


def test_performance_invariant_becomes_one_read_only_obligation() -> None:
    performance.install_formal_performance_surface()
    bound, _ = bind_source_performance_contracts(
        _ir(),
        {"performance_formal_contracts": [_contract()]},
    )
    result = compile_obligations_with_source_performance(
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
        if row["risk_family"] == performance.RISK_FAMILY
    ]
    assert len(rows) == 1
    obligation = rows[0]
    assert obligation["required_operations"] == ["bir_op_get_order"]
    assert obligation["required_actors"] == ["actor_admin"]
    assert obligation["required_observers"] == [performance.OBSERVER_ID]
    assert obligation["property"]["template"] == performance.PROTOCOL_TEMPLATE
    assert obligation["cleanup_requirement"] == {
        "required": False,
        "reason": "read_only_sequential_latency_sampling",
    }
    assert result["by_family"][performance.RISK_FAMILY] == 1
    assert result["source_performance_obligation_receipt"]["load_capacity_claimed"] is False


def test_protocol_emits_exact_warmup_and_sample_steps() -> None:
    result = performance._compile_performance_protocol({
        "property_spec": {
            "invariant_ref": "bir_perf_inv",
            "actor_ref": "actor_admin",
            "performance_contract": _contract(),
        },
        "operation": _ir()["operations"][0],
        "operation_ref": "bir_op_get_order",
        "treatment_actor_ref": "actor_admin",
    })

    assert result["status"] == "COMPILED"
    assert result["control_plan"] == []
    assert [row["step_id"] for row in result["treatment_plan"]] == [
        "performance_warmup_1",
        "performance_sample_1",
        "performance_sample_2",
        "performance_sample_3",
        "performance_sample_4",
        "performance_sample_5",
    ]
    assert result["per_step_evidence"] is True
    assert result["assertion"]["kind"] == performance.ASSERTION_KIND


def _steps(
    durations: list[float],
    statuses: list[int] | None = None,
    attempts: list[int] | None = None,
) -> list[dict]:
    status_values = statuses or [200] * len(durations)
    attempt_values = attempts or [1] * len(durations)
    return [
        {
            "step_id": f"performance_sample_{index + 1}",
            "operation_ref": "bir_op_get_order",
            "actor_ref": "actor_admin",
            "method": "GET",
            "path": f"/api/orders/{index + 1}",
            "status_code": status_values[index],
            "duration_ms": duration,
            "raw": {"_attempts": attempt_values[index]},
            "body": {"secret": "must-not-enter-performance-receipt"},
            "headers": {"authorization": "Bearer secret"},
        }
        for index, duration in enumerate(durations)
    ]


def _observer_envelope(steps: list[dict]) -> dict:
    contract = _contract()
    return {
        "assertion": {
            "kind": performance.ASSERTION_KIND,
            "property": {"performance_contract": contract},
        },
        "property": {"performance_contract": contract},
        "execution_steps": steps,
    }


def test_complete_p95_series_is_observed_and_redacted() -> None:
    receipt = performance._performance_observer_handler(
        _observer_envelope(_steps([100, 120, 140, 160, 200]))
    )

    assert receipt["status"] == "OBSERVED"
    evidence = receipt["evidence"][performance.EVIDENCE_KEY]
    # Nearest-rank p95 for five samples is the maximum (rank ceil(4.75)=5).
    assert evidence["observed_percentile_ms"] == 200
    assert evidence["observed_error_rate"] == 0
    assert evidence["coverage_complete"] is True
    assert evidence["retried_sample_count"] == 0
    assert evidence["missing_attempt_count"] == 0
    assert evidence["measurement_semantics"] == "sequential_get_or_head_single_attempt_samples"
    serialized = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
    assert "must-not-enter-performance-receipt" not in serialized
    assert "Bearer secret" not in serialized

    verdict = performance._evaluate_latency_budget({
        "observations": {performance.EVIDENCE_KEY: evidence},
    })
    assert verdict["passed"] is True


def test_latency_or_error_rate_budget_can_fail_independently() -> None:
    slow = performance._performance_observer_handler(
        _observer_envelope(_steps([100, 120, 140, 160, 300]))
    )["evidence"][performance.EVIDENCE_KEY]
    slow_verdict = performance._evaluate_latency_budget({
        "observations": {performance.EVIDENCE_KEY: slow},
    })
    assert slow_verdict["passed"] is False
    assert slow_verdict["actual"]["latency_budget_exceeded"] is True
    assert slow_verdict["actual"]["error_rate_budget_exceeded"] is False

    error = performance._performance_observer_handler(
        _observer_envelope(_steps(
            [100, 110, 120, 130, 140],
            [200, 200, 500, 200, 200],
        ))
    )["evidence"][performance.EVIDENCE_KEY]
    error_verdict = performance._evaluate_latency_budget({
        "observations": {performance.EVIDENCE_KEY: error},
    })
    assert error_verdict["passed"] is False
    assert error_verdict["actual"]["latency_budget_exceeded"] is False
    assert error_verdict["actual"]["error_rate_budget_exceeded"] is True


def test_missing_sample_or_duration_is_indeterminate() -> None:
    missing_step = performance._performance_observer_handler(
        _observer_envelope(_steps([100, 120, 140, 160]))
    )
    assert missing_step["status"] == "INDETERMINATE"
    assert missing_step["reason_code"] == "PERFORMANCE_SAMPLE_SET_INCOMPLETE"

    missing_duration_steps = _steps([100, 120, 140, 160, 180])
    missing_duration_steps[2]["duration_ms"] = None
    missing_duration = performance._performance_observer_handler(
        _observer_envelope(missing_duration_steps)
    )
    assert missing_duration["status"] == "INDETERMINATE"
    assert missing_duration["evidence"][performance.EVIDENCE_KEY][
        "missing_duration_count"
    ] == 1


def test_retried_transport_duration_is_not_trusted_as_service_latency() -> None:
    receipt = performance._performance_observer_handler(
        _observer_envelope(_steps(
            [100, 120, 140, 160, 180],
            attempts=[1, 1, 2, 1, 1],
        ))
    )

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "PERFORMANCE_RETRIED_TRANSPORT_UNTRUSTWORTHY"
    evidence = receipt["evidence"][performance.EVIDENCE_KEY]
    assert evidence["retried_sample_count"] == 1
    assert evidence["missing_attempt_count"] == 0
    assert "coverage_complete" not in evidence
