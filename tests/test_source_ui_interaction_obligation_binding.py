from __future__ import annotations

from typing import Any

from ai_test_asset_center.formal_ui_surface import install_formal_ui_surface
from ai_test_asset_center.professional_ui_interaction_privacy_guard import (
    EVIDENCE_POLICY,
)
from ai_test_asset_center.professional_ui_persistent_cleanup_probe import (
    EQUIVALENCE_SCOPE,
    PERSISTENT_PROBE_PROPERTY,
)
from ai_test_asset_center.source_ui_obligation_binding import (
    compile_obligations_with_source_ui,
)


def _base_compile(_: dict[str, Any]) -> dict[str, Any]:
    return {
        "obligations": [{
            "obligation_id": "misclassified-ui-obligation",
            "risk_family": "validation",
            "property": {"invariant_ref": "inv-ui"},
        }],
        "coverage_gaps": [],
    }


def _request(*, interactive: bool = True, persistent: bool = True) -> dict[str, Any]:
    if not interactive:
        return {
            "provider": "playwright_browser_plan",
            "start_url": "https://example.test/records",
            "execution_mode": "safe_read_only",
            "browser_plan": {
                "execution_mode": "safe_read_only",
                "steps": [
                    {"action": "goto", "url": "/records"},
                    {"action": "expect_text", "selector": "h1", "text": "Records"},
                ],
            },
        }
    probes: list[dict[str, Any]] = [
        {"probe_id": "row-count", "property": "count", "selector": ".row"},
    ]
    if persistent:
        probes.append({
            "probe_id": "row-count-persistent",
            "property": PERSISTENT_PROBE_PROPERTY,
            "method": "GET",
            "url": "/api/records/summary",
            "json_pointer": "/count",
            "expected_status_class": 2,
            "max_response_bytes": 100_000,
        })
    return {
        "provider": "playwright_browser_plan",
        "start_url": "https://example.test/records",
        "execution_mode": "approved_sandbox_write",
        "browser_plan": {
            "execution_mode": "approved_sandbox_write",
            "write_approved": True,
            "interaction_contract": {
                "cleanup_strategy": "browser_compensation",
                "equivalence": "source_declared_state_probes",
                "equivalence_scope": EQUIVALENCE_SCOPE,
                "target_scope": "approved_nonproduction_target",
                "evidence_policy": EVIDENCE_POLICY,
            },
            "state_probes": probes,
            "steps": [
                {"phase": "setup", "action": "goto", "url": "/records"},
                {
                    "phase": "treatment",
                    "action": "click",
                    "selector": "#create-record",
                },
                {
                    "phase": "assertion",
                    "action": "expect_text",
                    "selector": "#result",
                    "text": "Created",
                },
                {
                    "phase": "cleanup",
                    "action": "click",
                    "selector": "#remove-test-record",
                },
            ],
        },
    }


def _behavior_ir(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "operations": [{
            "id": "op-records-page",
            "method": "GET",
            "path": "/records",
            "confidence": 1.0,
            "source_refs": [{"source_id": "api-spec"}],
        }],
        "actors": [{
            "id": "actor-public",
            "role": "public",
            "confidence": 1.0,
            "source_refs": [{"source_id": "role-spec"}],
        }],
        "invariants": [{
            "id": "inv-ui",
            "status": "accepted",
            "confidence": 1.0,
            "expression": {
                "kind": "ui_source_expectation",
                "operator": "must_render_source_expectation",
            },
            "operation_refs": ["op-records-page"],
            "ui_actor_ref": "actor-public",
            "ui_contract_id": "ui-contract-1",
            "ui_request": request,
            "ui_expectation_actions": ["expect_text"],
            "source_refs": [{"source_id": "ui-spec"}],
        }],
        "relations": [{
            "id": "rel-ui-observes",
            "relation_type": "observes",
            "from_ref": "inv-ui",
            "to_ref": "op-records-page",
            "operation_ref": "op-records-page",
            "actor_ref": "actor-public",
            "source_refs": [{"source_id": "ui-spec"}],
        }],
    }


def test_interactive_obligation_carries_complete_cleanup_authority() -> None:
    install_formal_ui_surface()

    result = compile_obligations_with_source_ui(
        _behavior_ir(_request()),
        base_compile=_base_compile,
    )

    assert result["obligation_count"] == 1
    obligation = result["obligations"][0]
    cleanup = obligation["cleanup_requirement"]
    assert cleanup["required"] is False
    assert cleanup["delegated"] is True
    assert cleanup["equivalence_scope"] == EQUIVALENCE_SCOPE
    assert cleanup["persistent_probe_required"] is True
    assert cleanup["persistent_probe_count"] == 1
    assert cleanup["evidence_policy"] == EVIDENCE_POLICY
    authority = obligation["property"]["ui_cleanup_authority"]
    assert authority["contract_complete"] is True
    assert authority["persistent_probe_count"] == 1
    assert authority["rendered_state_only_cleanup_accepted"] is False
    assert authority["generic_http_cleanup_must_not_run"] is True
    receipt = result["source_ui_obligation_receipt"]
    assert receipt["interactive_obligation_count"] == 1
    assert receipt["read_only_obligation_count"] == 0
    assert receipt["persistent_probe_count"] == 1
    assert receipt["interactive_cleanup_equivalence_scope"] == EQUIVALENCE_SCOPE
    assert receipt["interactive_evidence_policy"] == EVIDENCE_POLICY


def test_historical_interaction_without_persistent_probe_does_not_compile() -> None:
    install_formal_ui_surface()

    result = compile_obligations_with_source_ui(
        _behavior_ir(_request(persistent=False)),
        base_compile=_base_compile,
    )

    assert result["obligation_count"] == 0
    receipt = result["source_ui_obligation_receipt"]
    assert receipt["status"] == "BLOCKED"
    assert receipt["interactive_obligation_count"] == 0
    assert receipt["skipped_reason_counts"] == {
        "FORMAL_UI_INTERACTION_CLEANUP_AUTHORITY_INCOMPLETE": 1,
    }
    assert result["coverage_gaps"][0]["code"] == (
        "FORMAL_UI_INTERACTION_CLEANUP_AUTHORITY_INCOMPLETE"
    )


def test_readonly_ui_obligation_remains_cleanup_free() -> None:
    install_formal_ui_surface()

    result = compile_obligations_with_source_ui(
        _behavior_ir(_request(interactive=False)),
        base_compile=_base_compile,
    )

    assert result["obligation_count"] == 1
    obligation = result["obligations"][0]
    assert obligation["cleanup_requirement"] == {
        "required": False,
        "mode": "not_required_read_only",
        "reason": "read_only_ui_contract",
    }
    authority = obligation["property"]["ui_cleanup_authority"]
    assert authority["mode"] == "not_required_read_only"
    assert authority["equivalence_required"] is False
    assert authority["contract_complete"] is True
    receipt = result["source_ui_obligation_receipt"]
    assert receipt["interactive_obligation_count"] == 0
    assert receipt["read_only_obligation_count"] == 1
    assert receipt["persistent_probe_count"] == 0
