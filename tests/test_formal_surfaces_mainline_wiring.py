"""Formal surface mainline wiring (event / UI / performance / stability).

Guards the four remaining non-http surfaces end to end without touching a target:
install registers observer + assertion kind + risk family + protocol; contract
invariants compile into obligations through the installed wrapper chain; the
registered protocol compilers answer COMPILED; the observer gate blocks without
the declared adapter; dispatch reaches each handler and refuses fail-closed; the
registered-observer evidence merge is first-class in the dispatch authority; and
the executor injects runtime context first-class (no method replacement).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_test_asset_center import discovery_runtime_planning as _planning  # noqa: E402
from ai_test_asset_center import obligation_source_adapter as _osa  # noqa: E402
from ai_test_asset_center import experiment_compiler_obligation as _eco  # noqa: E402
from ai_test_asset_center import observer_contracts_base as _ocb  # noqa: E402
from ai_test_asset_center.behavior_ir_core import empty_behavior_ir  # noqa: E402
from ai_test_asset_center.experiment_protocols import compile_family_protocol  # noqa: E402
from ai_test_asset_center.observer_contracts_base import (  # noqa: E402
    OBSERVER_REGISTRY,
    compile_observer_requirements,
    observe_experiment_requirements,
)
from ai_test_asset_center.test_obligation import (  # noqa: E402
    _RUNTIME_CANONICAL_FAMILIES,
    canonical_risk_families,
)

_SURFACES = [
    ("formal_event_surface", "source_event_delivery_reader", "source_event_delivery_contract", "event_delivery_consistency"),
    ("formal_ui_surface", "ui_source_expectation_reader", "ui_source_expectation", "ui_state_consistency"),
    ("formal_performance_surface", "source_http_latency_series_reader", "source_latency_budget", "performance_latency"),
    ("formal_stability_surface", "source_http_read_stability_reader", "source_read_stability_budget", "stability_reliability"),
]

_ACTOR = {
    "id": "actor-operator",
    "role": "operator",
    "account_ref": "operator_a",
    "status": "accepted",
    "derivation": "explicit",
    "confidence": 0.9,
}

_OPERATIONS = [
    {
        "id": "create_order",
        "method": "POST",
        "path": "/orders",
        "read_write": "write",
        "status": "accepted",
        "derivation": "explicit",
        "confidence": 0.8,
        "source_refs": [{"source_id": "api", "kind": "api_operation", "locator": "POST /orders"}],
    },
    {
        "id": "get_order",
        "method": "GET",
        "path": "/orders/{id}",
        "read_write": "read",
        "status": "accepted",
        "derivation": "explicit",
        "confidence": 0.8,
        "source_refs": [{"source_id": "api", "kind": "api_operation", "locator": "GET /orders/{id}"}],
    },
]

_EVENT_CONTRACT = {
    "contract_id": "ec-1",
    "observer_path": "/events",
    "events_path": "$.items",
    "event_id_field": "event_id",
    "event_type_field": "event_type",
    "correlation_field": "order_id",
    "correlation_query_parameter": "order_id",
    "expected_event_type": "order_created",
    "correlation_value": "o-1",
    "expected_min_count": 1,
    "expected_max_count": 1,
    "observation_window_ms": 5000,
    "poll_interval_ms": 500,
    "trigger_body": {"sku": "sku-1", "quantity": 1},
}

_PERFORMANCE_CONTRACT = {
    "contract_id": "pc-1",
    "sample_count": 3,
    "warmup_count": 0,
    "percentile": "p95",
    "max_latency_ms": 2000,
    "max_error_rate": 0.0,
    "expected_status_class": 2,
}

_STABILITY_CONTRACT = {
    "contract_id": "sc-1",
    "sample_count": 5,
    "max_failed_samples": 1,
    "max_retried_samples": 1,
    "expected_status_class": 2,
}

_UI_REQUEST = {
    "request_id": "ui-1",
    "provider": "playwright_browser_plan",
    "start_url": "/orders",
    "execution_mode": "safe_read_only",
    "browser_plan": {
        "steps": [
            {"action": "goto", "url": "/orders"},
            {"action": "expect_text", "selector": "#order-status", "text": "pending"},
        ]
    },
    "success_criteria": {"all_steps_complete": True},
}


def _node(identifier: str, **typed: Any) -> dict[str, Any]:
    return {
        "id": identifier,
        "status": "accepted",
        "derivation": "explicit",
        "confidence": 0.9,
        **typed,
    }


def _relation(
    identifier: str,
    *,
    relation_type: str,
    from_ref: str,
    to_ref: str,
    operation_ref: str,
    actor_ref: str,
) -> dict[str, Any]:
    return {
        "id": identifier,
        "relation_type": relation_type,
        "from_ref": from_ref,
        "to_ref": to_ref,
        "operation_ref": operation_ref,
        "actor_ref": actor_ref,
        "preconditions": [],
        "effects": [],
        "source_refs": [{"source_id": "contract", "kind": "source_contract", "locator": identifier}],
        "status": "accepted",
        "derivation": "explicit",
        "confidence": 0.9,
    }


def _contract_ir() -> dict[str, Any]:
    model = empty_behavior_ir(project_id="formal-surfaces-wiring")
    model["actors"] = [_ACTOR]
    model["operations"] = _OPERATIONS
    model["invariants"] = [
        _node(
            "inv-event-1",
            expression={
                "kind": "event_delivery_contract",
                "operator": "must_match_declared_event_delivery",
                "operands": [],
                "raw": "order creation emits exactly one order_created event",
            },
            operation_refs=["create_order"],
            event_contract_id="ec-1",
            event_contract=_EVENT_CONTRACT,
            event_actor_ref="actor-operator",
            binding_status="source_identity_bound",
            source_refs=[{"source_id": "contract", "kind": "source_contract", "locator": "ec-1"}],
        ),
        _node(
            "inv-perf-1",
            expression={
                "kind": "latency_budget_contract",
                "operator": "must_stay_within_declared_latency_budget",
                "operands": [],
                "raw": "order reads stay within declared latency budget",
            },
            operation_refs=["get_order"],
            performance_contract_id="pc-1",
            performance_contract=_PERFORMANCE_CONTRACT,
            performance_actor_ref="actor-operator",
            binding_status="source_identity_bound",
            source_refs=[{"source_id": "contract", "kind": "source_contract", "locator": "pc-1"}],
        ),
        _node(
            "inv-stab-1",
            expression={
                "kind": "read_stability_contract",
                "operator": "must_stay_within_declared_read_stability",
                "operands": [],
                "raw": "order reads stay within declared reliability budget",
            },
            operation_refs=["get_order"],
            stability_contract_id="sc-1",
            stability_contract=_STABILITY_CONTRACT,
            stability_actor_ref="actor-operator",
            binding_status="source_identity_bound",
            source_refs=[{"source_id": "contract", "kind": "source_contract", "locator": "sc-1"}],
        ),
        _node(
            "inv-ui-1",
            expression={
                "kind": "ui_source_expectation",
                "operator": "must_satisfy_source_declared_ui_expectation",
                "operands": [],
                "raw": "order page shows pending state",
            },
            operation_refs=["get_order"],
            ui_request=_UI_REQUEST,
            ui_actor_ref="actor-operator",
            binding_status="source_identity_bound",
            source_refs=[{"source_id": "contract", "kind": "source_contract", "locator": "ui-1"}],
        ),
    ]
    model["relations"] = [
        _relation(
            "rel-event-1",
            relation_type="produces",
            from_ref="create_order",
            to_ref="inv-event-1",
            operation_ref="create_order",
            actor_ref="actor-operator",
        ),
        _relation(
            "rel-perf-1",
            relation_type="observes",
            from_ref="inv-perf-1",
            to_ref="get_order",
            operation_ref="get_order",
            actor_ref="actor-operator",
        ),
        _relation(
            "rel-stab-1",
            relation_type="observes",
            from_ref="inv-stab-1",
            to_ref="get_order",
            operation_ref="get_order",
            actor_ref="actor-operator",
        ),
        _relation(
            "rel-ui-1",
            relation_type="observes",
            from_ref="inv-ui-1",
            to_ref="get_order",
            operation_ref="get_order",
            actor_ref="actor-operator",
        ),
    ]
    return model


@pytest.fixture(scope="module")
def wired_ir() -> dict:
    from ai_test_asset_center.formal_event_surface import install_formal_event_surface
    from ai_test_asset_center.formal_performance_surface import (
        install_formal_performance_surface,
    )
    from ai_test_asset_center.formal_stability_surface import (
        install_formal_stability_surface,
    )
    from ai_test_asset_center.formal_ui_surface import install_formal_ui_surface
    from ai_test_asset_center.source_event_obligation_binding import (
        install_source_event_obligation_binding,
    )
    from ai_test_asset_center.source_job_obligation_binding import (
        install_source_job_obligation_binding,
    )
    from ai_test_asset_center.source_performance_obligation_binding import (
        install_source_performance_obligation_binding,
    )
    from ai_test_asset_center.source_stability_obligation_binding import (
        install_source_stability_obligation_binding,
    )
    from ai_test_asset_center.source_ui_obligation_binding import (
        install_source_ui_obligation_binding,
    )

    install_formal_event_surface()
    install_formal_ui_surface()
    install_formal_performance_surface()
    install_formal_stability_surface()
    install_source_event_obligation_binding()
    install_source_performance_obligation_binding()
    install_source_stability_obligation_binding()
    install_source_ui_obligation_binding()
    install_source_job_obligation_binding()

    yield _contract_ir()

    # Deliberately NO teardown of the four surfaces: on the real mainline they are
    # installed unconditionally by discovery_runtime_semantic_binding at import, and
    # other test files depend on that persistent state. Removing them here broke
    # those files when this module ran earlier in the same process. Only the
    # per-test registered observer is cleaned in its own test. Protocols are
    # idempotent registrations and stay.


def test_surfaces_install_registers_all_links(wired_ir: dict) -> None:
    from ai_test_asset_center.assertion_dsl_base import registered_assertion_kinds
    from ai_test_asset_center.experiment_protocol_registry import (
        registered_family_protocols,
    )

    protocols = set(registered_family_protocols())
    for _module_name, observer_id, kind, family in _SURFACES:
        assert OBSERVER_REGISTRY.get(observer_id, {}).get("implemented") is True
        assert kind in registered_assertion_kinds()
        assert family in canonical_risk_families()
    assert "event_delivery_consistency:source_declared_event_observation" in protocols
    assert "ui_state_consistency:source_declared_ui_expectation" in protocols
    assert "performance_latency:source_declared_latency_budget" in protocols
    assert "stability_reliability:source_declared_read_stability" in protocols


def test_contract_invariants_compile_into_obligations(wired_ir: dict) -> None:
    pack = _planning.compile_obligations_from_behavior_ir(
        wired_ir,
        root=str(ROOT),
        project="formal-surfaces-wiring",
    )
    by_family: dict[str, int] = {}
    for row in pack["obligations"]:
        family = row.get("risk_family")
        if family in {"event_delivery_consistency", "ui_state_consistency", "performance_latency", "stability_reliability"}:
            by_family[family] = by_family.get(family, 0) + 1
    assert by_family.get("event_delivery_consistency", 0) >= 1
    assert by_family.get("performance_latency", 0) >= 1
    assert by_family.get("stability_reliability", 0) >= 1
    assert by_family.get("ui_state_consistency", 0) >= 1


def test_registered_protocol_compilers_answer_compiled(wired_ir: dict) -> None:
    operation_by_id = {row["id"]: row for row in _OPERATIONS}
    cases = [
        ("event_delivery_consistency", "create_order", "source_declared_event_observation", {"event_contract": _EVENT_CONTRACT, "actor_ref": "actor-operator"}),
        ("performance_latency", "get_order", "source_declared_latency_budget", {"performance_contract": _PERFORMANCE_CONTRACT, "actor_ref": "actor-operator"}),
        ("stability_reliability", "get_order", "source_declared_read_stability", {"stability_contract": _STABILITY_CONTRACT, "actor_ref": "actor-operator"}),
        ("ui_state_consistency", "get_order", "source_declared_ui_expectation", {"ui_request": _UI_REQUEST, "actor_ref": "actor-operator"}),
    ]
    for family, operation_ref, template, extra in cases:
        protocol = compile_family_protocol(
            risk_family=family,
            operation=operation_by_id[operation_ref],
            operation_ref=operation_ref,
            control_actor_ref="",
            treatment_actor_ref="actor-operator",
            property_spec={"template": template, **extra},
            behavior_ir=wired_ir,
        )
        assert protocol["status"] == "COMPILED", f"{family}: {protocol.get('reason_code')} {protocol.get('detail')}"


def test_observer_gate_requires_declared_adapter(wired_ir: dict) -> None:
    # event / ui require their declared adapters; performance/stability are http_api.
    _, reason, detail = compile_observer_requirements(
        ["http_response", "source_event_delivery_reader"],
        risk_family="event_delivery_consistency",
        available_adapters={"http_api"},
    )
    assert reason == "BLOCKED_UNSUPPORTED_ADAPTER" and detail == "event_observer_http"

    requirements, reason, detail = compile_observer_requirements(
        ["http_response", "source_event_delivery_reader"],
        risk_family="event_delivery_consistency",
        available_adapters={"http_api", "event_observer_http"},
    )
    assert reason == "" and len(requirements) == 2

    _, reason, detail = compile_observer_requirements(
        ["source_http_latency_series_reader"],
        risk_family="performance_latency",
        available_adapters={"http_api"},
    )
    assert reason == ""

    _, reason, detail = compile_observer_requirements(
        ["source_http_read_stability_reader"],
        risk_family="stability_reliability",
        available_adapters={"http_api"},
    )
    assert reason == ""

    _, reason, detail = compile_observer_requirements(
        ["ui_source_expectation_reader"],
        risk_family="ui_state_consistency",
        available_adapters={"http_api"},
    )
    assert reason == "BLOCKED_UNSUPPORTED_ADAPTER" and detail == "ui_browser"


def test_dispatch_reaches_handlers_and_refuses_fail_closed(wired_ir: dict) -> None:
    observers = [
        {"observer_id": "source_event_delivery_reader"},
        {"observer_id": "source_http_latency_series_reader"},
        {"observer_id": "source_http_read_stability_reader"},
        {"observer_id": "ui_source_expectation_reader"},
    ]
    receipts = observe_experiment_requirements(
        {
            "assertions": [
                {
                    "kind": "source_event_delivery_contract",
                    "property": {
                        "event_contract": _EVENT_CONTRACT,
                        "ui_request": _UI_REQUEST,
                        "performance_contract": _PERFORMANCE_CONTRACT,
                        "stability_contract": _STABILITY_CONTRACT,
                    },
                },
            ],
            "observers": observers,
            "source_refs": [],
        },
        observations={
            "control_observation": {},
            "treatment_observation": {},
            "execution_steps": [],
        },
        campaign_id="campaign-surfaces",
        execution_id="execution-surfaces",
    )
    by_observer = {row["observer_id"]: row for row in receipts}
    event_receipt = by_observer["source_event_delivery_reader"]
    assert event_receipt["status"] == "INDETERMINATE"
    assert event_receipt["reason_code"] == "EVENT_RUNTIME_CONTEXT_MISSING"
    ui_receipt = by_observer["ui_source_expectation_reader"]
    assert ui_receipt["status"] == "INDETERMINATE"
    assert ui_receipt["reason_code"] == "UI_RUNTIME_CONTEXT_MISSING"
    # performance/stability judge only their sample steps; none present -> INDETERMINATE
    assert by_observer["source_http_latency_series_reader"]["status"] == "INDETERMINATE"
    assert by_observer["source_http_read_stability_reader"]["status"] == "INDETERMINATE"


def test_registered_observer_evidence_merge_is_first_class() -> None:
    """Dispatch itself copies OBSERVED evidence into observations (no wrapper)."""
    observer_id = "test_wiring_evidence_observer"

    def handler(envelope: dict[str, Any]) -> dict[str, Any]:
        return _ocb._receipt(
            observer_id=observer_id,
            status="OBSERVED",
            evidence={"test_wiring_evidence_key": "observed-value"},
        )

    _ocb.register_observer(
        observer_id,
        surface="test_surface",
        adapter="test_adapter",
        handler=handler,
        evidence_keys=("test_wiring_evidence_key",),
    )
    try:
        observations: dict[str, Any] = {
            "control_observation": {},
            "treatment_observation": {},
            "execution_steps": [],
        }
        observe_experiment_requirements(
            {
                "assertions": [],
                "observers": [{"observer_id": observer_id}],
                "source_refs": [],
            },
            observations=observations,
            campaign_id="campaign-merge",
            execution_id="execution-merge",
        )
        assert observations.get("test_wiring_evidence_key") == "observed-value"
        assert observations.get(observer_id + "_observer_receipt", {}).get("status") == "OBSERVED"
    finally:
        OBSERVER_REGISTRY.pop(observer_id, None)
        _ocb._REGISTERED_OBSERVER_HANDLERS.pop(observer_id, None)


def test_execute_one_experiment_injects_runtime_context_first_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mainline executor itself provides root/project/runtime_contract."""
    import ai_test_asset_center.experiment_executor as executor_module

    captured: dict[str, Any] = {}

    def fake_governed(experiment: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        captured["experiment"] = experiment
        return {"observer_receipts": [], "execution_steps": []}

    monkeypatch.setattr(executor_module, "_execute_one_governed", fake_governed)
    monkeypatch.setattr(executor_module, "_authorization_binding_targets", lambda exp: [])
    monkeypatch.setattr(
        executor_module,
        "enforce_authorization_oracle_causality",
        lambda **kwargs: kwargs["result"],
    )
    monkeypatch.setattr(
        executor_module,
        "enforce_oracle_validity_gates",
        lambda result, experiment: result,
    )
    monkeypatch.setattr(
        executor_module,
        "_verify_authorization_compile_identity",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        executor_module,
        "attach_authorization_delivery_evidence",
        lambda governed, experiment: governed,
    )
    monkeypatch.setattr(
        executor_module,
        "_seal_authorization_finding_lineage",
        lambda packaged: packaged,
    )

    executor_module.execute_one_experiment(
        {"experiment_id": "exp-1", "observers": []},
        behavior_ir={"operations": []},
        root=ROOT,
        project="formal-surfaces-wiring",
        base_url="http://localhost",
        runtime_contract={"status": "approved", "declared_adapters": []},
        campaign_id="campaign-exec",
        execution_id="execution-exec",
    )
    context = captured["experiment"].get("_observer_runtime_context")
    assert context is not None
    assert context["root"] == str(ROOT)
    assert context["project"] == "formal-surfaces-wiring"
    assert context["runtime_contract"].get("status") == "approved"
