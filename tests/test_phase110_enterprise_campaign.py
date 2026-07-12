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


def test_policy_version_does_not_change_campaign_identity() -> None:
    snapshot = source_snapshot_hash("requirements-v1", "api-v1", "schema-v1", "service-scope", "test-environment")
    first = EnterpriseCampaign.create(
        "enterprise-project",
        "service-scope",
        "test-environment",
        snapshot,
        source_id="api-spec",
        source_hash="hash-api-v1",
        policy_version="policy-v1",
    )
    second = EnterpriseCampaign.create(
        "enterprise-project",
        "service-scope",
        "test-environment",
        snapshot,
        source_id="api-spec",
        source_hash="hash-api-v1",
        policy_version="policy-v2",
    )

    assert first.campaign_id == second.campaign_id


def test_rerun_key_creates_new_campaign_but_preserves_lineage(tmp_path) -> None:
    store = EnterpriseCampaignStore(tmp_path, "enterprise-project")
    baseline, _ = store.open_or_create(_campaign())
    store.save(baseline)

    rerun_candidate = EnterpriseCampaign.create(
        "enterprise-project",
        "service-scope",
        "test-environment",
        baseline.source_snapshot_hash,
        source_id=baseline.source_id,
        source_hash=baseline.source_hash,
        policy_version="policy-v1",
        rerun_key="priority-strategy-v2",
        rerun_reason="re-evaluate selection strategy",
    )
    rerun, mode = store.open_or_create(rerun_candidate)
    store.save(rerun)
    resumed, resumed_mode = store.open_or_create(rerun_candidate)

    assert mode == "created"
    assert resumed_mode == "resumed"
    assert rerun.campaign_id != baseline.campaign_id
    assert rerun.lineage_campaign_id == baseline.campaign_id
    assert rerun.public_contract()["rerun_key"] == "priority-strategy-v2"
    assert rerun.public_contract()["rerun_reason"] == "re-evaluate selection strategy"
    assert resumed.campaign_id == rerun.campaign_id


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


def test_campaign_identity_contract_is_separate_from_legacy_completion_projection() -> None:
    campaign = _campaign()

    identity = campaign.identity_contract()
    public = campaign.public_contract()

    assert identity["campaign_id"] == campaign.campaign_id
    assert identity["source_snapshot_hash"] == campaign.source_snapshot_hash
    assert "status" not in identity
    assert "attempted_slice_count" not in identity
    assert public["completion_authority"] == "legacy_behavior_slice_compatibility"
    assert public["completion_is_formal"] is False


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


def test_confirmed_slice_accepts_modern_v12_receipt_shape() -> None:
    issue = {
        "behavior_slice_id": "BHV_2",
        "execution_status": "executed",
        "confirmation_status": "confirmed",
        "gate_passed": True,
        "actor": "qa_lead",
        "timestamp": "2026-07-07T12:00:00Z",
        "reproduction_steps": ["POST /api/refunds", "observe forbidden state transition accepted"],
        "failed_assertions": ["禁止的状态转换应被阻止"],
        "raw_evidence": {
            "timestamp": "2026-07-07T12:00:00Z",
            "request_raw": {"method": "POST", "path": "/api/refunds", "actor": "qa_lead"},
            "response_raw": {"status_code": 201, "body": "{\"ok\":true}"},
        },
        "reproduction": {"method": "POST", "path": "/api/refunds"},
    }

    assert has_real_confirmation_receipt(issue)


def test_confirmed_slice_accepts_db_snapshot_backed_receipt_shape() -> None:
    issue = {
        "behavior_slice_id": "BHV_3",
        "execution_status": "executed",
        "confirmation_status": "confirmed",
        "gate_passed": True,
        "actor": "qa_lead",
        "timestamp": "2026-07-07T12:00:00Z",
        "reproduction_steps": ["POST /api/orders", "observe database row count changed unexpectedly"],
        "failed_assertions": ["订单写入后数据库状态异常"],
        "raw_evidence": {
            "timestamp": "2026-07-07T12:00:00Z",
            "request_raw": {"method": "POST", "path": "/api/orders", "actor": "qa_lead"},
            "db_snapshot": {
                "table": "orders",
                "assertion": "orders row count changed 1->2",
                "before": {"row_count": 1},
                "after": {"row_count": 2},
            },
        },
        "reproduction": {"method": "POST", "path": "/api/orders"},
    }

    assert has_real_confirmation_receipt(issue)


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


def test_campaign_does_not_complete_when_selected_slices_were_not_all_attempted() -> None:
    campaign = _campaign()

    campaign.record_cycle(
        round_number=1,
        selection={
            "stop_reason": "selected_final_unattempted_slice_batch",
            "selected_slice_ids": ["BHV_attempted", "BHV_not_attempted"],
            "remaining_slice_count": 0,
            "next_round": None,
        },
        findings=[],
        coverage_gap_count=0,
        execution_status="completed",
        attempted_slice_ids=["BHV_attempted"],
    )

    assert campaign.status == "active"
    assert campaign.public_contract()["attempted_slice_count"] == 1
    assert campaign.audit_events[-1]["selected_unattempted"] == 1


def test_campaign_completes_when_no_slices_remain_even_without_full_confirmation():
    campaign = _campaign()
    campaign.record_cycle(
        round_number=1,
        selection={"stop_reason": "selected_final_available_slice_batch", "selected_slice_ids": ["BHV_1"], "remaining_slice_count": 0, "next_round": None},
        findings=[],
        coverage_gap_count=0,
        execution_status="completed",
        attempted_slice_ids=["BHV_1"],
    )
    assert campaign.status == "completed"
    assert campaign.coverage_deferred_reason == ""


def test_campaign_history_item_carries_confirmed_slice_ids_for_same_campaign_scheduler_feedback():
    campaign = _campaign()
    campaign.attempted_slice_ids = ["BHV_1", "BHV_2"]
    campaign.confirmation_receipts = {"BHV_2": "receipt_2"}
    history = campaign.history_item()

    assert history["behavior_slice_ledger"]["attempted_slice_ids"] == ["BHV_1", "BHV_2"]
    assert history["behavior_slice_ledger"]["confirmed_slice_ids"] == ["BHV_2"]
