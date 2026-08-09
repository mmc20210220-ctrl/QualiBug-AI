"""UI surface obligations must carry registry-stamped observer adapters.

Regression for the run11 end-to-end blocker: the ``ui_state_consistency``
compile branch attached the protocol's raw ``{"observer_id": "ui_browser"}``
rows verbatim, so every compiled UI experiment carried observers with no
``adapter`` identity, and ``build_agent_intent_plan`` — which requires each
observer to name an execution adapter — raised
``observer_contract_missing:<obligation_id>`` on the first selected UI
obligation, killing the whole run at planning.

The fix routes the UI observers through the same observer-contract registry
every other family uses (``compile_observer_requirements``):

* with the ``ui_browser`` adapter declared, the observer is stamped with
  adapter/surface/receipt identity and the agent-intent plan gate passes;
* without the declaration the experiment fails closed as
  ``BLOCKED_UNSUPPORTED_ADAPTER`` instead of compiling an unobservable
  experiment.
"""
from __future__ import annotations

import pytest

from ai_test_asset_center.adaptive_discovery_planner import (
    AgentIntentError,
    build_agent_intent_plan,
)
from ai_test_asset_center.experiment_compiler import (
    compile_experiment_for_obligation,
)

_UI_OBSERVER_ID = "ui_browser"


def _ui_obligation() -> dict:
    return {
        "schema_version": "qualibug.obligation.v1",
        "obligation_id": "obl_ui_test_screen",
        "risk_family": "ui_state_consistency",
        "required_operations": [],
        "ui_url": "http://localhost:3002",
        "property": {
            "template": "ui_state_consistency",
            "invariant_ref": "bir_ui_test",
            "expression": {
                "kind": "ui",
                "operator": "must_hold",
                "operands": [],
                "raw": "ADMIN-RBAC-03：All current workbench actions are visible.",
            },
            "ui_url": "http://localhost:3002",
            "screen": "ADMIN-01",
            "negative_examples": [],
            "ui_oracle": {},
            "surface_contracts": [
                {
                    "schema_version": "qualibug.ui-formal-contract.v2",
                    "contract_id": "ui_surface_test_1",
                    "title": "UI surface check: sales_report",
                    "check_kind": "control_state",
                    "control": "sales_report",
                    "role": "admin",
                    "state_context": "",
                    "negative_examples": [],
                    "expectations": [],
                    "ui_request": {
                        "request_id": "ui_surface_test_1",
                        "title": "UI surface check: sales_report",
                        "provider": "playwright_browser_plan",
                        "start_url": "http://localhost:3002",
                        "execution_mode": "safe_read_only",
                        "browser_plan": {
                            "execution_mode": "safe_read_only",
                            "steps": [
                                {"action": "goto", "url": "http://localhost:3002"},
                                {
                                    "action": "expect_visible",
                                    "locator_intent": {"text": "sales_report"},
                                    "timeout_ms": 5000,
                                },
                            ],
                        },
                        "success_criteria": {"action": "all_ui_surface_expectations"},
                        "metadata": {
                            "source_declared": True,
                            "surface_declaration": True,
                        },
                    },
                    "source_refs": [{
                        "source_id": "src_ui",
                        "locator": "ui_surface_test_1",
                        "kind": "ui_surface_declaration",
                    }],
                    "source_id": "src_ui",
                    "status": "accepted",
                    "derivation": "explicit",
                    "confidence": 1.0,
                }
            ],
        },
        "source_refs": [{
            "source_id": "src_ui",
            "locator": "ui_surface_test_1",
            "kind": "ui_surface_declaration",
        }],
    }


def _compile_ui(*, adapters: set[str]) -> dict:
    return compile_experiment_for_obligation(
        _ui_obligation(),
        behavior_ir={
            "model_id": "bir-ui-test",
            "operations": [],
            "actors": [],
            "relations": [],
        },
        environment_type="staging",
        policy_version="v-test",
        available_adapters=adapters,
    )


def test_ui_obligation_compiles_with_registry_stamped_observer_when_adapter_declared() -> None:
    experiment = _compile_ui(adapters={"http_api", "ui_browser"})

    receipt = experiment.get("compile_receipt") or {}
    assert receipt.get("status") == "COMPILED", receipt
    observers = experiment.get("observers") or []
    assert observers, "compiled UI experiment must carry observers"
    stamped = next(
        row for row in observers if row.get("observer_id") == _UI_OBSERVER_ID
    )
    # The registry stamp (adapter/surface/receipt identity) is what the
    # agent-intent plan gate requires; without it the run died at planning.
    assert stamped.get("adapter") == "ui_browser"
    assert stamped.get("surface") == "ui_browser"
    assert stamped.get("receipt_schema")
    assert stamped.get("required_status") == "OBSERVED"


def test_ui_obligation_fails_closed_when_browser_adapter_is_not_declared() -> None:
    experiment = _compile_ui(adapters={"http_api"})

    receipt = experiment.get("compile_receipt") or {}
    assert receipt.get("status") == "BLOCKED", receipt
    assert receipt.get("reason_code") == "BLOCKED_UNSUPPORTED_ADAPTER"
    assert receipt.get("detail") == "ui_browser"


def test_ui_obligation_compiled_experiment_passes_agent_intent_plan_gate() -> None:
    """The exact run11 shape: a UI obligation compiled with the ui_browser
    adapter declared must pass build_agent_intent_plan (no
    observer_contract_missing)."""
    experiment = _compile_ui(adapters={"http_api", "ui_browser"})
    obligation = _ui_obligation()
    adaptive_plan = {
        "schema_version": "qualibug.adaptive-obligation-plan.v1",
        "selected": [{
            "obligation_id": obligation["obligation_id"],
            "experiment_id": experiment.get("experiment_id") or "exp-ui",
            "risk_family": "ui_state_consistency",
            "score": 0.9,
        }],
        "pending_next_round": [],
    }

    receipt = build_agent_intent_plan(
        adaptive_plan,
        obligations=[obligation],
        experiments_by_obligation={obligation["obligation_id"]: experiment},
        behavior_ir={
            "model_id": "bir-ui-test",
            "operations": [],
            "actors": [],
            "relations": [],
        },
    )

    assert receipt["status"] == "VERIFIED"
    assert receipt["intent_count"] == 1
    intent = receipt["intents"][0]
    assert intent["observer_refs"] == [_UI_OBSERVER_ID]
    assert intent["execution_adapters"] == ["ui_browser"]


def test_ui_obligation_without_adapter_still_fails_the_plan_gate() -> None:
    """The fail-fast gate is preserved: an unobservable experiment must never
    produce an intent, even if it somehow reached the planner."""
    obligation = _ui_obligation()
    experiment = {
        "experiment_id": "exp-ui-broken",
        "obligation_id": obligation["obligation_id"],
        "compile_receipt": {"status": "COMPILED"},
        "observers": [{"observer_id": _UI_OBSERVER_ID}],  # no adapter stamp
        "source_refs": obligation.get("source_refs") or [],
    }
    adaptive_plan = {
        "schema_version": "qualibug.adaptive-obligation-plan.v1",
        "selected": [{
            "obligation_id": obligation["obligation_id"],
            "experiment_id": "exp-ui-broken",
            "risk_family": "ui_state_consistency",
            "score": 0.9,
        }],
        "pending_next_round": [],
    }
    with pytest.raises(
        AgentIntentError,
        match=f"observer_contract_missing:{obligation['obligation_id']}",
    ):
        build_agent_intent_plan(
            adaptive_plan,
            obligations=[obligation],
            experiments_by_obligation={obligation["obligation_id"]: experiment},
            behavior_ir={
                "model_id": "bir-ui-test",
                "operations": [],
                "actors": [],
                "relations": [],
            },
        )
