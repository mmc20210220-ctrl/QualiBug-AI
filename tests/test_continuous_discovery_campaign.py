from __future__ import annotations

import json
from pathlib import Path

from ai_test_asset_center.continuous_discovery_campaign import record_continuous_discovery_campaign_run


def test_continuous_discovery_campaign_records_semantics_state_machine_and_frontier_ledger(tmp_path: Path) -> None:
    probes = [
        {
            "probe_id": "PROBE-ORDER-CREATE",
            "entity": "order",
            "method": "POST",
            "path": "/api/orders",
            "risk_type": "order_create",
            "actor_role": "sales",
            "priority_score": 0.92,
        },
        {
            "probe_id": "PROBE-REFUND-APPROVAL",
            "entity": "refund",
            "method": "POST",
            "path": "/api/refunds/123/approve",
            "risk_type": "refund",
            "actor_role": "finance",
            "state_transition": "requested->approved",
            "priority_score": 0.75,
            "blocked": True,
            "blocker": "environment_not_ready",
        },
    ]
    issues = [
        {
            "issue_id": "ISSUE-ORDER-PENDING",
            "entity": "order",
            "request": {"method": "POST", "url": "/api/orders"},
            "risk_type": "order_create",
            "actor_role": "sales",
            "severity": "P1",
            "validated_bug_accounting": {
                "accounting_state": "pending",
                "strict_validated_bug": False,
                "verifier_passed": True,
                "has_reproduction": False,
                "has_evidence_refs": False,
                "blocker_reason_codes": ["missing_reproduction", "missing_evidence_refs"],
                "primary_blocker_reason_code": "missing_reproduction",
            },
        }
    ]

    report = record_continuous_discovery_campaign_run(
        "campaign-demo",
        tmp_path,
        probes,
        issues,
        trigger="scheduled_round",
        run_context={"mode": "safe"},
    )

    assert report["status"] == "ready"
    assert report["campaign"]["semantic_model"]["campaign"].startswith("Long-lived continuous discovery")
    assert set(report["campaign"]["state_machine"]["states"]) == {
        "scheduled",
        "active",
        "blocked",
        "completed",
        "paused",
    }
    assert report["summary"]["campaign_state"] == "scheduled"
    assert report["summary"]["coverage_ledger_entry_count"] == 2
    assert report["summary"]["remaining_actionable_frontier_count"] == 1
    assert report["summary"]["blocked_frontier_count"] == 1
    assert report["summary"]["reporting_basis"] == "validated_bug"
    assert report["current_run"]["continue_campaign"] is True
    assert report["current_run"]["state_path"] == ["scheduled", "active", "scheduled"]
    assert report["coverage_ledger"]["path"].endswith("continuous_discovery_campaign.json")

    entries = {entry["frontier"]["title"]: entry for entry in report["coverage_ledger"]["entries"]}
    pending_entry = entries["POST /api/orders :: order_create"]
    blocked_entry = entries["POST /api/refunds/{id}/approve :: refund"]

    assert pending_entry["last_status"] == "pending"
    assert pending_entry["semantic_dimensions"]["actor_role"] == "sales"
    assert pending_entry["frontier"]["blocker_reason"] == "missing_reproduction"
    assert pending_entry["frontier"]["evidence_maturity"] == "needs_reproduction"
    assert pending_entry["frontier"]["next_action"].startswith("Add deterministic reproduction")

    assert blocked_entry["last_status"] == "blocked"
    assert blocked_entry["semantic_dimensions"]["state_transition"] == "requested->approved"
    assert blocked_entry["frontier"]["blocker_reason"] == "environment_not_ready"
    assert blocked_entry["frontier"]["last_run_result"] == "blocked_execution"

    assert report["recommended_frontier"][0]["status"] == "pending"
    assert "Pending frontier still has verifier, repro or evidence gaps." in report["current_run"]["continue_conditions"]


def test_continuous_discovery_campaign_dashboard_distinguishes_run_and_cumulative_strict_metrics(tmp_path: Path) -> None:
    first_report = record_continuous_discovery_campaign_run(
        "dashboard-metrics-demo",
        tmp_path,
        [
            {
                "entity": "order",
                "method": "POST",
                "path": "/api/orders",
                "risk_type": "order_create",
                "actor_role": "sales",
                "priority_score": 0.93,
            }
        ],
        [
            {
                "entity": "order",
                "request": {"method": "POST", "url": "/api/orders"},
                "risk_type": "order_create",
                "actor_role": "sales",
                "severity": "P1",
                "validated_bug_accounting": {
                    "accounting_state": "pending",
                    "strict_validated_bug": False,
                    "verifier_passed": True,
                    "has_reproduction": False,
                    "has_evidence_refs": False,
                    "primary_blocker_reason_code": "missing_reproduction",
                },
            }
        ],
        trigger="scheduled_round",
        run_context={"mode": "safe"},
    )

    assert first_report["summary"]["this_run_new_validated_bug_count"] == 0
    assert first_report["summary"]["cumulative_validated_bug_count"] == 0
    assert first_report["summary"]["pending_to_validated_conversion_rate"] == 0.0
    assert first_report["summary"]["remaining_high_value_uncovered_behavior_count"] == 1
    assert first_report["summary"]["revalidation_queue_size"] == 0
    assert first_report["dashboard"]["strict_reporting"]["formal_summary_uses_strict_validated_bug_only"] is True

    second_report = record_continuous_discovery_campaign_run(
        "dashboard-metrics-demo",
        tmp_path,
        [
            {
                "entity": "order",
                "method": "POST",
                "path": "/api/orders",
                "risk_type": "order_create",
                "actor_role": "sales",
                "priority_score": 0.93,
            }
        ],
        [
            {
                "entity": "order",
                "request": {"method": "POST", "url": "/api/orders"},
                "risk_type": "order_create",
                "actor_role": "sales",
                "severity": "P0",
                "validated_bug_accounting": {
                    "accounting_state": "validated",
                    "strict_validated_bug": True,
                    "verifier_passed": True,
                    "has_reproduction": True,
                    "has_evidence_refs": True,
                    "primary_blocker_reason_code": "",
                },
            }
        ],
        trigger="scheduled_round",
        run_context={"mode": "safe"},
    )

    dashboard = second_report["dashboard"]
    assert second_report["summary"]["this_run_new_validated_bug_count"] == 1
    assert second_report["summary"]["cumulative_validated_bug_count"] == 1
    assert second_report["summary"]["pending_to_validated_conversion_count"] == 1
    assert second_report["summary"]["pending_to_validated_conversion_rate"] == 1.0
    assert second_report["summary"]["frontier_burn_down_count"] == 1
    assert second_report["summary"]["remaining_high_value_uncovered_behavior_count"] == 0
    assert second_report["summary"]["can_stop_now"] is True
    assert dashboard["frontier_burn_down"]["open_frontier_before_run"] == 1
    assert dashboard["frontier_burn_down"]["open_frontier_after_run"] == 0
    assert dashboard["campaign_totals"]["cumulative_validated_bug_count"] == 1
    assert dashboard["strict_reporting"]["reporting_basis"] == "validated_bug"
    assert dashboard["stop_decision"]["threshold_reached"] is False
    assert dashboard["stop_decision"]["remaining_risks"] == [
        "当前没有显式 frontier 债务，后续只需等待新的 revalidation trigger 或新增行为面。"
    ]


def test_continuous_discovery_campaign_marks_prior_validated_frontier_for_revalidation(tmp_path: Path) -> None:
    first_report = record_continuous_discovery_campaign_run(
        "revalidation-demo",
        tmp_path,
        [
            {
                "entity": "invoice",
                "method": "POST",
                "path": "/api/invoices",
                "risk_type": "double_charge",
                "actor_role": "billing",
                "priority_score": 0.88,
            }
        ],
        [
            {
                "entity": "invoice",
                "request": {"method": "POST", "url": "/api/invoices"},
                "risk_type": "double_charge",
                "actor_role": "billing",
                "severity": "P0",
                "validated_bug_accounting": {
                    "accounting_state": "validated",
                    "strict_validated_bug": True,
                    "verifier_passed": True,
                    "has_reproduction": True,
                    "has_evidence_refs": True,
                    "blocker_reason_codes": [],
                    "primary_blocker_reason_code": "",
                },
            }
        ],
        trigger="scheduled_round",
    )
    assert first_report["summary"]["validated_frontier_count"] == 1
    assert first_report["summary"]["campaign_state"] == "completed"

    second_report = record_continuous_discovery_campaign_run(
        "revalidation-demo",
        tmp_path,
        [],
        [],
        trigger="knowledge_asset_updated",
    )

    assert second_report["summary"]["revalidate_due_count"] == 1
    assert second_report["summary"]["campaign_state"] == "scheduled"
    assert second_report["recommended_frontier"][0]["status"] == "revalidate_due"
    assert second_report["current_run"]["trigger"] == "knowledge_asset_updated"
    assert "Revalidation debt exists after an environment, knowledge or data trigger." in second_report["current_run"]["continue_conditions"]

    entry = second_report["coverage_ledger"]["entries"][0]
    assert entry["last_status"] == "revalidate_due"
    assert entry["frontier"]["last_run_result"] == "triggered_revalidation"


def test_continuous_discovery_campaign_builds_frontier_budget_slices_and_auto_schedule(tmp_path: Path) -> None:
    record_continuous_discovery_campaign_run(
        "budget-slices-demo",
        tmp_path,
        [
            {
                "entity": "order",
                "method": "POST",
                "path": "/api/orders",
                "risk_type": "order_create",
                "actor_role": "sales",
                "priority_score": 0.93,
            },
            {
                "entity": "catalog",
                "method": "GET",
                "path": "/api/catalog/items",
                "risk_type": "catalog_read",
                "actor_role": "ops",
                "priority_score": 0.62,
            },
            {
                "entity": "invoice",
                "method": "POST",
                "path": "/api/invoices",
                "risk_type": "double_charge",
                "actor_role": "billing",
                "priority_score": 0.9,
            },
        ],
        [
            {
                "entity": "order",
                "request": {"method": "POST", "url": "/api/orders"},
                "risk_type": "order_create",
                "actor_role": "sales",
                "severity": "P1",
                "validated_bug_accounting": {
                    "accounting_state": "pending",
                    "strict_validated_bug": False,
                    "verifier_passed": True,
                    "has_reproduction": False,
                    "has_evidence_refs": False,
                    "primary_blocker_reason_code": "missing_reproduction",
                },
            },
            {
                "entity": "invoice",
                "request": {"method": "POST", "url": "/api/invoices"},
                "risk_type": "double_charge",
                "actor_role": "billing",
                "severity": "P0",
                "validated_bug_accounting": {
                    "accounting_state": "validated",
                    "strict_validated_bug": True,
                    "verifier_passed": True,
                    "has_reproduction": True,
                    "has_evidence_refs": True,
                },
            },
        ],
        trigger="scheduled_round",
        run_context={"mode": "standard", "frontier_budget": 6},
    )

    second_report = record_continuous_discovery_campaign_run(
        "budget-slices-demo",
        tmp_path,
        [],
        [],
        trigger="knowledge_asset_updated",
        run_context={"mode": "standard", "frontier_budget": 6},
    )

    next_run_plan = second_report["next_run_plan"]
    selected = next_run_plan["selected_frontier"]
    budget_classes = {item["budget_class"] for item in selected}

    assert second_report["summary"]["campaign_state"] == "scheduled"
    assert second_report["automation"]["status"] == "scheduled"
    assert next_run_plan["strategy"] == "frontier_driven_incremental_scheduler"
    assert next_run_plan["budget_slice_counts"]["exploit"] >= 1
    assert next_run_plan["budget_slice_counts"]["explore"] >= 1
    assert next_run_plan["budget_slice_counts"]["revalidate"] >= 1
    assert budget_classes == {"explore", "exploit", "revalidate"}
    assert any(item["status"] == "pending" and item["budget_class"] == "exploit" for item in selected)
    assert any(item["status"] == "revalidate_due" and item["budget_class"] == "revalidate" for item in selected)
    assert any(item["status"] == "untouched" and item["budget_class"] == "explore" for item in selected)
    assert "Stable exploit reserve protects" in " ".join(next_run_plan["selection_summary"]["why_this_round"])


def test_continuous_discovery_campaign_rechecks_blocked_frontier_after_matching_trigger(tmp_path: Path) -> None:
    first_report = record_continuous_discovery_campaign_run(
        "blocked-recheck-demo",
        tmp_path,
        [
            {
                "entity": "refund",
                "method": "POST",
                "path": "/api/refunds/123/approve",
                "risk_type": "refund",
                "actor_role": "finance",
                "priority_score": 0.84,
                "blocked": True,
                "blocker": "environment_not_ready",
            }
        ],
        [],
        trigger="scheduled_round",
        run_context={"mode": "safe", "frontier_budget": 4},
    )
    assert first_report["summary"]["campaign_state"] == "blocked"

    second_report = record_continuous_discovery_campaign_run(
        "blocked-recheck-demo",
        tmp_path,
        [],
        [],
        trigger="environment_recovered",
        run_context={"mode": "safe", "frontier_budget": 4},
    )

    assert second_report["summary"]["campaign_state"] == "scheduled"
    assert second_report["summary"]["remaining_actionable_frontier_count"] == 1
    assert second_report["automation"]["status"] == "scheduled"
    assert second_report["recommended_frontier"][0]["budget_class"] == "revalidate"
    assert second_report["recommended_frontier"][0]["status"] == "blocked"
    assert "matching trigger reopened" in " ".join(second_report["recommended_frontier"][0]["why_selected"]).lower()
    assert second_report["next_run_plan"]["blocked_frontier_watchlist"][0]["wake_conditions"] == [
        "blocker_cleared",
        "environment_recovered",
    ]


def test_continuous_discovery_campaign_benchmark_multi_run_outperforms_single_run_on_validated_bug_and_frontier_convergence(
    tmp_path: Path,
) -> None:
    benchmark_probes = [
        {
            "entity": "order",
            "method": "POST",
            "path": "/api/orders",
            "risk_type": "order_create",
            "actor_role": "sales",
            "priority_score": 0.95,
        },
        {
            "entity": "invoice",
            "method": "POST",
            "path": "/api/invoices",
            "risk_type": "double_charge",
            "actor_role": "billing",
            "priority_score": 0.91,
        },
        {
            "entity": "refund",
            "method": "POST",
            "path": "/api/refunds/123/approve",
            "risk_type": "refund",
            "actor_role": "finance",
            "priority_score": 0.88,
            "blocked": True,
            "blocker": "environment_not_ready",
        },
    ]
    single_run_report = record_continuous_discovery_campaign_run(
        "benchmark-single-run",
        tmp_path,
        benchmark_probes,
        [
            {
                "entity": "order",
                "request": {"method": "POST", "url": "/api/orders"},
                "risk_type": "order_create",
                "actor_role": "sales",
                "severity": "P1",
                "validated_bug_accounting": {
                    "accounting_state": "pending",
                    "strict_validated_bug": False,
                    "verifier_passed": True,
                    "has_reproduction": False,
                    "has_evidence_refs": False,
                    "primary_blocker_reason_code": "missing_reproduction",
                },
            }
        ],
        trigger="scheduled_round",
        run_context={"mode": "standard", "frontier_budget": 6},
    )

    multi_run_first = record_continuous_discovery_campaign_run(
        "benchmark-multi-run",
        tmp_path,
        benchmark_probes,
        [
            {
                "entity": "order",
                "request": {"method": "POST", "url": "/api/orders"},
                "risk_type": "order_create",
                "actor_role": "sales",
                "severity": "P1",
                "validated_bug_accounting": {
                    "accounting_state": "pending",
                    "strict_validated_bug": False,
                    "verifier_passed": True,
                    "has_reproduction": False,
                    "has_evidence_refs": False,
                    "primary_blocker_reason_code": "missing_reproduction",
                },
            }
        ],
        trigger="scheduled_round",
        run_context={"mode": "standard", "frontier_budget": 6},
    )
    multi_run_second = record_continuous_discovery_campaign_run(
        "benchmark-multi-run",
        tmp_path,
        [
            {
                "entity": "order",
                "method": "POST",
                "path": "/api/orders",
                "risk_type": "order_create",
                "actor_role": "sales",
                "priority_score": 0.95,
            },
            {
                "entity": "invoice",
                "method": "POST",
                "path": "/api/invoices",
                "risk_type": "double_charge",
                "actor_role": "billing",
                "priority_score": 0.91,
            },
        ],
        [
            {
                "entity": "order",
                "request": {"method": "POST", "url": "/api/orders"},
                "risk_type": "order_create",
                "actor_role": "sales",
                "severity": "P0",
                "validated_bug_accounting": {
                    "accounting_state": "validated",
                    "strict_validated_bug": True,
                    "verifier_passed": True,
                    "has_reproduction": True,
                    "has_evidence_refs": True,
                    "primary_blocker_reason_code": "",
                },
            },
            {
                "entity": "invoice",
                "request": {"method": "POST", "url": "/api/invoices"},
                "risk_type": "double_charge",
                "actor_role": "billing",
                "severity": "P0",
                "validated_bug_accounting": {
                    "accounting_state": "validated",
                    "strict_validated_bug": True,
                    "verifier_passed": True,
                    "has_reproduction": True,
                    "has_evidence_refs": True,
                    "primary_blocker_reason_code": "",
                },
            },
        ],
        trigger="environment_recovered",
        run_context={"mode": "standard", "frontier_budget": 6},
    )

    assert multi_run_first["summary"]["cumulative_validated_bug_count"] == 0
    assert single_run_report["summary"]["cumulative_validated_bug_count"] == 0
    assert multi_run_second["summary"]["cumulative_validated_bug_count"] == 2
    assert multi_run_second["summary"]["cumulative_validated_bug_count"] > single_run_report["summary"]["cumulative_validated_bug_count"]
    assert (
        multi_run_second["summary"]["remaining_high_value_uncovered_behavior_count"]
        < single_run_report["summary"]["remaining_high_value_uncovered_behavior_count"]
    )
    assert multi_run_second["summary"]["frontier_burn_down_count"] >= 1
    assert multi_run_second["dashboard"]["this_run"]["new_validated_bug_count"] == 2
    assert multi_run_second["dashboard"]["this_run"]["pending_to_validated_conversion_count"] == 1
    assert multi_run_second["dashboard"]["frontier_health"]["revalidation_queue_size"] >= 1
    assert multi_run_second["summary"]["reporting_basis"] == "validated_bug"


def test_real_project_discovery_exposes_continuous_discovery_campaign_contract(tmp_path: Path) -> None:
    from ai_test_asset_center.real_project_defect_discovery import run_real_project_discovery

    project = "continuous-discovery-contract"
    input_dir = tmp_path / "platform_inputs" / project
    input_dir.mkdir(parents=True)
    (input_dir / "real_project_config.json").write_text(
        json.dumps(
            {
                "project_name": "Continuous Discovery Contract",
                "environment": "test",
                "discovery_mode": "safe",
                "max_probe_count": 12,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (input_dir / "openapi.json").write_text(
        json.dumps(
            {
                "openapi": "3.0.0",
                "paths": {
                    "/api/orders": {"get": {"responses": {"200": {"description": "ok"}}}},
                    "/api/orders/{id}": {"get": {"responses": {"200": {"description": "ok"}}}},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = run_real_project_discovery(project, tmp_path)

    campaign = result["continuous_discovery_campaign"]
    assert campaign["status"] == "ready"
    assert campaign["campaign"]["state_machine"]["states"]["scheduled"].startswith("Campaign has more work")
    assert campaign["summary"]["coverage_ledger_entry_count"] >= 1
    assert campaign["summary"]["recommended_frontier_count"] >= 1
    assert campaign["summary"]["reporting_basis"] == "validated_bug"
    assert campaign["dashboard"]["strict_reporting"]["formal_summary_uses_strict_validated_bug_only"] is True
    assert campaign["next_run_plan"]["strategy"] == "frontier_driven_incremental_scheduler"
    assert campaign["automation"]["status"] in {"scheduled", "waiting_for_trigger", "idle"}
    assert campaign["current_run"]["continue_campaign"] is True
    assert result["metrics"]["continuous_discovery_coverage_entries"] == campaign["summary"]["coverage_ledger_entry_count"]
    assert result["metrics"]["continuous_discovery_auto_schedule_status"] == campaign["automation"]["status"]
    assert result["metrics"]["continuous_discovery_reporting_basis"] == "validated_bug"
