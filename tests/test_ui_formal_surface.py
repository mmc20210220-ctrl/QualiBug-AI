from __future__ import annotations

import json

from ai_test_asset_center.discovery_mainline_contract import build_mainline_run_contract
from ai_test_asset_center.ui_formal_runtime import formalize_browser_ui_contracts_strict


def _mainline(environment_id: str = "staging") -> dict:
    return build_mainline_run_contract(
        mainline_authority="experiment_candidate",
        run_id="run-ui-1",
        campaign_id="campaign-ui-1",
        target_id="target-ui-1",
        environment_id=environment_id,
        policy_version="policy-ui-1",
        evaluation_mode="operational",
    )


def _runtime() -> dict:
    return {
        "status": "approved",
        "approved_base_url": "https://example.test",
        "declared_adapters": ["ui_browser"],
    }


def _report(*, title: str = "Order details", reachable: bool = True) -> dict:
    return {
        "schema_version": "browser-ui-smoke-v1",
        "enabled": True,
        "status": "passed",
        "reason_code": "",
        "message": "completed",
        "page_count": 1,
        "reachable_page_count": 1 if reachable else 0,
        "console_error_count": 0,
        "network_error_count": 0,
        "screenshot_count": 1,
        "pages": [{
            "url": "https://example.test/orders/1",
            "reachable": reachable,
            "status_code": 200 if reachable else 500,
            "title": title,
            "duration_ms": 120,
            "screenshot_path": "/private/evidence/must-not-enter-formal-finding.png",
            "console_errors": [],
            "network_errors": [],
            "error": "",
        }],
        "evidence_files": ["/private/evidence/must-not-enter-formal-finding.png"],
    }


def _contract(expected: str = "Order details") -> dict:
    return {
        "contract_id": "ui-order-title",
        "title": "Order page must show its declared title",
        "path": "/orders/1",
        "actor_ref": "actor_order_viewer",
        "severity": "P1",
        "execution_mode": "safe_read_only",
        "adapter": "ui_browser",
        "source_refs": [{
            "source_id": "prd-orders",
            "source_hash": "a" * 64,
            "locator": "REQ-ORDER-UI-12",
        }],
        "success_criteria": {
            "kind": "title_equals",
            "expected": expected,
        },
    }


def test_declared_ui_violation_reaches_delivery_gate() -> None:
    result = formalize_browser_ui_contracts_strict(
        {"mainline_run": _mainline(), "findings": [], "ui_findings": []},
        browser_ui_report=_report(title="Unexpected title"),
        contracts=[_contract(expected="Order details")],
        runtime_contract=_runtime(),
    )

    formal = result["formal_ui_contracts"]
    assert formal["requested"] == 1
    assert formal["deliverable_count"] == 1
    assert formal["provider_findings_promoted"] == 0
    outcome = formal["outcomes"][0]
    assert outcome["status"] == "DELIVERABLE"
    assert outcome["oracle_receipt"]["status"] == "VIOLATION"
    assert outcome["reproduction_receipt"]["status"] == "REPRODUCED"
    finding = result["ui_findings"][0]
    assert finding["surface"] == "UI"
    assert finding["gate_passed"] is True
    assert finding["customer_delivery_status"] == "defect"

    serialized = json.dumps(result, ensure_ascii=False, sort_keys=True)
    assert "must-not-enter-formal-finding.png" not in json.dumps(finding, ensure_ascii=False)
    assert "provider_findings_promoted\": 0" in serialized


def test_property_held_is_rejected_and_creates_no_finding() -> None:
    result = formalize_browser_ui_contracts_strict(
        {"mainline_run": _mainline(), "findings": [], "ui_findings": []},
        browser_ui_report=_report(title="Order details"),
        contracts=[_contract(expected="Order details")],
        runtime_contract=_runtime(),
    )

    formal = result["formal_ui_contracts"]
    assert formal["deliverable_count"] == 0
    assert formal["rejected_count"] == 1
    assert formal["outcomes"][0]["status"] == "REJECTED"
    assert formal["outcomes"][0]["oracle_receipt"]["status"] == "PROPERTY_HELD"
    assert result["findings"] == []
    assert result["ui_findings"] == []


def test_missing_source_refs_blocks_before_execution_authority() -> None:
    contract = _contract()
    contract["source_refs"] = []
    result = formalize_browser_ui_contracts_strict(
        {"mainline_run": _mainline(), "findings": []},
        browser_ui_report=_report(),
        contracts=[contract],
        runtime_contract=_runtime(),
    )

    outcome = result["formal_ui_contracts"]["outcomes"][0]
    assert outcome["status"] == "BLOCKED"
    assert "UI_SOURCE_REFS_MISSING" in outcome["reason_codes"]
    assert result["findings"] == []


def test_undeclared_adapter_blocks_without_inferring_from_playwright_or_url() -> None:
    runtime = _runtime()
    runtime["declared_adapters"] = []
    result = formalize_browser_ui_contracts_strict(
        {"mainline_run": _mainline(), "findings": []},
        browser_ui_report=_report(),
        contracts=[_contract()],
        runtime_contract=runtime,
    )

    outcome = result["formal_ui_contracts"]["outcomes"][0]
    assert outcome["status"] == "BLOCKED"
    assert "UI_BROWSER_ADAPTER_NOT_DECLARED" in outcome["reason_codes"]


def test_production_environment_cannot_publish_ui_finding() -> None:
    result = formalize_browser_ui_contracts_strict(
        {"mainline_run": _mainline("production"), "findings": []},
        browser_ui_report=_report(title="Unexpected title"),
        contracts=[_contract()],
        runtime_contract=_runtime(),
    )

    outcome = result["formal_ui_contracts"]["outcomes"][0]
    assert outcome["status"] == "BLOCKED"
    assert "UI_NONPRODUCTION_ENVIRONMENT_REQUIRED" in outcome["reason_codes"]
    assert result["findings"] == []


def test_unapproved_runtime_contract_blocks_the_whole_formal_chain() -> None:
    runtime = _runtime()
    runtime["status"] = "plan_only"
    result = formalize_browser_ui_contracts_strict(
        {"mainline_run": _mainline(), "findings": []},
        browser_ui_report=_report(title="Unexpected title"),
        contracts=[_contract()],
        runtime_contract=runtime,
    )

    outcome = result["formal_ui_contracts"]["outcomes"][0]
    assert outcome["status"] == "BLOCKED"
    assert outcome["reason_codes"] == ["UI_APPROVED_RUNTIME_CONTRACT_REQUIRED"]
    assert result["findings"] == []
