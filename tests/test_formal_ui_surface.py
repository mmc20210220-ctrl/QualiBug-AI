from __future__ import annotations

import json

import pytest

from ai_test_asset_center import formal_ui_surface as ui


def _request(*, expectation: str = "expect_text") -> dict:
    expectation_step = (
        {
            "action": "expect_text",
            "selector": "[data-testid='order-status']",
            "text": "Approved",
            "timeout_ms": 5000,
        }
        if expectation == "expect_text"
        else {
            "action": "expect_url",
            "pattern": "**/orders/123",
            "timeout_ms": 5000,
        }
    )
    return {
        "request_id": "ui_order_approved",
        "provider": "playwright_browser_plan",
        "start_url": "/orders/123",
        "execution_mode": "safe_read_only",
        "browser_plan": {
            "steps": [
                {"action": "goto", "url": "/orders/123"},
                expectation_step,
            ]
        },
        "success_criteria": {"source": "prd:order-approved-view"},
    }


def _envelope(request: dict | None = None) -> dict:
    property_spec = {
        "invariant_ref": "bir_inv_order_approved_visible",
        "ui_request": request or _request(),
    }
    return {
        "experiment": {
            "experiment_id": "exp_ui_1",
            "execution_id": "exec_ui_1",
            "_observer_runtime_context": {
                "root": "/tmp/qualibug",
                "project": "ui_project",
                "runtime_contract": {
                    "status": "approved",
                    "approved_base_url": "http://localhost:8080",
                    "declared_adapters": ["ui_browser"],
                },
            },
        },
        "assertion": {
            "kind": ui.ASSERTION_KIND,
            "property": property_spec,
        },
        "property": property_spec,
        "observations": {},
        "campaign_id": "campaign_ui_1",
        "execution_id": "exec_ui_1",
    }


def test_install_closes_all_formal_ui_registry_links() -> None:
    installed = ui.install_formal_ui_surface()

    from ai_test_asset_center.adapter_capability import DECLARATION_REQUIRED
    from ai_test_asset_center.assertion_dsl_base import registered_assertion_kinds
    from ai_test_asset_center.experiment_protocol_registry import registered_family_protocols
    from ai_test_asset_center.observer_contracts_base import OBSERVER_REGISTRY
    from ai_test_asset_center.test_obligation import canonical_risk_families

    assert installed["observer"] == ui.OBSERVER_ID
    assert ui.OBSERVER_ID in OBSERVER_REGISTRY
    assert OBSERVER_REGISTRY[ui.OBSERVER_ID]["adapter"] == "ui_browser"
    assert ui.EVIDENCE_KEY in OBSERVER_REGISTRY[ui.OBSERVER_ID]["evidence_keys"]
    assert ui.ASSERTION_KIND in registered_assertion_kinds()
    assert ui.RISK_FAMILY in canonical_risk_families()
    assert f"{ui.RISK_FAMILY}:{ui.PROTOCOL_TEMPLATE}" in registered_family_protocols()
    assert f"visibility:{ui.PROTOCOL_TEMPLATE}" in registered_family_protocols()
    assert f"state:{ui.PROTOCOL_TEMPLATE}" in registered_family_protocols()
    assert DECLARATION_REQUIRED["ui_browser"] == "runtime_contract.declared_adapters[]"


def test_protocol_refuses_a_ui_plan_without_a_source_expectation() -> None:
    request = _request()
    request["browser_plan"] = {"steps": [{"action": "goto", "url": "/orders/123"}]}

    result = ui._compile_ui_protocol({
        "property_spec": {"ui_request": request},
        "operation_ref": "bir_op_get_order",
        "treatment_actor_ref": "actor_admin",
    })

    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "BLOCKED_MISSING_ASSERTION"


def test_protocol_compiles_only_the_declared_browser_expectation() -> None:
    result = ui._compile_ui_protocol({
        "property_spec": {
            "invariant_ref": "bir_inv_order_approved_visible",
            "ui_request": _request(),
        },
        "operation_ref": "bir_op_get_order",
        "treatment_actor_ref": "actor_admin",
    })

    assert result["status"] == "COMPILED"
    assert result["control_plan"] == []
    assert result["treatment_plan"][0]["operation_ref"] == "bir_op_get_order"
    assert result["observers"] == [{"observer_id": ui.OBSERVER_ID}]
    assert result["assertion"]["kind"] == ui.ASSERTION_KIND
    assert result["assertion"]["ui_expectation_count"] == 1


def test_timeout_on_the_declared_expectation_becomes_a_formal_violation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_execute(project, request, runtime_contract, *, root, run_id):
        return {
            "status": "failed",
            "duration_ms": 123,
            "results": [{
                "request_id": request["request_id"],
                "provider": "playwright_browser_plan",
                "status": "failed",
                "reason": "TimeoutError: locator did not become visible",
                # The first source step completed; the second source step is therefore the
                # failed expect_text step.
                "steps": [{"step_index": 1, "action": "goto", "status": 200}],
                "findings": [{
                    "finding_id": "provider_must_not_be_formal",
                    "title": "Untrusted provider finding",
                }],
                "artifacts": [{"artifact_type": "screenshot", "ref": "private/final.png"}],
                "duration_ms": 120,
            }],
        }

    monkeypatch.setattr(ui, "_execute_ui_requests", fake_execute)
    receipt = ui._ui_observer_handler(_envelope())

    assert receipt["status"] == "OBSERVED"
    evidence = receipt["evidence"][ui.EVIDENCE_KEY]
    assert evidence["expectation_satisfied"] is False
    assert evidence["violation_observed"] is True
    assert evidence["failed_step_index"] == 2
    assert evidence["failed_expectation"]["action"] == "expect_text"
    assert evidence["provider_candidate_finding_count"] == 1
    assert evidence["provider_findings_consumed"] is False

    serialized = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
    assert "provider_must_not_be_formal" not in serialized
    assert "Untrusted provider finding" not in serialized
    assert "private/final.png" not in serialized

    verdict = ui._evaluate_ui_expectation({
        "observations": {ui.EVIDENCE_KEY: evidence},
    })
    assert verdict["passed"] is False
    assert verdict["actual"]["failed_expectation"]["text"] == "Approved"


def test_successful_browser_plan_passes_the_source_expectation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_execute(project, request, runtime_contract, *, root, run_id):
        return {
            "status": "completed",
            "results": [{
                "request_id": request["request_id"],
                "provider": "playwright_browser_plan",
                "status": "executed",
                "reason": "",
                "steps": [
                    {"step_index": 1, "action": "goto", "status": 200},
                    {"step_index": 2, "action": "expect_text"},
                ],
                "findings": [],
                "artifacts": [],
                "duration_ms": 80,
            }],
        }

    monkeypatch.setattr(ui, "_execute_ui_requests", fake_execute)
    receipt = ui._ui_observer_handler(_envelope())
    evidence = receipt["evidence"][ui.EVIDENCE_KEY]

    assert receipt["status"] == "OBSERVED"
    assert evidence["expectation_satisfied"] is True
    assert ui._evaluate_ui_expectation({
        "observations": {ui.EVIDENCE_KEY: evidence},
    })["passed"] is True


def test_non_expectation_runtime_failure_is_indeterminate_not_a_bug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_execute(project, request, runtime_contract, *, root, run_id):
        return {
            "status": "failed",
            "results": [{
                "request_id": request["request_id"],
                "provider": "playwright_browser_plan",
                "status": "failed",
                "reason": "BrowserDisconnectedError: chromium exited",
                "steps": [],
                "findings": [],
                "artifacts": [],
            }],
        }

    monkeypatch.setattr(ui, "_execute_ui_requests", fake_execute)
    receipt = ui._ui_observer_handler(_envelope())

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "UI_EXPECTATION_RESULT_UNPROVEN"
    evidence = receipt["evidence"][ui.EVIDENCE_KEY]
    assert evidence["expectation_satisfied"] is None
    assert evidence["violation_observed"] is False


def test_registered_ui_receipt_evidence_reaches_oracle_observations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_execute(project, request, runtime_contract, *, root, run_id):
        return {
            "status": "completed",
            "results": [{
                "request_id": request["request_id"],
                "provider": "playwright_browser_plan",
                "status": "executed",
                "reason": "",
                "steps": [
                    {"step_index": 1, "action": "goto", "status": 200},
                    {"step_index": 2, "action": "expect_text"},
                ],
                "findings": [],
                "artifacts": [],
            }],
        }

    monkeypatch.setattr(ui, "_execute_ui_requests", fake_execute)
    ui.install_formal_ui_surface()

    from ai_test_asset_center import experiment_outcome_finalizer as finalizer

    observations: dict = {}
    exp = _envelope()["experiment"] | {
        "observers": [{"observer_id": ui.OBSERVER_ID}],
        "assertions": [_envelope()["assertion"]],
    }
    receipts = finalizer.observe_experiment_requirements(
        exp,
        observations=observations,
        campaign_id="campaign_ui_1",
        execution_id="exec_ui_1",
    )

    assert receipts[0]["observer_id"] == ui.OBSERVER_ID
    assert receipts[0]["status"] == "OBSERVED"
    assert observations[ui.EVIDENCE_KEY]["expectation_satisfied"] is True
    assert observations[ui.OBSERVER_ID + "_observer_receipt"]["receipt_id"] == receipts[0]["receipt_id"]


def test_public_runtime_installs_the_formal_ui_surface() -> None:
    from ai_test_asset_center import discovery_runtime
    from ai_test_asset_center.observer_contracts_base import OBSERVER_REGISTRY

    assert callable(discovery_runtime.run_experiment_candidate)
    assert ui.OBSERVER_ID in OBSERVER_REGISTRY
