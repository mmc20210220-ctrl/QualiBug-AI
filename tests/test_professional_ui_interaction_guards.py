from __future__ import annotations

import json
from typing import Any

import pytest

from ai_test_asset_center import formal_ui_surface
from ai_test_asset_center import formal_ui_surface_guard
from ai_test_asset_center import professional_ui_interaction_cleanup as interaction
from ai_test_asset_center import professional_ui_interaction_privacy_guard as privacy
from ai_test_asset_center import professional_ui_readonly
from ai_test_asset_center import professional_ui_responsive_accessibility
from ai_test_asset_center import scan_ui_contract_overlay as scan_overlay
from ai_test_asset_center.enterprise_knowledge_center._formal_ui_contracts import (
    extract_formal_ui_contracts,
)
from ai_test_asset_center.professional_ui_contract_guard import (
    install_professional_ui_contract_guard,
)
from ai_test_asset_center.professional_ui_coverage_projection import (
    build_professional_ui_coverage,
)
from ai_test_asset_center.professional_ui_interaction_contract_guard import (
    install_controlled_ui_interaction_contract_guard,
)
from ai_test_asset_center.professional_ui_interaction_privacy_guard import (
    EVIDENCE_POLICY,
    install_controlled_ui_interaction_privacy_guard,
)
from ai_test_asset_center.professional_ui_persistent_cleanup_probe import (
    EQUIVALENCE_SCOPE,
    MAX_RESPONSE_BYTES,
    PERSISTENT_PROBE_PROPERTY,
    install_persistent_ui_cleanup_probe,
)
from ai_test_asset_center.scan_ui_interaction_contract_guard import (
    install_scan_ui_interaction_contract_guard,
)


def _install_complete_ui_contract_chain() -> None:
    formal_ui_surface.install_formal_ui_surface()
    formal_ui_surface_guard.install_formal_ui_read_only_guard()
    professional_ui_readonly.install_professional_ui_readonly()
    install_professional_ui_contract_guard()
    professional_ui_responsive_accessibility.install_professional_ui_responsive_accessibility()
    interaction.install_controlled_ui_interaction()
    install_controlled_ui_interaction_contract_guard()
    install_controlled_ui_interaction_privacy_guard()
    install_persistent_ui_cleanup_probe()
    install_scan_ui_interaction_contract_guard()


def _source_ref() -> dict[str, Any]:
    return {
        "source_id": "ui-spec-controlled-workflow",
        "version": "v1",
        "locator": "workflow:create-and-remove-record",
        "kind": "formal_ui_contract",
        "quote_hash": "a" * 64,
    }


def _interaction_plan() -> dict[str, Any]:
    return {
        "execution_mode": "approved_sandbox_write",
        "write_approved": True,
        "interaction_contract": {
            "cleanup_strategy": "browser_compensation",
            "equivalence": "source_declared_state_probes",
            "equivalence_scope": EQUIVALENCE_SCOPE,
            "target_scope": "approved_nonproduction_target",
            "evidence_policy": EVIDENCE_POLICY,
        },
        "state_probes": [
            {
                "probe_id": "record-count-ui",
                "property": "count",
                "selector": "[data-testid=record-row]",
            },
            {
                "probe_id": "record-count-persistent",
                "property": PERSISTENT_PROBE_PROPERTY,
                "method": "GET",
                "url": "/api/records/summary",
                "json_pointer": "/data/count",
                "expected_status_class": 2,
                "max_response_bytes": 100_000,
            },
        ],
        "steps": [
            {"phase": "setup", "action": "goto", "url": "/records"},
            {
                "phase": "treatment",
                "action": "fill",
                "selector": "#record-name",
                "value_ref": "test_record_name",
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
        ],
    }


def _scan_request() -> dict[str, Any]:
    return {
        "request_id": "ui-controlled-workflow",
        "title": "Create, verify and remove one test record",
        "provider": "playwright_browser_plan",
        "start_url": "https://example.test/records",
        "execution_mode": "approved_sandbox_write",
        "operation_ref": "get-records-page",
        "actor_role": "public",
        "source_refs": [_source_ref()],
        "browser_plan": _interaction_plan(),
    }


def test_enterprise_source_rejects_request_plan_execution_mode_drift() -> None:
    _install_complete_ui_contract_chain()
    request = _scan_request()
    request["browser_plan"]["execution_mode"] = "safe_read_only"
    raw = {
        "contract_id": request["request_id"],
        "operation_ref": request["operation_ref"],
        "actor_role": request["actor_role"],
        "ui_request": request,
    }

    contracts, gaps = extract_formal_ui_contracts(
        json.dumps({"ui_formal_contracts": [raw]}),
        source_id="ui-spec-mode-drift",
    )

    assert contracts == []
    assert gaps[0]["reason_code"] == "FORMAL_UI_CONTRACT_INCOMPLETE"
    assert gaps[0]["missing_requirements"] == [
        "ui_request_and_browser_plan_execution_mode_match"
    ]


def test_direct_scan_accepts_complete_governed_interaction_contract() -> None:
    _install_complete_ui_contract_chain()

    asset, receipt = scan_overlay.overlay_scan_ui_contracts(
        {},
        {"ui_execution_requests": [_scan_request()]},
    )

    assert receipt["status"] == "OVERLAID"
    assert receipt["contract_added_count"] == 1
    assert receipt["coverage_gap_count"] == 0
    contract = asset["ui_formal_contracts"][0]
    plan = contract["ui_request"]["browser_plan"]
    assert plan["interaction_contract"]["equivalence_scope"] == EQUIVALENCE_SCOPE
    assert any(
        row["property"] == PERSISTENT_PROBE_PROPERTY
        for row in plan["state_probes"]
    )


def test_direct_scan_rejects_mode_drift_before_behavior_ir() -> None:
    _install_complete_ui_contract_chain()
    request = _scan_request()
    request["browser_plan"]["execution_mode"] = "safe_read_only"

    asset, receipt = scan_overlay.overlay_scan_ui_contracts(
        {},
        {"ui_execution_requests": [request]},
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["contract_added_count"] == 0
    assert asset["ui_formal_contracts"] == []
    gap = asset["coverage_gaps"][0]
    assert gap["reason_code"] == "FORMAL_UI_EXECUTION_MODE_MISMATCH"
    assert gap["missing_requirements"] == [
        "ui_request_and_browser_plan_execution_mode_match"
    ]


def test_direct_scan_rejects_missing_persistent_cleanup_probe() -> None:
    _install_complete_ui_contract_chain()
    request = _scan_request()
    request["browser_plan"]["state_probes"] = [
        row
        for row in request["browser_plan"]["state_probes"]
        if row["property"] != PERSISTENT_PROBE_PROPERTY
    ]

    asset, receipt = scan_overlay.overlay_scan_ui_contracts(
        {},
        {"ui_execution_requests": [request]},
    )

    assert receipt["status"] == "BLOCKED"
    gap = asset["coverage_gaps"][0]
    assert gap["reason_code"] == "FORMAL_UI_INTERACTION_CONTRACT_INCOMPLETE"
    assert gap["missing_requirements"] == [
        "browser_plan.persistent_state_probe"
    ]


def test_interaction_privacy_scrubs_artifact_fields_and_untyped_errors() -> None:
    raw = {
        "status": "failed",
        "reason": "PlaywrightError: typed password secret-value into #password",
        "trace_ref": "browser_runs/run/trace.zip",
        "har_ref": "browser_runs/run/network.har",
        "console": [{"type": "error", "text": "token=secret-value"}],
        "network": [{
            "method": "POST",
            "status": 500,
            "url": "https://example.test/api/save?token=secret-value",
        }],
    }

    scrubbed = privacy._scrub_result(raw)
    serialized = json.dumps(scrubbed, sort_keys=True)

    assert "secret-value" not in serialized
    assert scrubbed["reason"].startswith("UI_INTERACTION_RUNTIME_ERROR:")
    assert scrubbed["trace_ref"] == ""
    assert scrubbed["har_ref"] == ""
    assert "text" not in scrubbed["console"][0]
    assert "url" not in scrubbed["network"][0]
    assert scrubbed["evidence_privacy"]["trace_persisted"] is False
    assert scrubbed["evidence_privacy"]["har_persisted"] is False
    assert scrubbed["evidence_privacy"][
        "runtime_exception_text_persisted"
    ] is False


def test_interaction_privacy_preserves_typed_formal_reason_codes() -> None:
    reason = "UI_EXPECTATION_UNSATISFIED:expect_text:value_mismatch"
    scrubbed = privacy._scrub_result({"reason": reason})
    assert scrubbed["reason"] == reason


class _FakeContext:
    pass


class _FakeBrowser:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    def new_context(self, **kwargs: Any) -> _FakeContext:
        self.kwargs = dict(kwargs)
        return _FakeContext()


def test_privacy_browser_removes_har_configuration_but_keeps_storage_state() -> None:
    raw_browser = _FakeBrowser()
    wrapped = privacy._PrivacyBrowser(raw_browser)

    wrapped.new_context(
        record_har_path="network.har",
        record_har_content="embed",
        record_har_mode="full",
        storage_state="project/auth.json",
    )

    assert "record_har_path" not in raw_browser.kwargs
    assert "record_har_content" not in raw_browser.kwargs
    assert "record_har_mode" not in raw_browser.kwargs
    assert raw_browser.kwargs["storage_state"] == "project/auth.json"


class _FakeResponse:
    def __init__(self, *, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def body(self) -> bytes:
        return self._body


class _FakeRequest:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, int]] = []

    def get(self, url: str, *, timeout: int) -> _FakeResponse:
        self.calls.append((url, timeout))
        return self.response


class _FakePage:
    def __init__(self, response: _FakeResponse) -> None:
        self.request = _FakeRequest(response)


def _persistent_probe() -> dict[str, Any]:
    return {
        "probe_id": "record-count-persistent",
        "property": PERSISTENT_PROBE_PROPERTY,
        "method": "GET",
        "url": "https://example.test/api/records/summary",
        "json_pointer": "/data/count",
        "expected_status_class": 2,
        "max_response_bytes": MAX_RESPONSE_BYTES,
        "timeout_ms": 2500,
    }


def test_persistent_probe_receipt_contains_only_fingerprints() -> None:
    _install_complete_ui_contract_chain()
    page = _FakePage(
        _FakeResponse(status=200, body=b'{"data":{"count":2,"secret":"x"}}')
    )

    receipt = interaction._probe_material(page, _persistent_probe())
    serialized = json.dumps(receipt, sort_keys=True)

    assert receipt["property"] == PERSISTENT_PROBE_PROPERTY
    assert receipt["status_class"] == 2
    assert receipt["raw_response_included"] is False
    assert receipt["raw_selected_value_included"] is False
    assert "count" not in receipt
    assert "secret" not in serialized
    assert page.request.calls == [
        ("https://example.test/api/records/summary", 2500)
    ]


def test_persistent_probe_rejects_non_json_or_non_success_response() -> None:
    _install_complete_ui_contract_chain()
    with pytest.raises(
        RuntimeError,
        match=r"^UI_PERSISTENT_PROBE_STATUS_CLASS_INVALID$",
    ):
        interaction._probe_material(
            _FakePage(_FakeResponse(status=500, body=b'{"data":{"count":2}}')),
            _persistent_probe(),
        )

    with pytest.raises(
        RuntimeError,
        match=r"^UI_PERSISTENT_PROBE_JSON_INVALID$",
    ):
        interaction._probe_material(
            _FakePage(_FakeResponse(status=200, body=b"not-json")),
            _persistent_probe(),
        )


def _coverage_result(*, cleanup_status: str) -> dict[str, Any]:
    obligation_id = "ui-interaction-obligation"
    return {
        "test_obligations": {
            "obligations": [{
                "obligation_id": obligation_id,
                "risk_family": "ui_state_consistency",
                "property": {
                    "ui_cleanup_authority": {
                        "equivalence_required": True,
                    },
                    "ui_request": {
                        "browser_plan": _interaction_plan(),
                    },
                },
            }],
        },
        "experiment_execution": {
            "results": {
                obligation_id: {
                    "obligation_id": obligation_id,
                    "observer_receipts": [{
                        "observer_id": "ui_source_expectation_reader",
                        "status": "OBSERVED",
                        "evidence": {
                            "ui_source_expectation": {
                                "cleanup_receipt": {
                                    "status": cleanup_status,
                                },
                            },
                        },
                    }],
                    "oracle_verdict": {"status": "VIOLATION"},
                },
            },
        },
        "obligation_attempt_ledger": {
            "attempts": [{
                "obligation_id": obligation_id,
                "risk_family": "ui_state_consistency",
                "terminal_status": "DELIVERABLE",
                "reason_code": "",
            }],
        },
    }


def test_interaction_coverage_reports_persistent_cleanup_and_privacy_boundary() -> None:
    coverage = build_professional_ui_coverage(
        _coverage_result(cleanup_status="ACCEPTED")
    )

    assert coverage["declared_treatment_interaction_action_counts"] == {
        "click": 1,
        "fill": 1,
    }
    assert coverage["declared_cleanup_interaction_action_counts"] == {
        "click": 1,
    }
    cleanup = coverage["interaction_cleanup_contracts"]
    assert cleanup["declared_interaction_contract_count"] == 1
    assert cleanup["declared_persistent_probe_count"] == 1
    assert cleanup["interaction_without_persistent_probe_count"] == 0
    workflow = coverage["dimensions"]["workflow_interaction"]
    assert workflow["cleanup_equivalence_accepted_count"] == 1
    assert workflow["deliverable_count"] == 1
    invariant = coverage["cleanup_delivery_invariant"]
    assert invariant["invalid_deliverable_without_cleanup_count"] == 0
    boundary = coverage["capability_boundary"]
    assert boundary["persistent_cleanup_probe_required"] is True
    assert boundary["rendered_state_only_cleanup_accepted"] is False
    assert boundary["interaction_evidence_policy"] == EVIDENCE_POLICY
    assert boundary["interactive_har_persisted"] is False
    assert boundary["interactive_trace_persisted"] is False
    assert boundary["universal_backend_restoration_claimed"] is False


def test_coverage_refuses_ledger_deliverable_without_cleanup_equivalence() -> None:
    coverage = build_professional_ui_coverage(
        _coverage_result(cleanup_status="INDETERMINATE")
    )

    workflow = coverage["dimensions"]["workflow_interaction"]
    assert workflow["violation_count"] == 1
    assert workflow["deliverable_count"] == 0
    assert workflow["blocked_or_indeterminate_count"] == 1
    assert workflow["cleanup_equivalence_indeterminate_count"] == 1
    invariant = coverage["cleanup_delivery_invariant"]
    assert invariant["invalid_deliverable_without_cleanup_count"] == 1
    assert invariant["invalid_deliverables_counted_as_deliverable"] is False
    assert coverage["terminal_reason_counts"] == {
        "UI_DELIVERABLE_WITHOUT_CLEANUP_EQUIVALENCE": 1,
    }
