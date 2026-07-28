from __future__ import annotations

import json

import pytest

from ai_test_asset_center import discovery_runtime_semantic_binding as _runtime  # noqa: F401
from ai_test_asset_center import professional_ui_complex_interactions as complex_ui
from ai_test_asset_center import professional_ui_interaction_cleanup as interaction
from ai_test_asset_center.enterprise_knowledge_center._formal_ui_contracts import (
    extract_formal_ui_contracts,
)
from ai_test_asset_center.professional_ui_interaction_privacy_guard import EVIDENCE_POLICY
from ai_test_asset_center.professional_ui_persistent_cleanup_probe import (
    EQUIVALENCE_SCOPE,
    PERSISTENT_PROBE_PROPERTY,
)


def test_runtime_rejects_frame_origin_with_path() -> None:
    with pytest.raises(
        interaction._browser.BrowserExecutionError,
        match="browser_frame_origin_exact_http_origin_required",
    ):
        complex_ui._validate_frame_scope({
            "frame_selector": "iframe#billing",
            "frame_origin": "https://example.test/path",
        })


def test_runtime_rejects_non_origin_popup_allowlist_entry() -> None:
    with pytest.raises(RuntimeError, match="EXACT_ORIGIN_REQUIRED"):
        complex_ui._approved_origins(
            {
                "approved_base_url": "https://example.test/app",
                "approved_popup_origins": ["https://partner.test/path"],
            },
            "approved_popup_origins",
        )


def test_source_rejects_frame_scoped_persistent_http_probe() -> None:
    contract = {
        "contract_id": "ui-frame-persistent-probe",
        "operation_ref": "update-record",
        "actor_ref": "qa-operator",
        "ui_request": {
            "request_id": "ui-frame-persistent-probe",
            "provider": "playwright_browser_plan",
            "start_url": "/records",
            "execution_mode": interaction.WRITE_MODE,
            "browser_plan": {
                "execution_mode": interaction.WRITE_MODE,
                "write_approved": True,
                "interaction_contract": {
                    "cleanup_strategy": "browser_compensation",
                    "equivalence": "source_declared_state_probes",
                    "equivalence_scope": EQUIVALENCE_SCOPE,
                    "target_scope": "approved_nonproduction_target",
                    "evidence_policy": EVIDENCE_POLICY,
                },
                "state_probes": [{
                    "probe_id": "persistent-count",
                    "property": PERSISTENT_PROBE_PROPERTY,
                    "method": "GET",
                    "url": "/api/records/summary",
                    "json_pointer": "/count",
                    "expected_status_class": 2,
                    "frame_selector": "iframe#records",
                    "frame_origin": "https://example.test",
                }],
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
                    {
                        "phase": "cleanup",
                        "action": "click",
                        "selector": "#delete-test-record",
                    },
                ],
            },
        },
    }

    contracts, gaps = extract_formal_ui_contracts(
        json.dumps({"ui_formal_contracts": [contract]}),
        source_id="ui-frame-persistent-source",
    )

    assert contracts == []
    assert any(
        "persistent_probe_no_frame_scope" in requirement
        for requirement in gaps[0]["missing_requirements"]
    )
