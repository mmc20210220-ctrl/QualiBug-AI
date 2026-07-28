from __future__ import annotations

from pathlib import Path

import pytest

from ai_test_asset_center import browser_execution
from ai_test_asset_center import formal_ui_surface
from ai_test_asset_center import formal_ui_surface_guard
from ai_test_asset_center import professional_ui_interaction_cleanup as interaction
from ai_test_asset_center import professional_ui_readonly
from ai_test_asset_center import professional_ui_responsive_accessibility
from ai_test_asset_center.observer_contracts_base import _receipt
from ai_test_asset_center.professional_ui_contract_guard import (
    install_professional_ui_contract_guard,
)
from ai_test_asset_center.professional_ui_interaction_contract_guard import (
    install_controlled_ui_interaction_contract_guard,
)


def _install_ui_mainline() -> None:
    formal_ui_surface.install_formal_ui_surface()
    formal_ui_surface_guard.install_formal_ui_read_only_guard()
    professional_ui_readonly.install_professional_ui_readonly()
    install_professional_ui_contract_guard()
    professional_ui_responsive_accessibility.install_professional_ui_responsive_accessibility()
    interaction.install_controlled_ui_interaction()
    install_controlled_ui_interaction_contract_guard()


def _runtime(*, environment_type: str = "test") -> dict:
    return {
        "status": "approved",
        "approved_base_url": "https://example.test",
        "requested_base_url": "https://example.test",
        "environment_type": environment_type,
        "environment_ref": f"env-{environment_type}",
        "execution_mode": "approved_sandbox_write",
        "declared_adapters": ["ui_browser"],
        "ui_input_bindings": {
            "test_name": {"value": "qualibug-test-record"},
            "test_password": {"value": "secret-from-runtime"},
        },
    }


def _plan() -> dict:
    return {
        "execution_mode": "approved_sandbox_write",
        "write_approved": True,
        "actor_ref": "qa-operator",
        "interaction_contract": {
            "cleanup_strategy": "browser_compensation",
            "equivalence": "source_declared_state_probes",
            "target_scope": "approved_nonproduction_target",
        },
        "state_probes": [
            {"probe_id": "page-url", "property": "url"},
            {
                "probe_id": "record-count",
                "property": "count",
                "selector": "[data-testid=record-row]",
            },
        ],
        "steps": [
            {"phase": "setup", "action": "goto", "url": "/records"},
            {
                "phase": "setup",
                "action": "set_viewport",
                "width": 1280,
                "height": 800,
            },
            {
                "phase": "treatment",
                "action": "fill",
                "selector": "#record-name",
                "value_ref": "test_name",
            },
            {
                "phase": "treatment",
                "action": "click",
                "selector": "#save-record",
            },
            {
                "phase": "assertion",
                "action": "expect_text",
                "selector": "#save-result",
                "text": "Saved",
            },
            {
                "phase": "cleanup",
                "action": "click",
                "selector": "#delete-test-record",
            },
            {
                "phase": "cleanup",
                "action": "click",
                "selector": "#confirm-delete",
            },
        ],
    }


def test_valid_governed_interaction_plan_is_phased_and_source_reversible() -> None:
    _install_ui_mainline()

    normalized = interaction.validate_controlled_browser_plan(_plan(), _runtime())

    assert normalized["execution_mode"] == "approved_sandbox_write"
    assert normalized["interaction_contract"]["cleanup_strategy"] == (
        "browser_compensation"
    )
    assert [row["phase"] for row in normalized["steps"]] == [
        "setup",
        "setup",
        "treatment",
        "treatment",
        "assertion",
        "cleanup",
        "cleanup",
    ]
    assert [row["probe_id"] for row in normalized["state_probes"]] == [
        "page-url",
        "record-count",
    ]


def test_interaction_plan_without_cleanup_is_non_reversible() -> None:
    _install_ui_mainline()
    plan = _plan()
    plan["steps"] = [
        row for row in plan["steps"] if row["phase"] != "cleanup"
    ]

    with pytest.raises(
        browser_execution.BrowserExecutionError,
        match=r"^UI_INTERACTION_CLEANUP_STEPS_MISSING$",
    ):
        interaction.validate_controlled_browser_plan(plan, _runtime())


def test_interaction_plan_requires_state_probes() -> None:
    _install_ui_mainline()
    plan = _plan()
    plan["state_probes"] = []

    with pytest.raises(
        browser_execution.BrowserExecutionError,
        match=r"^UI_INTERACTION_STATE_PROBES_MISSING$",
    ):
        interaction.validate_controlled_browser_plan(plan, _runtime())


def test_interaction_phases_cannot_move_backwards() -> None:
    _install_ui_mainline()
    plan = _plan()
    plan["steps"][-1] = {
        "phase": "treatment",
        "action": "click",
        "selector": "#late-treatment",
    }

    with pytest.raises(
        browser_execution.BrowserExecutionError,
        match=r"^browser_interaction_phase_order_invalid$",
    ):
        interaction.validate_controlled_browser_plan(plan, _runtime())


def test_sensitive_fill_requires_runtime_binding_reference() -> None:
    _install_ui_mainline()
    plan = _plan()
    plan["steps"][2] = {
        "phase": "treatment",
        "action": "fill",
        "selector": "#password",
        "value": "literal-secret",
    }

    with pytest.raises(
        browser_execution.BrowserExecutionError,
        match=r"^browser_sensitive_fill_requires_value_ref$",
    ):
        interaction.validate_controlled_browser_plan(plan, _runtime())


def test_responsive_configuration_is_setup_only_and_validated() -> None:
    _install_ui_mainline()
    plan = _plan()
    plan["steps"][1]["phase"] = "treatment"

    with pytest.raises(
        browser_execution.BrowserExecutionError,
        match=r"^browser_responsive_configuration_phase_invalid:set_viewport$",
    ):
        interaction.validate_controlled_browser_plan(plan, _runtime())

    plan = _plan()
    plan["steps"][1]["width"] = 100
    with pytest.raises(
        browser_execution.BrowserExecutionError,
        match=r"^browser_viewport_width_invalid$",
    ):
        interaction.validate_controlled_browser_plan(plan, _runtime())


def test_read_only_mode_cannot_smuggle_interactive_actions_into_formal_compile() -> None:
    _install_ui_mainline()
    request = {
        "request_id": "ui-readonly-smuggled-click",
        "provider": "playwright_browser_plan",
        "start_url": "https://example.test/records",
        "execution_mode": "safe_read_only",
        "browser_plan": {
            "execution_mode": "safe_read_only",
            "steps": [
                {"action": "goto", "url": "/records"},
                {"action": "click", "selector": "#save-record"},
                {
                    "action": "expect_text",
                    "selector": "#save-result",
                    "text": "Saved",
                },
            ],
        },
    }

    compiled = formal_ui_surface._compile_ui_protocol({
        "property_spec": {"ui_request": request, "actor_ref": "qa-operator"},
        "operation_ref": "list-records",
        "treatment_actor_ref": "qa-operator",
    })

    assert compiled["status"] == "BLOCKED"
    assert compiled["reason_code"] == "BLOCKED_TARGET_POLICY"
    assert compiled["detail"] == "ui_interaction_requires_approved_sandbox_write"


def test_write_compile_requires_cleanup_contract_and_marks_cleanup_authority() -> None:
    _install_ui_mainline()
    plan = _plan()
    request = {
        "request_id": "ui-governed-interaction",
        "provider": "playwright_browser_plan",
        "start_url": "https://example.test/records",
        "execution_mode": "approved_sandbox_write",
        "browser_plan": plan,
    }
    compiled = formal_ui_surface._compile_ui_protocol({
        "property_spec": {"ui_request": request, "actor_ref": "qa-operator"},
        "operation_ref": "list-records",
        "treatment_actor_ref": "qa-operator",
    })
    assert compiled["status"] == "COMPILED"
    assert compiled["assertion"]["ui_interaction_cleanup_required"] is True
    assert compiled["assertion"]["ui_cleanup_receipt_schema"] == (
        interaction.CLEANUP_RECEIPT_SCHEMA
    )

    missing = _plan()
    missing.pop("interaction_contract")
    request["browser_plan"] = missing
    blocked = formal_ui_surface._compile_ui_protocol({
        "property_spec": {"ui_request": request, "actor_ref": "qa-operator"},
        "operation_ref": "list-records",
        "treatment_actor_ref": "qa-operator",
    })
    assert blocked["status"] == "BLOCKED"
    assert blocked["reason_code"] == "BLOCKED_NON_REVERSIBLE_WRITE"


def test_cleanup_receipt_accepts_only_exact_post_cleanup_equivalence() -> None:
    before = {
        "page-url": {"property": "url", "value_fingerprint": "url-a"},
        "record-count": {
            "property": "count",
            "matched_count": 2,
            "value_fingerprint": "count-2",
        },
    }
    accepted = interaction._cleanup_receipt(
        run_id="run-1",
        request_context={"request_id": "ui-1", "actor_ref": "qa-operator"},
        policy={"decision_id": "tpd-1"},
        before=before,
        after=before,
        cleanup_steps=[{"action": "click", "phase": "cleanup"}],
        cleanup_error="",
    )
    assert accepted["schema_version"] == interaction.CLEANUP_RECEIPT_SCHEMA
    assert accepted["status"] == "ACCEPTED"
    assert accepted["reason_code"] == ""
    assert accepted["raw_state_included"] is False

    after = dict(before)
    after["record-count"] = {
        "property": "count",
        "matched_count": 3,
        "value_fingerprint": "count-3",
    }
    mismatch = interaction._cleanup_receipt(
        run_id="run-1",
        request_context={"request_id": "ui-1", "actor_ref": "qa-operator"},
        policy={"decision_id": "tpd-1"},
        before=before,
        after=after,
        cleanup_steps=[{"action": "click", "phase": "cleanup"}],
        cleanup_error="",
    )
    assert mismatch["status"] == "INDETERMINATE"
    assert mismatch["reason_code"] == "UI_CLEANUP_EQUIVALENCE_MISMATCH"


def test_production_target_is_blocked_before_browser_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_ui_mainline()

    def browser_must_not_launch() -> tuple[object, object, str]:
        raise AssertionError("browser launched for production target")

    monkeypatch.setattr(interaction, "_launch_browser", browser_must_not_launch)
    result = interaction.execute_controlled_browser_plan(
        "project-a",
        _plan(),
        _runtime(environment_type="production"),
        root=tmp_path,
        run_id="production-block",
    )

    assert result["status"] == "blocked"
    assert result["execution_status"] == "not_executed"
    assert result["reason"].startswith("UI_WRITE_POLICY_BLOCKED:")
    assert result["cleanup_receipt"]["status"] == "INDETERMINATE"


def test_formal_execution_blocks_missing_or_unaccepted_cleanup_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_ui_mainline()

    def fake_execution(*args: object, **kwargs: object) -> dict:
        return {
            "status": "completed",
            "results": [{
                "request_id": "ui-1",
                "status": "failed",
                "reason": "UI_EXPECTATION_UNSATISFIED:expect_text:value_mismatch",
                "steps": [],
                "cleanup_receipt": {
                    "schema_version": interaction.CLEANUP_RECEIPT_SCHEMA,
                    "status": "INDETERMINATE",
                    "reason_code": "UI_CLEANUP_EQUIVALENCE_MISMATCH",
                },
            }],
        }

    monkeypatch.setattr(
        formal_ui_surface,
        interaction.ORIGINAL_FORMAL_EXECUTION,
        fake_execution,
    )
    request = {
        "execution_mode": "approved_sandbox_write",
        "browser_plan": _plan(),
    }
    execution = interaction._formal_execution_with_cleanup_gate(
        "project-a",
        request,
        _runtime(),
        root=tmp_path,
        run_id="run-1",
    )

    row = execution["results"][0]
    assert row["status"] == "blocked"
    assert row["reason"] == (
        "UI_CLEANUP_EQUIVALENCE_UNPROVEN:UI_CLEANUP_EQUIVALENCE_MISMATCH"
    )


def test_observer_receipt_preserves_cleanup_lineage_without_raw_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_ui_mainline()
    cleanup = {
        "schema_version": interaction.CLEANUP_RECEIPT_SCHEMA,
        "receipt_id": "uic-1",
        "status": "ACCEPTED",
        "reason_code": "",
        "raw_state_included": False,
    }

    def fake_observer(envelope: dict) -> dict:
        interaction._LAST_CLEANUP_CONTEXT.set({
            "cleanup_receipt": cleanup,
            "interaction_count": 2,
            "cleanup_interaction_count": 1,
        })
        return _receipt(
            observer_id=formal_ui_surface.OBSERVER_ID,
            status="OBSERVED",
            evidence={
                formal_ui_surface.EVIDENCE_KEY: {
                    "expectation_satisfied": False,
                    "violation_observed": True,
                },
            },
        )

    monkeypatch.setattr(
        formal_ui_surface,
        interaction.ORIGINAL_OBSERVER,
        fake_observer,
    )
    receipt = interaction._observer_with_cleanup_evidence({})
    evidence = receipt["evidence"][formal_ui_surface.EVIDENCE_KEY]

    assert evidence["cleanup_equivalence_required"] is True
    assert evidence["cleanup_equivalence_accepted"] is True
    assert evidence["cleanup_receipt"]["receipt_id"] == "uic-1"
    assert evidence["cleanup_receipt"]["raw_state_included"] is False
    assert evidence["interaction_count"] == 2
    assert evidence["cleanup_interaction_count"] == 1
