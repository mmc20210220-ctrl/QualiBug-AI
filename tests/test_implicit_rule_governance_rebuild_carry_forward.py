from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center import composition


def test_rebuild_carries_only_durable_implicit_rule_governance(monkeypatch, tmp_path):
    previous = {
        "asset_id": "knowledge_asset:previous",
        "implicit_rule_lifecycle_ledger": {
            "items": [
                {
                    "rule_id": "implicit_rule_old",
                    "status": "ACTIVE",
                    "rule_snapshot": {"rule_id": "implicit_rule_old"},
                }
            ]
        },
        "implicit_rule_authority_decision_ledger": {
            "items": [{"decision_id": "decision:old", "status": "APPLIED"}]
        },
        "implicit_rule_runtime_evolution": {
            "receipt_id": "runtime:old",
            "rules": [],
        },
        "latest_implicit_rule_runtime_evolution": {
            "receipt_id": "runtime:latest",
            "rules": [],
        },
        "rule_library": [{"rule_id": "implicit_rule_old"}],
        "business_fact_ledger": {"items": [{"fact_id": "fact:old"}]},
        "relationships": [{"edge_id": "edge:old"}],
        "enterprise_understanding_model": {"model_id": "model:old"},
        "probe_catalog": [{"probe_id": "probe:old"}],
    }
    monkeypatch.setattr(
        composition._base_api,
        "load_enterprise_business_knowledge_asset",
        lambda project_id, root: deepcopy(previous),
    )

    captured = composition._capture_previous_implicit_rule_governance(
        "project-a", tmp_path
    )
    fresh = {
        "asset_id": "knowledge_asset:fresh",
        "rule_library": [{"rule_id": "current-explicit-rule"}],
        "business_fact_ledger": {"items": [{"fact_id": "fact:current"}]},
        "relationships": [{"edge_id": "edge:current"}],
        "enterprise_understanding_model": {"model_id": "model:current"},
    }
    composition._restore_previous_implicit_rule_governance(fresh, captured)

    assert captured["previous_asset_id"] == "knowledge_asset:previous"
    assert set(captured["fields"]) == {
        "implicit_rule_lifecycle_ledger",
        "implicit_rule_authority_decision_ledger",
        "implicit_rule_runtime_evolution",
        "latest_implicit_rule_runtime_evolution",
    }
    assert fresh["implicit_rule_lifecycle_ledger"] == previous[
        "implicit_rule_lifecycle_ledger"
    ]
    assert fresh["implicit_rule_authority_decision_ledger"] == previous[
        "implicit_rule_authority_decision_ledger"
    ]
    assert fresh["implicit_rule_runtime_evolution"]["receipt_id"] == "runtime:old"
    assert fresh["latest_implicit_rule_runtime_evolution"]["receipt_id"] == (
        "runtime:latest"
    )

    assert fresh["rule_library"] == [{"rule_id": "current-explicit-rule"}]
    assert fresh["business_fact_ledger"] == {
        "items": [{"fact_id": "fact:current"}]
    }
    assert fresh["relationships"] == [{"edge_id": "edge:current"}]
    assert fresh["enterprise_understanding_model"] == {"model_id": "model:current"}
    assert "probe_catalog" not in fresh

    receipt = fresh["implicit_rule_governance_carry_forward_receipt"]
    assert receipt["status"] == "RESTORED"
    assert receipt["captured_before_base_rebuild"] is True
    assert receipt["prior_rule_library_reused"] is False
    assert receipt["prior_business_fact_ledger_reused"] is False
    assert receipt["prior_relationships_reused"] is False
    assert receipt["prior_enterprise_understanding_model_reused"] is False
    assert receipt["prior_probe_catalog_reused"] is False


def test_first_build_records_no_previous_governance(monkeypatch, tmp_path):
    monkeypatch.setattr(
        composition._base_api,
        "load_enterprise_business_knowledge_asset",
        lambda project_id, root: None,
    )
    captured = composition._capture_previous_implicit_rule_governance(
        "project-a", tmp_path
    )
    fresh = {"asset_id": "knowledge_asset:first"}

    composition._restore_previous_implicit_rule_governance(fresh, captured)

    receipt = fresh["implicit_rule_governance_carry_forward_receipt"]
    assert receipt["status"] == "NO_PREVIOUS_GOVERNANCE_STATE"
    assert receipt["restored_field_count"] == 0
