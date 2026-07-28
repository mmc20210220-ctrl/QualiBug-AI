from __future__ import annotations

import copy
import json

import pytest

from ai_test_asset_center import discovery_runtime_semantic_binding as _runtime  # noqa: F401
from ai_test_asset_center import formal_ui_surface as formal
from ai_test_asset_center import observer_contracts_base as observers
from ai_test_asset_center import professional_ui_accessibility_engine as engine
from ai_test_asset_center import professional_ui_accessibility_observation_guard as observation_guard
from ai_test_asset_center import professional_ui_browser_matrix as matrix
from ai_test_asset_center import professional_ui_coverage_projection as coverage
from ai_test_asset_center import professional_ui_readonly as professional
from ai_test_asset_center.enterprise_knowledge_center._formal_ui_contracts import (
    extract_formal_ui_contracts,
)
from ai_test_asset_center.professional_ui_accessibility_contract_guard import (
    CUSTOM_STANDARD,
)
from ai_test_asset_center.professional_ui_accessibility_matrix_guard import (
    apply_final_accessibility_gate,
)


_RUNTIME_CONTRACT = {
    "status": "approved",
    "approved_base_url": "https://example.test",
}


def _plan(step: dict[str, object]) -> dict[str, object]:
    return {
        "execution_mode": "safe_read_only",
        "steps": [
            {"action": "goto", "url": "/records"},
            step,
        ],
    }


def _source_contract(step: dict[str, object]) -> dict[str, object]:
    return {
        "contract_id": "ui-accessibility-contract",
        "title": "Accessibility authority",
        "operation_ref": "get-records-page",
        "actor_ref": "qa-operator",
        "ui_request": {
            "request_id": "ui-accessibility-contract",
            "provider": "playwright_browser_plan",
            "start_url": "/records",
            "execution_mode": "safe_read_only",
            "browser_plan": _plan(step),
        },
    }


def _validate(step: dict[str, object]) -> dict[str, object]:
    normalized = professional.validate_professional_browser_plan(
        _plan(step),
        _RUNTIME_CONTRACT,
    )
    return normalized["steps"][1]


def _empty_focus() -> dict[str, object]:
    return {
        "findings": [],
        "untestable": [],
        "checked": 0,
        "candidate_count": 0,
        "truncated": False,
    }


def test_accessibility_action_requires_explicit_standard_or_rules() -> None:
    with pytest.raises(
        professional._browser.BrowserExecutionError,
        match="browser_accessibility_standard_or_rules_missing",
    ):
        _validate({"action": engine.ACTION})


def test_full_deterministic_standard_expands_high_confidence_subset() -> None:
    normalized = _validate({
        "action": engine.ACTION,
        "standard": engine.STANDARD,
    })

    assert normalized["standard"] == engine.STANDARD
    assert tuple(normalized["rules"]) == tuple(engine.STANDARD_RULES)
    assert set(normalized["rules"]).issubset(engine.RULE_CATALOG)
    assert set(engine.CUSTOM_ONLY_RULES).isdisjoint(normalized["rules"])
    assert normalized["max_violations"] == 0
    assert all(value == 0 for value in normalized["impact_budgets"].values())
    assert normalized["require_complete_scan"] is True


def test_full_standard_cannot_be_weakened_by_subset_or_exclusion() -> None:
    with pytest.raises(
        professional._browser.BrowserExecutionError,
        match="browser_accessibility_standard_rule_set_mismatch",
    ):
        _validate({
            "action": engine.ACTION,
            "standard": engine.STANDARD,
            "rules": ["document_title"],
        })

    with pytest.raises(
        professional._browser.BrowserExecutionError,
        match="browser_accessibility_standard_exclusions_forbidden",
    ):
        _validate({
            "action": engine.ACTION,
            "standard": engine.STANDARD,
            "exclude_selectors": ["#third-party-widget"],
        })

    with pytest.raises(
        professional._browser.BrowserExecutionError,
        match="browser_accessibility_standard_zero_budget_required",
    ):
        _validate({
            "action": engine.ACTION,
            "standard": engine.STANDARD,
            "max_violations": 1,
        })


def test_source_declared_rule_subset_is_not_mislabelled_as_full_standard() -> None:
    normalized = _validate({
        "action": engine.ACTION,
        "rules": ["buttons_have_name", "focus_visible", "target_size_minimum"],
    })

    assert normalized["standard"] == CUSTOM_STANDARD
    assert normalized["rules"] == [
        "buttons_have_name",
        "focus_visible",
        "target_size_minimum",
    ]
    assert normalized["require_complete_scan"] is True


def test_source_parser_accepts_valid_full_and_custom_authority() -> None:
    full, full_gaps = extract_formal_ui_contracts(
        json.dumps({
            "ui_formal_contracts": [
                _source_contract({
                    "action": engine.ACTION,
                    "standard": engine.STANDARD,
                })
            ]
        }),
        source_id="ui-accessibility-full",
    )
    custom, custom_gaps = extract_formal_ui_contracts(
        json.dumps({
            "ui_formal_contracts": [
                _source_contract({
                    "action": engine.ACTION,
                    "rules": ["buttons_have_name", "links_have_name"],
                })
            ]
        }),
        source_id="ui-accessibility-custom",
    )

    assert full_gaps == []
    assert custom_gaps == []
    assert len(full) == 1
    assert len(custom) == 1
    assert full[0]["ui_request"]["browser_plan"]["steps"][1]["standard"] == engine.STANDARD
    assert custom[0]["ui_request"]["browser_plan"]["steps"][1]["rules"] == [
        "buttons_have_name",
        "links_have_name",
    ]


def test_source_parser_rejects_weakened_full_standard_before_execution() -> None:
    contracts, gaps = extract_formal_ui_contracts(
        json.dumps({
            "ui_formal_contracts": [
                _source_contract({
                    "action": engine.ACTION,
                    "standard": engine.STANDARD,
                    "exclude_selectors": ["#ignored"],
                })
            ]
        }),
        source_id="ui-accessibility-weakened",
    )

    assert contracts == []
    assert len(gaps) == 1
    assert (
        "expect_accessibility_rules[1].standard_exclusions_forbidden"
        in gaps[0]["missing_requirements"]
    )


def test_incomplete_accessibility_observation_is_preserved_not_promoted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = _validate({
        "action": engine.ACTION,
        "rules": ["text_contrast_minimum"],
    })
    monkeypatch.setattr(engine, "_dom_audit", lambda _page, _step: {
        "findings": [],
        "untestable": [{
            "rule": "text_contrast_minimum",
            "reason": "complex_or_translucent_background",
            "count": 2,
        }],
        "visited": 20,
        "total": 20,
        "truncated": False,
    })
    monkeypatch.setattr(engine, "_focus_audit", lambda _page, _step: _empty_focus())
    token = observation_guard._OBSERVATIONS.set([])
    try:
        receipt = observation_guard._execute_with_preserved_observation(None, step)
        captured = copy.deepcopy(observation_guard._OBSERVATIONS.get())
    finally:
        observation_guard._OBSERVATIONS.reset(token)

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["complete_observation"] is False
    assert receipt["untestable_counts_by_rule"] == {"text_contrast_minimum": 2}
    assert receipt["raw_dom_included"] is False
    assert receipt["raw_page_text_included"] is False
    assert receipt["raw_accessible_names_included"] is False
    assert receipt["ai_accessibility_judgement_used"] is False
    assert captured[0]["status"] == "INDETERMINATE"


def test_accessibility_violation_uses_typed_ui_failure_and_minimized_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = _validate({
        "action": engine.ACTION,
        "rules": ["buttons_have_name"],
    })
    monkeypatch.setattr(engine, "_dom_audit", lambda _page, _step: {
        "findings": [{
            "rule": "buttons_have_name",
            "node": {"tag": "button", "id": "save", "path": "body>button"},
            "detail": "",
        }],
        "untestable": [],
        "visited": 10,
        "total": 10,
        "truncated": False,
    })
    monkeypatch.setattr(engine, "_focus_audit", lambda _page, _step: _empty_focus())
    token = observation_guard._OBSERVATIONS.set([])
    try:
        with pytest.raises(
            professional.ProfessionalUIExpectationError,
            match="UI_EXPECTATION_UNSATISFIED:expect_accessibility_rules:",
        ):
            observation_guard._execute_with_preserved_observation(None, step)
        captured = copy.deepcopy(observation_guard._OBSERVATIONS.get())
    finally:
        observation_guard._OBSERVATIONS.reset(token)

    assert captured[0]["status"] == "VIOLATION_OBSERVED"
    assert captured[0]["violation_counts_by_rule"] == {"buttons_have_name": 1}
    assert len(captured[0]["violation_fingerprints"]) == 1
    serialized = json.dumps(captured[0], ensure_ascii=False)
    assert "body>button" not in serialized
    assert "save" not in serialized


def test_accessibility_observation_carries_matrix_profile_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = _validate({
        "action": engine.ACTION,
        "rules": ["document_title"],
    })
    monkeypatch.setattr(engine, "_dom_audit", lambda _page, _step: {
        "findings": [],
        "untestable": [],
        "visited": 5,
        "total": 5,
        "truncated": False,
    })
    monkeypatch.setattr(engine, "_focus_audit", lambda _page, _step: _empty_focus())
    profile_token = matrix._ACTIVE_PROFILE.set({
        "profile_id": "firefox-desktop",
        "browser_engine": "firefox",
        "device_class": "desktop",
    })
    observations_token = observation_guard._OBSERVATIONS.set([])
    try:
        observation_guard._execute_with_preserved_observation(None, step)
        captured = copy.deepcopy(observation_guard._OBSERVATIONS.get())
    finally:
        observation_guard._OBSERVATIONS.reset(observations_token)
        matrix._ACTIVE_PROFILE.reset(profile_token)

    assert captured[0]["matrix_profile_id"] == "firefox-desktop"
    assert captured[0]["browser_engine"] == "firefox"
    assert captured[0]["device_class"] == "desktop"


def test_final_matrix_gate_keeps_incomplete_accessibility_indeterminate() -> None:
    original = observers._receipt(
        observer_id=formal.OBSERVER_ID,
        status="OBSERVED",
        evidence={
            formal.EVIDENCE_KEY: {
                "expectation_satisfied": True,
                "violation_observed": False,
                "browser_matrix": {"status": "ALL_PROFILES_EXECUTED"},
                "accessibility_rule_observations": [{
                    "status": "INDETERMINATE",
                    "complete_observation": False,
                    "matrix_profile_id": "webkit-mobile",
                }],
            }
        },
    )

    gated = apply_final_accessibility_gate(original)

    assert gated["status"] == "INDETERMINATE"
    assert gated["reason_code"] == "UI_ACCESSIBILITY_OBSERVATION_INCOMPLETE"
    evidence = gated["evidence"][formal.EVIDENCE_KEY]
    assert evidence["expectation_satisfied"] is None
    assert evidence["violation_observed"] is False
    assert gated["receipt_id"] != original["receipt_id"]
    assert observers.validate_observer_receipt(gated) == gated


def test_accessibility_rule_projection_uses_unified_dimension() -> None:
    observation = {
        "schema_version": engine.SCHEMA_VERSION,
        "status": "OBSERVED",
        "standard": CUSTOM_STANDARD,
        "rules": ["buttons_have_name"],
        "complete_observation": True,
        "violation_counts_by_rule": {},
        "violation_counts_by_impact": {},
        "violation_counts_by_wcag": {},
        "untestable_counts_by_rule": {},
        "untestable_reason_counts": {},
        "dom_node_count": 12,
        "dom_nodes_evaluated": 12,
        "keyboard_candidate_count": 0,
        "keyboard_candidates_evaluated": 0,
        "ai_accessibility_judgement_used": False,
        "full_wcag_certification_claimed": False,
    }
    receipt = observers._receipt(
        observer_id=formal.OBSERVER_ID,
        status="OBSERVED",
        evidence={
            formal.EVIDENCE_KEY: {
                "expectation_satisfied": True,
                "violation_observed": False,
                "accessibility_rule_observations": [observation],
            }
        },
    )
    result = {
        "test_obligations": {
            "obligations": [{
                "obligation_id": "obl-accessibility",
                "risk_family": formal.RISK_FAMILY,
                "property": {
                    "ui_request": {
                        "browser_plan": _plan({
                            "action": engine.ACTION,
                            "rules": ["buttons_have_name"],
                        })
                    }
                },
            }]
        },
        "obligation_attempt_ledger": {"attempts": []},
        "experiment_execution": {
            "results": {
                "obl-accessibility": {
                    "obligation_id": "obl-accessibility",
                    "observer_receipts": [receipt],
                }
            }
        },
    }

    projected = coverage.build_professional_ui_coverage(result)

    assert projected["dimensions"]["accessibility"]["declared_contract_count"] == 1
    assert projected["declared_assertion_action_counts"][engine.ACTION] == 1
    assert projected["accessibility_rules"]["declared_contract_count"] == 1
    assert projected["accessibility_rules"]["observation_count"] == 1
    assert projected["accessibility_rules"]["complete_observation_count"] == 1
    assert projected["accessibility_rules"]["raw_dom_in_receipts"] is False
    assert projected["capability_boundary"][
        "deterministic_accessibility_rule_engine_supported"
    ] is True
    assert projected["capability_boundary"]["full_accessibility_certification_claimed"] is False
