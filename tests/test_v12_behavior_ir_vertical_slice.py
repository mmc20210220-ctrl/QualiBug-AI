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
    (root / "projects" / project / "input" / "API_SPEC.md").write_text(api, encoding="utf-8")

    # Avoid network / long execution: block approved base URL via empty runtime.
    result = run_v12_pipeline(
        project=project,
        root=root,
        prd_text="Items have owner and viewer roles. Quantity must be conserved.",
        api_spec_text=api,
        db_schema_text="",
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
        },
    )
    assert isinstance(result, dict)
    phase = (result.get("phases") or {}).get("behavior_ir")
    assert phase["status"] == "completed"
    assert result["behavior_ir"]["schema_version"] == "qualibug.behavior-ir.v2"
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
        "accepted_relationship_count": 0,
    }
    blob = str(result.get("behavior_ir"))
    assert "bugs.json" not in blob.lower()
    assert "ground_truth" not in blob.lower()
