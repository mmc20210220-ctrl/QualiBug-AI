from __future__ import annotations

import json

from ai_test_asset_center.business_state_graph import BusinessStateGraphBuilder
from ai_test_asset_center.policy_wiring import _behavior_slice_execution_value
from ai_test_asset_center.v12_pipeline import (
    _behavior_slice_settings,
    _schedule_behavior_slices,
    run_v12_pipeline,
)


API_SPEC = json.dumps({
    "openapi": "3.0.0",
    "paths": {
        "/api/cases/{case_id}/approve": {"patch": {"operationId": "approveCase"}},
        "/api/cases/{case_id}/reopen": {"patch": {"operationId": "reopenCase"}},
    },
    "components": {
        "schemas": {
            "Case": {
                "type": "object",
                "properties": {
                    "state": {"type": "string", "enum": ["DRAFT", "APPROVED", "CLOSED"]},
                },
            },
        },
    },
}, ensure_ascii=False)

DB_SCHEMA = """
CREATE TABLE cases (
  id TEXT PRIMARY KEY,
  state TEXT CHECK (state IN ('DRAFT', 'APPROVED', 'CLOSED'))
);
"""

PRD = """
# Case lifecycle
DRAFT -> APPROVED by approve

禁止状态流转：
CLOSED -> DRAFT by reopen

# Value constraint
aggregate_value must equal reconciled_value
"""


def test_builder_outputs_only_source_bound_slices_and_explicit_unbound_gap():
    builder = BusinessStateGraphBuilder()
    graphs = builder.build(PRD, API_SPEC, DB_SCHEMA)
    contract = builder.behavior_contract()

    assert set(graphs) == {"case"}
    assert contract["summary"]["total_slices"] >= 2
    transition_slices = [item for item in contract["slices"] if item["kind"] == "transition"]
    assert {item["slice_id"] for item in transition_slices}
    assert all(item["source_refs"] for item in transition_slices)
    assert any(item["endpoints"] for item in transition_slices)
    assert any(gap["kind"] == "UNBOUND_REQUIREMENT" for gap in contract["coverage_gaps"])
    assert all("case" not in gap["title"].lower() for gap in contract["coverage_gaps"])


def test_unique_schema_field_overlap_binds_invariant_without_inventing_state():
    db_schema = """
    CREATE TABLE reconciliations (
      id TEXT PRIMARY KEY,
      aggregate_value NUMERIC,
      reconciled_value NUMERIC
    );
    """
    prd = """
    # Reconciliation constraint
    aggregate_value must equal reconciled_value
    """

    builder = BusinessStateGraphBuilder()
    graphs = builder.build(prd, "", db_schema)
    contract = builder.behavior_contract()

    assert set(graphs) == {"reconciliation"}
    assert contract["summary"]["source_field_bound_invariant_count"] == 1
    invariant_slices = [item for item in contract["slices"] if item["kind"] == "invariant"]
    assert len(invariant_slices) == 1
    assert invariant_slices[0]["entity"] == "reconciliation"
    assert invariant_slices[0]["states"] == []
    assert "STATE_ANCHOR_NOT_SOURCE_BOUND" in invariant_slices[0]["evidence_gaps"]
    assert not contract["coverage_gaps"]


def test_behavior_slice_policy_guardrails_cap_budget_and_round_bounds(monkeypatch):
    monkeypatch.setenv("QUALIBUG_MAX_BEHAVIOR_SLICES_PER_ROUND", "999")
    monkeypatch.setenv("QUALIBUG_DISCOVERY_ROUND", "0")
    monkeypatch.setenv("QUALIBUG_INCREMENTAL_DISCOVERY_ROUND_LIMIT", "999")

    assert _behavior_slice_execution_value("max_behavior_slices_per_round", 999, 15) == 15
    assert _behavior_slice_execution_value("incremental_discovery_round", 0, 1) == 1
    assert _behavior_slice_execution_value("incremental_discovery_round_limit", 999, 3) == 12


def test_slice_budget_is_hard_capped_at_fifteen(monkeypatch):
    monkeypatch.setenv("QUALIBUG_MAX_BEHAVIOR_SLICES_PER_ROUND", "999")
    settings = _behavior_slice_settings()
    assert settings["slice_budget"] == 15


def test_plan_only_slice_is_not_misclassified_as_confirmed():
    selection = _schedule_behavior_slices(
        [{"slice_id": "BHV_example", "entity": "example", "kind": "transition"}],
        {"slice_budget": 1, "round_number": 1, "round_limit": 2},
        [{
            "behavior_slice_id": "BHV_example",
            "execution_status": "not_executed",
            "confirmation_status": "candidate",
            "gate_passed": False,
        }],
    )

    assert selection["status"] == "planned"
    assert selection["confirmed_slice_ids"] == []
    assert selection["selected_slice_ids"] == ["BHV_example"]


def test_history_advances_to_next_unattempted_slice_without_promoting_prior_plan():
    selection = _schedule_behavior_slices(
        [
            {"slice_id": "BHV_first", "entity": "example", "kind": "transition"},
            {"slice_id": "BHV_second", "entity": "example", "kind": "invariant"},
        ],
        {"slice_budget": 1, "round_number": 1, "round_limit": 3},
        [{"behavior_slice_ledger": {"selected_slice_ids": ["BHV_first"], "confirmed_slice_ids": []}}],
    )

    assert selection["status"] == "planned"
    assert selection["selection_mode"] == "next_unattempted_after_history"
    assert selection["selected_slice_ids"] == ["BHV_second"]
    assert selection["confirmed_slice_ids"] == []


def test_scheduler_stops_after_all_pending_slices_were_attempted_without_confirmation():
    selection = _schedule_behavior_slices(
        [{"slice_id": "BHV_example", "entity": "example", "kind": "transition"}],
        {"slice_budget": 1, "round_number": 1, "round_limit": 3},
        [{"behavior_slice_ledger": {"selected_slice_ids": ["BHV_example"], "confirmed_slice_ids": []}}],
    )

    assert selection["status"] == "stopped"
    assert selection["stop_reason"] == "all_pending_slices_attempted_needs_new_evidence_or_policy"


def test_scheduler_respects_explicit_round_limit():
    selection = _schedule_behavior_slices(
        [{"slice_id": "BHV_example", "entity": "example", "kind": "transition"}],
        {"slice_budget": 1, "round_number": 4, "round_limit": 3},
        [],
    )

    assert selection["status"] == "stopped"
    assert selection["stop_reason"] == "configured_round_limit_reached"


def test_pipeline_persists_ledger_and_advances_without_external_round_or_history(monkeypatch, tmp_path):
    monkeypatch.setenv("QUALIBUG_MAX_BEHAVIOR_SLICES_PER_ROUND", "1")
    monkeypatch.setenv("QUALIBUG_INCREMENTAL_DISCOVERY_ROUND_LIMIT", "3")
    monkeypatch.delenv("QUALIBUG_DISCOVERY_ROUND", raising=False)

    first = run_v12_pipeline(
        project="generic-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
    )
    second = run_v12_pipeline(
        project="generic-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
    )

    ledger_path = tmp_path / "platform_workspace" / "generic-project" / "defect_discovery" / "v12_behavior_slice_ledger.json"
    assert ledger_path.exists()
    assert first["behavior_slice_ledger"]["history_source"] == "persisted_ledger"
    assert second["behavior_slice_ledger"]["history_source"] == "persisted_ledger"
    assert first["behavior_slice_ledger"]["round"] == 1
    assert second["behavior_slice_ledger"]["round"] == 2
    assert second["behavior_slice_ledger"]["selection_mode"] == "next_unattempted_after_history"
    assert first["behavior_slice_ledger"]["selected_slice_ids"] != second["behavior_slice_ledger"]["selected_slice_ids"]
    assert second["behavior_slice_ledger"]["confirmed_slice_ids"] == []
    assert all(item["discovery_round"] == 2 for item in second["plan_only_scenarios"])


def test_pipeline_selects_different_source_slices_across_explicit_rounds(monkeypatch, tmp_path):
    monkeypatch.setenv("QUALIBUG_MAX_BEHAVIOR_SLICES_PER_ROUND", "1")
    monkeypatch.setenv("QUALIBUG_INCREMENTAL_DISCOVERY_ROUND_LIMIT", "2")
    monkeypatch.setenv("QUALIBUG_DISCOVERY_ROUND", "1")

    first = run_v12_pipeline(
        project="generic-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
    )

    assert "error" not in first
    assert first["phases"]["incremental_discovery"]["status"] == "planned"
    assert len(first["behavior_slice_ledger"]["selected_slice_ids"]) == 1
    assert first["phases"]["execution"]["status"] == "skipped"
    assert all(item["behavior_slice_id"] for item in first["plan_only_scenarios"])
    assert all(item["discovery_round"] == 1 for item in first["plan_only_scenarios"])

    monkeypatch.setenv("QUALIBUG_DISCOVERY_ROUND", "2")
    second = run_v12_pipeline(
        project="generic-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
    )

    assert "error" not in second
    assert second["phases"]["incremental_discovery"]["status"] == "planned"
    assert first["behavior_slice_ledger"]["selected_slice_ids"] != second["behavior_slice_ledger"]["selected_slice_ids"]
    assert all(item["discovery_round"] == 2 for item in second["plan_only_scenarios"])
