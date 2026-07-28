from __future__ import annotations

import json

from ai_test_asset_center.enterprise_knowledge_center._formal_ui_contracts import (
    extract_formal_ui_contracts,
)
from ai_test_asset_center.professional_ui_interaction_privacy_guard import (
    EVIDENCE_POLICY,
)
from ai_test_asset_center.professional_ui_persistent_cleanup_probe import (
    EQUIVALENCE_SCOPE,
    PERSISTENT_PROBE_PROPERTY,
)


def _envelope(browser_plan: dict, *, execution_mode: str) -> dict:
    return {
        "contract_id": "ui-professional-source-contract",
        "title": "Source-declared professional UI contract",
        "operation_ref": "get-records-page",
        "actor_ref": "qa-operator",
        "ui_request": {
            "request_id": "ui-professional-source-contract",
            "provider": "playwright_browser_plan",
            "start_url": "/records",
            "execution_mode": execution_mode,
            "browser_plan": browser_plan,
        },
    }


def test_professional_readonly_contract_enters_enterprise_source_parser() -> None:
    contract = _envelope(
        {
            "execution_mode": "safe_read_only",
            "steps": [
                {"action": "goto", "url": "/records"},
                {"action": "set_viewport", "width": 390, "height": 844},
                {
                    "action": "expect_accessibility_basics",
                    "rules": ["html_lang", "buttons_have_name"],
                    "max_violations": 0,
                },
                {"action": "expect_no_horizontal_overflow"},
            ],
        },
        execution_mode="safe_read_only",
    )

    contracts, gaps = extract_formal_ui_contracts(
        json.dumps({"ui_formal_contracts": [contract]}),
        source_id="ui-spec-professional",
    )

    assert gaps == []
    assert len(contracts) == 1
    parsed = contracts[0]
    assert parsed["schema_version"] == "qualibug.ui-formal-contract.v2"
    assert parsed["ui_request"]["execution_mode"] == "safe_read_only"
    assert [
        row["action"]
        for row in parsed["ui_request"]["browser_plan"]["steps"]
    ] == [
        "goto",
        "set_viewport",
        "expect_accessibility_basics",
        "expect_no_horizontal_overflow",
    ]
    assert parsed["source_refs"][0]["source_id"] == "ui-spec-professional"


def test_governed_interaction_contract_enters_enterprise_source_parser() -> None:
    browser_plan = {
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
            {"probe_id": "record-count-ui", "property": "count", "selector": ".row"},
            {
                "probe_id": "record-count-persistent",
                "property": PERSISTENT_PROBE_PROPERTY,
                "method": "GET",
                "url": "/api/records/summary",
                "json_pointer": "/count",
                "expected_status_class": 2,
                "max_response_bytes": 100_000,
            },
        ],
        "steps": [
            {"phase": "setup", "action": "goto", "url": "/records"},
            {
                "phase": "treatment",
                "action": "fill",
                "selector": "#name",
                "value_ref": "test_name",
            },
            {
                "phase": "treatment",
                "action": "click",
                "selector": "#save",
            },
            {
                "phase": "assertion",
                "action": "expect_text",
                "selector": "#result",
                "text": "Saved",
            },
            {
                "phase": "cleanup",
                "action": "click",
                "selector": "#delete-test-record",
            },
        ],
    }
    contract = _envelope(
        browser_plan,
        execution_mode="approved_sandbox_write",
    )

    contracts, gaps = extract_formal_ui_contracts(
        json.dumps({"ui_formal_contracts": [contract]}),
        source_id="ui-spec-interaction",
    )

    assert gaps == []
    assert len(contracts) == 1
    parsed_plan = contracts[0]["ui_request"]["browser_plan"]
    assert parsed_plan["write_approved"] is True
    assert parsed_plan["interaction_contract"]["evidence_policy"] == EVIDENCE_POLICY
    assert parsed_plan["interaction_contract"]["equivalence_scope"] == EQUIVALENCE_SCOPE
    assert parsed_plan["state_probes"][1]["property"] == PERSISTENT_PROBE_PROPERTY


def test_interaction_source_without_cleanup_or_privacy_remains_a_visible_gap() -> None:
    browser_plan = {
        "execution_mode": "approved_sandbox_write",
        "write_approved": True,
        "interaction_contract": {
            "cleanup_strategy": "browser_compensation",
            "equivalence": "source_declared_state_probes",
            "target_scope": "approved_nonproduction_target",
        },
        "state_probes": [
            {"probe_id": "record-count", "property": "count", "selector": ".row"},
        ],
        "steps": [
            {"phase": "setup", "action": "goto", "url": "/records"},
            {
                "phase": "treatment",
                "action": "click",
                "selector": "#save",
            },
            {
                "phase": "assertion",
                "action": "expect_text",
                "selector": "#result",
                "text": "Saved",
            },
        ],
    }
    contract = _envelope(
        browser_plan,
        execution_mode="approved_sandbox_write",
    )

    contracts, gaps = extract_formal_ui_contracts(
        json.dumps({"ui_formal_contracts": [contract]}),
        source_id="ui-spec-incomplete-interaction",
    )

    assert contracts == []
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap["reason_code"] == "FORMAL_UI_CONTRACT_INCOMPLETE"
    assert (
        f"interaction_contract.evidence_policy={EVIDENCE_POLICY}"
        in gap["missing_requirements"]
    )
    assert "cleanup_interaction" in gap["missing_requirements"]


def test_interaction_source_without_persistent_probe_remains_a_visible_gap() -> None:
    browser_plan = {
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
            {"probe_id": "record-count-ui", "property": "count", "selector": ".row"},
        ],
        "steps": [
            {"phase": "setup", "action": "goto", "url": "/records"},
            {"phase": "treatment", "action": "click", "selector": "#save"},
            {
                "phase": "assertion",
                "action": "expect_text",
                "selector": "#result",
                "text": "Saved",
            },
            {
                "phase": "cleanup",
                "action": "click",
                "selector": "#delete-test-record",
            },
        ],
    }

    contracts, gaps = extract_formal_ui_contracts(
        json.dumps({
            "ui_formal_contracts": [
                _envelope(browser_plan, execution_mode="approved_sandbox_write")
            ]
        }),
        source_id="ui-spec-no-persistent-probe",
    )

    assert contracts == []
    assert gaps[0]["missing_requirements"] == [
        "browser_plan.persistent_state_probe"
    ]


def test_readonly_contract_with_click_is_not_silently_upgraded_to_write() -> None:
    contract = _envelope(
        {
            "execution_mode": "safe_read_only",
            "steps": [
                {"phase": "setup", "action": "goto", "url": "/records"},
                {
                    "phase": "treatment",
                    "action": "click",
                    "selector": "#save",
                },
                {
                    "phase": "assertion",
                    "action": "expect_text",
                    "selector": "#result",
                    "text": "Saved",
                },
            ],
        },
        execution_mode="safe_read_only",
    )

    contracts, gaps = extract_formal_ui_contracts(
        json.dumps({"ui_formal_contracts": [contract]}),
        source_id="ui-spec-smuggled-write",
    )

    assert contracts == []
    requirements = gaps[0]["missing_requirements"]
    assert "execution_mode=approved_sandbox_write" in requirements
    assert "browser_plan.write_approved=true" in requirements
    assert "cleanup_interaction" in requirements
