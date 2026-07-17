"""Integration: Behavior IR vertical slice is invoked from run_v12_pipeline."""
from __future__ import annotations

import hashlib
from pathlib import Path

from ai_test_asset_center.v12_pipeline import run_v12_pipeline


def test_run_v12_pipeline_emits_behavior_ir_phase(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path
    project = "generic-project"
    # Minimal project layout so knowledge/asset builders do not crash.
    (root / "projects" / project / "input").mkdir(parents=True)
    (root / "platform_inputs" / project).mkdir(parents=True)
    (root / "platform_workspace" / project).mkdir(parents=True)
    (root / "platform_outputs" / project).mkdir(parents=True)
    api = """
# API
GET /items
PUT /items/{id}
"""
    schema = """
CREATE TABLE items (
  id TEXT PRIMARY KEY,
  quantity INTEGER NOT NULL
);
"""
    (root / "projects" / project / "input" / "API_SPEC.md").write_text(api, encoding="utf-8")
    observed_ui_calls: list[dict[str, object]] = []

    def fake_ui_execution(
        project_id: str,
        requests: object,
        runtime_contract: dict[str, object],
        **kwargs: object,
    ) -> dict[str, object]:
        observed_ui_calls.append({
            "project_id": project_id,
            "requests": requests,
            "runtime_contract": runtime_contract,
            **kwargs,
        })
        return {
            "status": "completed",
            "requested": 1,
            "executed": 1,
            "failed": 0,
            "blocked": 0,
            "provider_distribution": {"playwright_browser_plan": 1},
            "results": [{
                "request_id": "UI-1",
                "provider": "playwright_browser_plan",
                "status": "executed",
                "artifacts": [{"artifact_type": "locator", "ref": "locator.png"}],
            }],
            "findings": [],
            "artifacts": [{"artifact_type": "locator", "ref": "locator.png"}],
            "duration_ms": 12,
        }

    monkeypatch.setattr(
        "ai_test_asset_center.ui_execution_adapter.execute_ui_execution_requests",
        fake_ui_execution,
    )

    # Avoid network / long execution: block approved base URL via empty runtime.
    result = run_v12_pipeline(
        project=project,
        root=root,
        prd_text="Items have owner and viewer roles. Quantity must be conserved.",
        api_spec_text=api,
        db_schema_text=schema,
        base_url="",
        campaign_context={
            "mainline_authority": "experiment_candidate",
            "run_id": "RUN-VERTICAL-SLICE",
            "target_id": "TARGET-VERTICAL-SLICE",
            "environment_id": "ENV-VERTICAL-SLICE",
            "policy_version": "policy-vertical-slice",
            "evaluation_mode": "operational",
            "scope_id": "integration_scope",
            "environment_ref": "integration_test",
            "environment_kind": "test",
            "source_manifest": {
                "source_id": "api",
                "source_hash": hashlib.sha256(api.encode("utf-8")).hexdigest(),
            },
            "ui_execution_requests": [{
                "request_id": "UI-1",
                "provider": "playwright_browser_plan",
                "browser_plan": {"steps": [{"action": "goto", "url": "/"}]},
            }],
        },
    )
    assert isinstance(result, dict)
    phase = (result.get("phases") or {}).get("behavior_ir")
    assert phase["status"] == "completed"
    assert result["behavior_ir"]["schema_version"] == "qualibug.behavior-ir.v2"
    assert any(
        entity.get("name") == "items"
        for entity in result["behavior_ir"]["entities"]
    )
    assert any(
        invariant.get("source_rule_refs")
        for invariant in result["behavior_ir"]["invariants"]
    )
    assert any(
        gap.get("reason_code") == "SOURCE_INVARIANT_OPERATION_UNBOUND"
        for gap in result["behavior_ir"]["coverage_gaps"]
    )
    overlay_receipt = result["runtime_source_overlay_receipt"]
    assert overlay_receipt["source_count"] == 3
    assert {
        row["source_type"] for row in overlay_receipt["source_fingerprints"]
    } == {"prd", "markdown_api", "database_schema"}
    assert all(
        len(row["content_hash"]) == 64
        for row in overlay_receipt["source_fingerprints"]
    )
    input_receipt = result["behavior_ir_input_receipt"]
    assert input_receipt["schema_version"] == "qualibug.behavior-ir-input-receipt.v1"
    assert input_receipt["runtime_source_overlay"]["source_count"] == 3
    assert input_receipt["api_operation_count"] == 2
    assert input_receipt["runtime_interface_discovery_enabled"] is False
    assert "test_obligations" in result
    assert "experiment_compile" in result
    assert result["agent_intent_plan"]["schema_version"] == (
        "qualibug.agent-intent-plan.v1"
    )
    assert result["agent_intent_plan"]["semantic_authority"] == "behavior_ir"
    assert result["phases"]["agent_intent"]["status"] == "verified"
    assert result["agent_semantic_link_receipt"]["status"] == "NOT_REQUESTED"
    assert result["phases"]["agent_semantic_linking"] == {
        "status": "not_requested",
        "proposal_count": 0,
        "accepted_relationship_count": 0,
        "rejected_proposal_count": 0,
    }
    assert len(observed_ui_calls) == 1
    assert observed_ui_calls[0]["project_id"] == project
    assert result["ui_execution"]["status"] == "completed"
    assert result["phases"]["ui_execution"] == {
        "status": "completed",
        "requested": 1,
        "executed": 1,
        "failed": 0,
        "blocked": 0,
        "provider_distribution": {"playwright_browser_plan": 1},
        "findings": 0,
        "duration_ms": 12,
    }
    blob = str(result.get("behavior_ir"))
    assert "bugs.json" not in blob.lower()
    assert "ground_truth" not in blob.lower()
