from __future__ import annotations

import json

from ai_test_asset_center.enterprise_campaign import (
    EnterpriseCampaign,
    EnterpriseCampaignStore,
    has_real_confirmation_receipt,
    source_snapshot_hash,
)


def _campaign(project: str = "enterprise-project", source: str = "api-v1", source_id: str = "api-spec") -> EnterpriseCampaign:
    snapshot = source_snapshot_hash("requirements-v1", source, "schema-v1", "service-scope", "test-environment")
    return EnterpriseCampaign.create(
        project,
        "service-scope",
        "test-environment",
        snapshot,
        source_id=source_id,
        source_hash=f"hash-{source}",
        policy_version="policy-v1",
        slice_budget=15,
        automatic_round_limit=2,
    )


def test_same_scope_and_snapshot_resume_the_same_campaign(tmp_path):
    first = _campaign()
    store = EnterpriseCampaignStore(tmp_path, "enterprise-project")
    opened, mode = store.open_or_create(first)
    store.save(opened)
    resumed, resumed_mode = store.open_or_create(_campaign())
    assert mode == "created"
    assert resumed_mode == "resumed"
    assert resumed.campaign_id == opened.campaign_id
    assert resumed.run_count == 2


def test_source_snapshot_change_creates_a_new_campaign(tmp_path):
    store = EnterpriseCampaignStore(tmp_path, "enterprise-project")
    first, _ = store.open_or_create(_campaign(source="api-v1"))
    store.save(first)
    changed, mode = store.open_or_create(_campaign(source="api-v2"))
    assert mode == "created"
    assert changed.campaign_id != first.campaign_id
    assert changed.source_snapshot_hash != first.source_snapshot_hash


def test_source_identity_is_persisted_and_part_of_campaign_identity(tmp_path):
    store = EnterpriseCampaignStore(tmp_path, "enterprise-project")
    first, _ = store.open_or_create(_campaign(source_id="source-a"))
    store.save(first)
    changed, mode = store.open_or_create(_campaign(source_id="source-b"))
    assert mode == "created"
    assert changed.campaign_id != first.campaign_id
    assert first.public_contract()["source_id"] == "source-a"
    assert first.public_contract()["source_hash"] == "hash-api-v1"


def test_campaign_defaults_source_identity_to_snapshot_when_single_asset_is_not_supplied():
    snapshot = source_snapshot_hash("requirements-v1", "api-v1", "schema-v1", "service-scope", "test-environment")
    campaign = EnterpriseCampaign.create("enterprise-project", "service-scope", "test-environment", snapshot)
    restored = EnterpriseCampaign.from_dict({
        "campaign_id": campaign.campaign_id,
        "project_id": campaign.project_id,
        "scope_id": campaign.scope_id,
        "environment_ref": campaign.environment_ref,
        "source_snapshot_hash": snapshot,
    })
    assert campaign.source_hash == snapshot
    assert campaign.source_id == f"source_snapshot:{snapshot[:24]}"
    assert restored.source_hash == snapshot
    assert restored.source_id == f"source_snapshot:{snapshot[:24]}"


def test_campaign_store_projects_safe_governance_to_command_center_snapshot(tmp_path):
    campaign = _campaign()
    campaign.record_cycle(
        round_number=1,
        selection={"stop_reason": "configured_round_limit_reached", "selected_slice_ids": ["BHV_1"], "remaining_slice_count": 4},
        findings=[],
        coverage_gap_count=2,
        execution_status="blocked",
    )
    store = EnterpriseCampaignStore(tmp_path, "enterprise-project")
    store.save(campaign)
    snapshot_path = tmp_path / "platform_outputs" / "enterprise-project" / "real_project" / "real_project_defect_data.json"
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    projected = payload["continuous_discovery_campaign"]

    assert projected["campaign"]["campaign_id"] == campaign.campaign_id
    assert projected["summary"]["campaign_state"] == "coverage_deferred"
    assert projected["current_run"]["remaining_slice_count"] == 4
    assert "attempted_slice_ids" not in projected["campaign"]
    assert "confirmation_receipts" not in projected["campaign"]
    assert campaign.public_contract()["attempted_slice_count"] == 0
    assert campaign.public_contract()["round_count"] == 0


def test_confirmed_slice_requires_complete_real_receipt():
    incomplete = {
        "behavior_slice_id": "BHV_1",
        "execution_status": "executed",
        "confirmation_status": "confirmed",
        "gate_passed": True,
        "evidence": {"request": "r", "response": "s"},
    }
    complete = {
        "behavior_slice_id": "BHV_1",
        "execution_status": "executed",
        "confirmation_status": "confirmed",
        "gate_passed": True,
        "evidence": {
            "request": "r",
            "response": "s",
            "assertion": "a",
            "timestamp": "2026-07-06T00:00:00Z",
            "target": "approved-target",
            "actor": "approved-actor",
            "reproduction_steps": ["step"],
        },
    }
    assert not has_real_confirmation_receipt(incomplete)
    assert has_real_confirmation_receipt(complete)


def test_terminal_campaign_stays_deferred_when_observed_again():
    campaign = _campaign()
    campaign.status = "coverage_deferred"
    campaign.coverage_deferred_reason = "configured_round_limit_reached"
    campaign.record_cycle(
        round_number=2,
        selection={"stop_reason": "campaign_coverage_deferred", "selected_slice_ids": [], "remaining_slice_count": 3},
        findings=[],
        coverage_gap_count=1,
        execution_status="stopped",
    )
    assert campaign.status == "coverage_deferred"
    assert campaign.coverage_deferred_reason == "configured_round_limit_reached"
