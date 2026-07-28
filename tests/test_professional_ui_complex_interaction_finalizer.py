from __future__ import annotations

import pytest

from ai_test_asset_center import discovery_runtime_semantic_binding as _runtime  # noqa: F401
from ai_test_asset_center import formal_ui_surface as formal
from ai_test_asset_center import professional_ui_complex_interactions as complex_ui
from ai_test_asset_center import professional_ui_coverage_projection as coverage
from ai_test_asset_center import professional_ui_interaction_cleanup as interaction
from ai_test_asset_center.professional_ui_persistent_cleanup_probe import (
    PERSISTENT_PROBE_PROPERTY,
)


def test_persistent_http_probe_cannot_claim_iframe_scope() -> None:
    with pytest.raises(
        interaction._browser.BrowserExecutionError,
        match="browser_persistent_probe_frame_scope_forbidden",
    ):
        interaction._validate_probe(
            {
                "probe_id": "persistent-count",
                "property": PERSISTENT_PROBE_PROPERTY,
                "method": "GET",
                "url": "/api/summary",
                "json_pointer": "/count",
                "expected_status_class": 2,
                "frame_selector": "iframe#billing",
                "frame_origin": "https://example.test",
            },
            set(),
        )


def test_complex_interactions_are_projected_in_unified_workflow_dimension() -> None:
    result = {
        "test_obligations": {
            "obligations": [{
                "obligation_id": "obl-complex-ui",
                "risk_family": formal.RISK_FAMILY,
                "property": {
                    "ui_request": {
                        "browser_plan": {
                            "steps": [
                                {
                                    "phase": "treatment",
                                    "action": complex_ui.SET_INPUT_FILES,
                                    "selector": "input[type=file]",
                                    "file_refs": ["fixture"],
                                },
                                {
                                    "phase": "treatment",
                                    "action": complex_ui.CLICK_DOWNLOAD,
                                    "selector": "#download",
                                    "delete_after_observation": True,
                                },
                                {
                                    "phase": "assertion",
                                    "action": "expect_text",
                                    "selector": "#result",
                                    "text": "Ready",
                                },
                                {
                                    "phase": "cleanup",
                                    "action": complex_ui.SET_INPUT_FILES,
                                    "selector": "input[type=file]",
                                    "file_refs": [],
                                },
                            ]
                        }
                    }
                },
            }]
        },
        "obligation_attempt_ledger": {"attempts": []},
        "experiment_execution": {"results": {}},
    }

    projected = coverage.build_professional_ui_coverage(result)

    workflow = projected["dimensions"]["workflow_interaction"]
    assert workflow["declared_contract_count"] == 1
    assert projected["declared_treatment_interaction_action_counts"] == {
        complex_ui.CLICK_DOWNLOAD: 1,
        complex_ui.SET_INPUT_FILES: 1,
    }
    assert projected["declared_cleanup_interaction_action_counts"] == {
        complex_ui.SET_INPUT_FILES: 1,
    }
    complex_projection = projected["complex_interactions"]
    assert complex_projection["declared_treatment_count"] == 2
    assert complex_projection["declared_cleanup_count"] == 1
    assert complex_projection["download_raw_content_persisted"] is False
    assert complex_projection[
        "download_or_popup_mismatch_is_formal_violation_v1"
    ] is False
    boundary = projected["capability_boundary"]
    assert boundary["governed_file_upload_supported"] is True
    assert boundary["governed_download_observation_supported"] is True
    assert boundary["governed_popup_observation_supported"] is True
    assert boundary["iframe_scoped_interaction_supported"] is True
    assert boundary["complex_interaction_cleanup_equivalence_required"] is True
    assert boundary["complex_interaction_mismatch_is_formal_violation_v1"] is False
