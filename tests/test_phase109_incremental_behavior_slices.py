from __future__ import annotations

import hashlib
import json

from ai_test_asset_center.business_state_graph import BusinessStateGraphBuilder
from ai_test_asset_center.policy_wiring import _behavior_slice_execution_value
from ai_test_asset_center.route_catalog_builder import RouteCatalogBuilder
from ai_test_asset_center.v12_pipeline import (
    _login_parameter_fuzzer,
    _behavior_slice_settings,
    _runtime_contract,
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

SOURCE_MANIFEST = {
    "source_id": "uploaded:case-api-v1",
    "source_hash": hashlib.sha256(API_SPEC.encode("utf-8")).hexdigest(),
    "source_origin": "declared_manifest",
}


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


def test_chinese_state_requirement_binds_entity_and_get_observation_route():
    api_doc = """### GET /api/orders/:id
### GET /api/orders
### POST /api/payments/pay
"""
    db_schema = """CREATE TABLE orders (
  id TEXT PRIMARY KEY,
  status TEXT CHECK (status IN ('CREATED', 'PENDING_PAYMENT', 'PAID', 'CANCELLED'))
);
"""
    prd = """### 3.2 支付
1. 订单必须处于 `PENDING_PAYMENT` 状态；
2. 支付成功后订单状态变为 `PAID`；
"""
    builder = BusinessStateGraphBuilder()
    builder.build(prd, api_doc, db_schema)
    contract = builder.behavior_contract()

    invariant_slices = [item for item in contract["slices"] if item["kind"] == "invariant" and item["entity"] == "order"]
    assert invariant_slices
    assert any("/api/orders/:id" in item["endpoints"] or "/api/orders" in item["endpoints"] for item in invariant_slices)
    assert any("支付成功后订单状态变为 `PAID`" in ref["quote"] for item in invariant_slices for ref in item["source_refs"])


def test_behavior_slice_policy_guardrails_cap_budget_and_round_bounds(monkeypatch):
    monkeypatch.setenv("QUALIBUG_MAX_BEHAVIOR_SLICES_PER_ROUND", "999")
    monkeypatch.setenv("QUALIBUG_DISCOVERY_ROUND", "0")
    monkeypatch.setenv("QUALIBUG_INCREMENTAL_DISCOVERY_ROUND_LIMIT", "999")
    assert _behavior_slice_execution_value("max_behavior_slices_per_round", 999, 15) == 15
    assert _behavior_slice_execution_value("incremental_discovery_round", 0, 1) == 1
    assert _behavior_slice_execution_value("incremental_discovery_round_limit", 999, 3) == 12


def test_parameter_fuzzer_executes_baseline_get_without_query_params(monkeypatch):
    from ai_test_asset_center.parameter_fuzzer import ParameterFuzzer

    fuzzer = ParameterFuzzer("http://example.test")
    calls: list[tuple[str, str, str]] = []

    def fake_call(method: str, path: str, body=None, token: str = ""):
        calls.append((method, path, token))
        return 500, {"error": "boom"}, 1.5

    monkeypatch.setattr(fuzzer, "_call", fake_call)

    findings = fuzzer.fuzz_all([{"method": "GET", "path": "/api/orders"}], max_variants=1)

    assert calls == [("GET", "/api/orders", "")]
    assert len(findings) == 1
    assert findings[0]["method"] == "GET"
    assert findings[0]["path"] == "/api/orders"


def test_markdown_route_catalog_extracts_colon_style_path_param():
    routes = RouteCatalogBuilder().build("### GET /api/orders/:id")
    assert len(routes) == 1
    assert routes[0].path == "/api/orders/:id"
    assert routes[0].path_params == ["id"]


def test_parameter_fuzzer_skips_unresolved_colon_path_param_route(monkeypatch):
    from ai_test_asset_center.parameter_fuzzer import ParameterFuzzer

    fuzzer = ParameterFuzzer("http://example.test")
    calls: list[tuple[str, str, str]] = []

    def fake_call(method: str, path: str, body=None, token: str = ""):
        calls.append((method, path, token))
        if path.startswith("/api/orders") or path == "/api":
            return 200, [], 1.0
        raise AssertionError(f"unexpected call to unresolved route: {path}")

    monkeypatch.setattr(fuzzer, "_call", fake_call)

    findings = fuzzer.fuzz_all([{"method": "GET", "path": "/api/orders/:id", "path_params": ["id"]}], max_variants=1)

    assert calls
    assert calls[0] == ("GET", "/api/orders", "")
    assert all(path != "/api/orders/:id" for _, path, _ in calls)
    assert all(not path.startswith("/api/orders/") or path == "/api/orders" for _, path, _ in calls)
    assert findings == []


def test_parameter_fuzzer_resolves_real_id_from_paginated_collection(monkeypatch):
    from ai_test_asset_center.parameter_fuzzer import ParameterFuzzer

    fuzzer = ParameterFuzzer("http://example.test")
    calls: list[tuple[str, str, str]] = []

    def fake_call(method: str, path: str, body=None, token: str = ""):
        calls.append((method, path, token))
        if path == "/api/orders":
            return 404, {"error": "not_found"}, 1.0
        if path == "/api/orders?page=1&size=1":
            return 200, {"items": [{"id": "ord_123"}]}, 1.0
        if path == "/api/orders/ord_123":
            return 200, {"id": "ord_123", "status": "PAID"}, 1.0
        raise AssertionError(f"unexpected call: {path}")

    monkeypatch.setattr(fuzzer, "_call", fake_call)

    findings = fuzzer.fuzz_all([{"method": "GET", "path": "/api/orders/:id", "path_params": ["id"]}], max_variants=1)

    assert calls == [
        ("GET", "/api/orders", ""),
        ("GET", "/api/orders?page=1&size=1", ""),
        ("GET", "/api/orders/ord_123", ""),
    ]
    assert findings == []


def test_login_parameter_fuzzer_uses_registry_credentials(tmp_path):
    registry_path = tmp_path / "platform_workspace" / "demo" / "enterprise_pilot_runtime" / "connector_registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "project_id": "demo",
                "connectors": [],
                "test_profile": {
                    "test_credentials": {
                        "buyer": {
                            "email": "buyer01@example.com",
                            "password": "Test@123456",
                        }
                    }
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    class StubFuzzer:
        def __init__(self) -> None:
            self.calls: list[dict[str, str]] = []

        def login(self, email: str = "", password: str = "", login_path: str = "", body_template=None) -> bool:
            self.calls.append({"email": email, "password": password, "login_path": login_path})
            return True

    stub = StubFuzzer()

    assert _login_parameter_fuzzer(
        stub,
        [{"method": "POST", "path": "/api/auth/login", "operation_id": "login"}],
        "demo",
        tmp_path,
    )
    assert stub.calls == [
        {
            "email": "buyer01@example.com",
            "password": "Test@123456",
            "login_path": "/api/auth/login",
        }
    ]


def test_slice_budget_is_hard_capped_at_fifteen(monkeypatch):
    monkeypatch.setenv("QUALIBUG_MAX_BEHAVIOR_SLICES_PER_ROUND", "999")
    assert _behavior_slice_settings()["slice_budget"] == 15


def test_plan_only_slice_is_not_misclassified_as_confirmed():
    selection = _schedule_behavior_slices(
        [{"slice_id": "BHV_example", "entity": "example", "kind": "transition"}],
        {"slice_budget": 1, "round_number": 1, "round_limit": 2},
        [{"behavior_slice_id": "BHV_example", "execution_status": "not_executed", "confirmation_status": "candidate", "gate_passed": False}],
    )
    assert selection["status"] == "planned"
    assert selection["confirmed_slice_ids"] == []
    assert selection["selected_slice_ids"] == ["BHV_example"]


def test_history_advances_to_next_unattempted_slice_after_real_attempt():
    selection = _schedule_behavior_slices(
        [{"slice_id": "BHV_first", "entity": "example", "kind": "transition"}, {"slice_id": "BHV_second", "entity": "example", "kind": "invariant"}],
        {"slice_budget": 1, "round_number": 1, "round_limit": 3},
        [{"behavior_slice_ledger": {"attempted_slice_ids": ["BHV_first"], "confirmed_slice_ids": []}}],
    )
    assert selection["status"] == "planned"
    assert selection["selection_mode"] == "next_unattempted_after_history"
    assert selection["selected_slice_ids"] == ["BHV_second"]
    assert selection["confirmed_slice_ids"] == []


def test_scheduler_stops_after_all_pending_slices_were_attempted_without_confirmation():
    selection = _schedule_behavior_slices(
        [{"slice_id": "BHV_example", "entity": "example", "kind": "transition"}],
        {"slice_budget": 1, "round_number": 1, "round_limit": 3},
        [{"behavior_slice_ledger": {"attempted_slice_ids": ["BHV_example"], "confirmed_slice_ids": []}}],
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


def test_pipeline_does_not_advance_round_without_runtime_attempts(monkeypatch, tmp_path):
    monkeypatch.setenv("QUALIBUG_MAX_BEHAVIOR_SLICES_PER_ROUND", "1")
    monkeypatch.setenv("QUALIBUG_INCREMENTAL_DISCOVERY_ROUND_LIMIT", "3")
    monkeypatch.delenv("QUALIBUG_DISCOVERY_ROUND", raising=False)
    first = run_v12_pipeline(project="generic-project", root=tmp_path, prd_text=PRD, api_spec_text=API_SPEC, db_schema_text=DB_SCHEMA)
    second = run_v12_pipeline(project="generic-project", root=tmp_path, prd_text=PRD, api_spec_text=API_SPEC, db_schema_text=DB_SCHEMA)
    ledger_path = tmp_path / "platform_workspace" / "generic-project" / "defect_discovery" / "v12_behavior_slice_ledger.json"
    campaign_dir = tmp_path / "platform_workspace" / "generic-project" / "defect_discovery" / "campaigns"
    assert ledger_path.exists()
    assert campaign_dir.exists()
    assert first["campaign"]["campaign_mode"] == "created"
    assert second["campaign"]["campaign_mode"] == "resumed"
    assert first["campaign"]["campaign_id"] == second["campaign"]["campaign_id"]
    assert first["behavior_slice_ledger"]["round"] == 1
    assert second["behavior_slice_ledger"]["round"] == 1
    assert second["behavior_slice_ledger"]["selection_mode"] == "round_paging"
    assert first["behavior_slice_ledger"]["selected_slice_ids"] == second["behavior_slice_ledger"]["selected_slice_ids"]
    assert second["behavior_slice_ledger"]["attempted_slice_ids"] == []
    assert second["behavior_slice_ledger"]["confirmed_slice_ids"] == []
    assert all(item["discovery_round"] == 1 for item in second["plan_only_scenarios"])


def test_pipeline_ignores_persisted_slice_history_from_different_snapshot(tmp_path):
    builder = BusinessStateGraphBuilder()
    builder.build(PRD, API_SPEC, DB_SCHEMA)
    contract = builder.behavior_contract()
    slice_ids = [item["slice_id"] for item in contract["slices"] if item.get("slice_id")]
    assert slice_ids
    ledger_path = tmp_path / "platform_workspace" / "generic-project" / "defect_discovery" / "v12_behavior_slice_ledger.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        json.dumps(
            {
                "campaign_id": "CMP_old_snapshot",
                "campaign_status": "coverage_deferred",
                "scope_id": "scope-a",
                "source_snapshot_hash": "different-snapshot",
                "project": "generic-project",
                "round": 1,
                "round_limit": 3,
                "slice_budget": 15,
                "selection_mode": "history_exhausted",
                "selected_slice_ids": [],
                "attempted_slice_ids": slice_ids,
                "confirmed_slice_ids": [],
                "next_round": None,
                "stop_reason": "all_pending_slices_attempted_needs_new_evidence_or_policy",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    result = run_v12_pipeline(
        project="generic-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": SOURCE_MANIFEST,
        },
    )

    assert result["phases"]["incremental_discovery"]["status"] == "planned"
    assert result["behavior_slice_ledger"]["selected_slice_ids"]
    assert result["behavior_slice_ledger"]["stop_reason"] != "all_pending_slices_attempted_needs_new_evidence_or_policy"


def test_pipeline_recovers_stale_deferred_campaign_without_attempt_history(tmp_path):
    initial = run_v12_pipeline(
        project="generic-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": SOURCE_MANIFEST,
        },
    )
    campaign_path = (
        tmp_path
        / "platform_workspace"
        / "generic-project"
        / "defect_discovery"
        / "campaigns"
        / f"{initial['campaign']['campaign_id']}.json"
    )
    stored = json.loads(campaign_path.read_text(encoding="utf-8"))
    stored["campaign_status"] = "coverage_deferred"
    stored["status"] = "coverage_deferred"
    stored["round_count"] = 1
    stored["attempted_slice_ids"] = []
    stored["confirmation_receipts"] = {}
    stored["coverage_deferred_reason"] = "all_pending_slices_attempted_needs_new_evidence_or_policy"
    stored["next_campaign_reason"] = "source_binding_or_runtime_evidence_required"
    campaign_path.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")

    recovered = run_v12_pipeline(
        project="generic-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": SOURCE_MANIFEST,
        },
    )

    assert recovered["campaign"]["campaign_status"] == "active"
    assert recovered["behavior_slice_ledger"]["round"] == 1
    assert recovered["phases"]["incremental_discovery"]["status"] == "planned"
    assert recovered["behavior_slice_ledger"]["selected_slice_ids"]


def test_pipeline_recovers_stale_deferred_campaign_when_new_slices_exist_for_same_snapshot(tmp_path):
    initial = run_v12_pipeline(
        project="generic-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": SOURCE_MANIFEST,
        },
    )
    campaign_path = (
        tmp_path
        / "platform_workspace"
        / "generic-project"
        / "defect_discovery"
        / "campaigns"
        / f"{initial['campaign']['campaign_id']}.json"
    )
    stored = json.loads(campaign_path.read_text(encoding="utf-8"))
    stored["campaign_status"] = "coverage_deferred"
    stored["status"] = "coverage_deferred"
    stored["round_count"] = 1
    stored["attempted_slice_ids"] = ["BHV_legacy_only"]
    stored["coverage_deferred_reason"] = "all_pending_slices_attempted_needs_new_evidence_or_policy"
    stored["next_campaign_reason"] = "source_binding_or_runtime_evidence_required"
    campaign_path.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")

    recovered = run_v12_pipeline(
        project="generic-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": SOURCE_MANIFEST,
        },
    )

    assert recovered["campaign"]["campaign_status"] == "active"
    assert recovered["phases"]["incremental_discovery"]["status"] == "planned"
    assert recovered["behavior_slice_ledger"]["selected_slice_ids"]
    assert recovered["behavior_slice_ledger"]["selection_mode"] == "next_unattempted_after_history"


def test_pipeline_recovers_round_exhausted_campaign_when_unattempted_slices_now_exist(tmp_path):
    initial = run_v12_pipeline(
        project="generic-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": SOURCE_MANIFEST,
        },
    )
    campaign_path = (
        tmp_path
        / "platform_workspace"
        / "generic-project"
        / "defect_discovery"
        / "campaigns"
        / f"{initial['campaign']['campaign_id']}.json"
    )
    stored = json.loads(campaign_path.read_text(encoding="utf-8"))
    stored["campaign_status"] = "coverage_deferred"
    stored["status"] = "coverage_deferred"
    stored["round_count"] = 3
    stored["automatic_round_limit"] = 3
    stored["attempted_slice_ids"] = ["BHV_legacy_only"]
    stored["coverage_deferred_reason"] = "slice_budget_reached"
    stored["next_campaign_reason"] = "source_binding_or_runtime_evidence_required"
    campaign_path.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")

    recovered = run_v12_pipeline(
        project="generic-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": SOURCE_MANIFEST,
        },
    )

    assert recovered["campaign"]["campaign_status"] == "active"
    assert recovered["campaign"]["round_count"] == 0
    assert recovered["phases"]["incremental_discovery"]["status"] == "planned"
    assert recovered["behavior_slice_ledger"]["selected_slice_ids"]


def test_direct_v12_target_execution_is_blocked_without_enterprise_contract(tmp_path):
    result = run_v12_pipeline(
        project="generic-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        base_url="https://example.invalid",
    )
    assert result["runtime_contract"]["status"] == "blocked"
    assert result["phases"]["execution"]["status"] == "blocked"
    assert result["auto_har"]["status"] == "no_traffic"
    assert result["campaign"]["confirmed_slice_count"] == 0


def test_direct_runtime_contract_accepts_verified_manifest_without_network_access():
    contract = _runtime_contract(
        {"scope_id": "case-lifecycle", "environment_ref": "approved-test", "source_manifest": SOURCE_MANIFEST},
        "https://example.invalid",
        API_SPEC,
    )
    assert contract["status"] == "approved"
    assert contract["approved_base_url"] == "https://example.invalid"
    assert contract["source_manifest"]["source_id"] == "uploaded:case-api-v1"


def test_direct_v12_rejects_hash_mismatch_before_any_execution(tmp_path):
    result = run_v12_pipeline(
        project="enterprise-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        base_url="https://example.invalid",
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": {"source_id": "uploaded:case-api-v1", "source_hash": "0" * 64},
        },
    )
    assert result["runtime_contract"]["status"] == "blocked"
    assert "SOURCE_HASH_MISMATCH" in result["runtime_contract"]["missing_requirements"]
    assert result["phases"]["execution"]["status"] == "blocked"
    assert result["auto_har"]["status"] == "no_traffic"


def test_campaign_persists_verified_source_identity_for_plan_only_runs(tmp_path):
    result = run_v12_pipeline(
        project="enterprise-project",
        root=tmp_path,
        prd_text=PRD,
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        campaign_context={
            "scope_id": "case-lifecycle",
            "environment_ref": "approved-test",
            "source_manifest": SOURCE_MANIFEST,
        },
    )
    assert "error" not in result
    assert result["runtime_contract"]["status"] == "plan_only"
    assert result["campaign"]["source_id"] == "uploaded:case-api-v1"
    assert result["campaign"]["source_hash"] == SOURCE_MANIFEST["source_hash"]
    assert result["campaign"]["source_snapshot_hash"] != SOURCE_MANIFEST["source_hash"]


def test_pipeline_selects_different_source_slices_across_explicit_rounds(monkeypatch, tmp_path):
    monkeypatch.setenv("QUALIBUG_MAX_BEHAVIOR_SLICES_PER_ROUND", "1")
    monkeypatch.setenv("QUALIBUG_INCREMENTAL_DISCOVERY_ROUND_LIMIT", "2")
    monkeypatch.setenv("QUALIBUG_DISCOVERY_ROUND", "1")
    first = run_v12_pipeline(project="generic-project", root=tmp_path, prd_text=PRD, api_spec_text=API_SPEC, db_schema_text=DB_SCHEMA)
    assert "error" not in first
    assert first["phases"]["incremental_discovery"]["status"] == "planned"
    assert len(first["behavior_slice_ledger"]["selected_slice_ids"]) == 1
    assert first["phases"]["execution"]["status"] == "skipped"
    assert all(item["behavior_slice_id"] for item in first["plan_only_scenarios"])
    assert all(item["discovery_round"] == 1 for item in first["plan_only_scenarios"])
    monkeypatch.setenv("QUALIBUG_DISCOVERY_ROUND", "2")
    second = run_v12_pipeline(project="generic-project", root=tmp_path, prd_text=PRD, api_spec_text=API_SPEC, db_schema_text=DB_SCHEMA)
    assert "error" not in second
    assert second["phases"]["incremental_discovery"]["status"] == "planned"
    assert first["behavior_slice_ledger"]["selected_slice_ids"] != second["behavior_slice_ledger"]["selected_slice_ids"]
    assert all(item["discovery_round"] == 2 for item in second["plan_only_scenarios"])
