from __future__ import annotations

from ai_test_asset_center.discovery_ui_loss_projection import (
    build_formal_ui_loss_funnel,
)


def test_professional_ui_contract_is_projected_across_declared_dimensions() -> None:
    result = {
        "behavior_ir": {
            "scan_ui_contract_overlay_receipt": {
                "status": "OVERLAID",
                "formal_candidate_count": 1,
                "contract_added_count": 1,
                "coverage_gap_count": 0,
            },
            "source_ui_contract_binding_receipt": {
                "status": "BOUND",
                "contract_count": 1,
                "bound_invariant_count": 1,
                "coverage_gap_count": 0,
                "reason_counts": {},
            },
        },
        "test_obligations": {
            "source_ui_obligation_receipt": {
                "status": "COMPILED",
                "complete_family_vector": True,
                "skipped_reason_counts": {},
            },
            "obligations": [{
                "obligation_id": "ui-professional-1",
                "risk_family": "ui_state_consistency",
                "property": {
                    "ui_request": {
                        "browser_plan": {
                            "steps": [
                                {"action": "set_viewport", "width": 390, "height": 844},
                                {"action": "expect_text", "selector": "h1", "text": "Orders"},
                                {"action": "expect_visible", "selector": "#orders"},
                                {
                                    "action": "expect_accessibility_basics",
                                    "rules": ["html_lang", "buttons_have_name"],
                                    "max_violations": 0,
                                },
                                {"action": "expect_no_horizontal_overflow"},
                                {"action": "expect_no_failed_requests"},
                            ],
                        },
                    },
                },
            }],
        },
        "experiment_execution": {
            "results": {
                "ui-professional-1": {
                    "obligation_id": "ui-professional-1",
                    "observer_receipts": [{
                        "observer_id": "ui_source_expectation_reader",
                        "status": "OBSERVED",
                        "reason_code": "",
                    }],
                    "oracle_verdict": {"status": "VIOLATION"},
                },
            },
        },
        "obligation_attempt_ledger": {
            "attempts": [{
                "obligation_id": "ui-professional-1",
                "risk_family": "ui_state_consistency",
                "terminal_status": "DELIVERABLE",
                "reason_code": "",
                "stages": [
                    {"stage": "compile", "status": "COMPILED"},
                    {"stage": "execution", "status": "EXECUTED"},
                    {"stage": "gate", "status": "DELIVERABLE"},
                ],
            }],
        },
    }

    funnel = build_formal_ui_loss_funnel(result)
    coverage = funnel["professional_coverage"]

    assert coverage["schema_version"] == "qualibug.professional-ui-coverage.v2"
    assert coverage["declared_assertion_action_counts"] == {
        "expect_accessibility_basics": 1,
        "expect_no_failed_requests": 1,
        "expect_no_horizontal_overflow": 1,
        "expect_text": 1,
        "expect_visible": 1,
    }
    assert coverage["declared_configuration_action_counts"] == {
        "set_viewport": 1,
    }
    assert coverage["declared_treatment_interaction_action_counts"] == {}
    assert coverage["declared_cleanup_interaction_action_counts"] == {}
    for category in (
        "content_navigation",
        "rendered_state",
        "accessibility",
        "layout_responsive",
        "runtime_quality",
    ):
        row = coverage["dimensions"][category]
        assert row["declared_contract_count"] == 1
        assert row["selected_contract_count"] == 1
        assert row["observed_contract_count"] == 1
        assert row["violation_count"] == 1
        assert row["deliverable_count"] == 1

    assert coverage["dimensions"]["visual_regression"][
        "declared_contract_count"
    ] == 0
    assert coverage["dimensions"]["workflow_interaction"][
        "declared_contract_count"
    ] == 0
    assert coverage["visual_baseline_contracts"] == {
        "declared_visual_contract_count": 0,
        "declared_baseline_namespace_counts": {},
        "visual_observation_count": 0,
        "comparable_visual_observation_count": 0,
        "visual_observation_status_counts": {},
        "visual_reason_counts": {},
        "ai_visual_judgement_consumed_count": 0,
        "baseline_scope": "project_approved_visual_baseline",
        "comparison_method": "rgba_max_channel_absolute_difference",
        "allowed_baseline_namespaces": [
            "visual_baselines",
            "approved_visual_baselines",
        ],
        "baseline_auto_update_supported": False,
    }
    # One contract can exercise several professional dimensions, but the formal
    # funnel still contains one violation and one deliverable finding occurrence.
    assert funnel["outcomes"]["violation_count"] == 1
    assert funnel["outcomes"]["deliverable_count"] == 1
    boundary = coverage["capability_boundary"]
    assert boundary["provider_findings_consumed"] is False
    assert boundary["visual_baseline_regression_supported"] is True
    assert boundary["visual_baseline_auto_update_supported"] is False
    assert boundary["visual_provider_or_ai_opinion_used_as_defect"] is False
    assert boundary["controlled_write_interaction_supported"] is True
    assert boundary["production_write_supported"] is False
    assert boundary["browser_cleanup_equivalence_required"] is True
    assert boundary["ai_usability_opinion_used_as_defect"] is False


def test_empty_ui_result_reports_every_professional_dimension_as_uncovered() -> None:
    funnel = build_formal_ui_loss_funnel({
        "behavior_ir": {},
        "test_obligations": {"obligations": []},
        "experiment_execution": {"results": {}},
        "obligation_attempt_ledger": {"attempts": []},
    })

    coverage = funnel["professional_coverage"]
    assert coverage["dimensions_without_declared_contracts"] == [
        "accessibility",
        "content_navigation",
        "layout_responsive",
        "rendered_state",
        "runtime_quality",
        "visual_regression",
        "workflow_interaction",
    ]
    assert all(
        row["declared_contract_count"] == 0
        for row in coverage["dimensions"].values()
    )
