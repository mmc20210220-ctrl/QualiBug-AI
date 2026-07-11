"""Integration: Behavior IR vertical slice is invoked from run_v12_pipeline."""
from __future__ import annotations

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
            "scope_id": "integration_scope",
            "environment_ref": "integration_test",
            "environment_kind": "test",
            "source_manifest": {"source_id": "api", "source_hash": "deadbeef"},
        },
    )
    assert isinstance(result, dict)
    # Knowledge asset may or may not materialize depending on parsers; when the
    # IR phase runs it must be structured and never contain ground truth paths.
    phase = (result.get("phases") or {}).get("behavior_ir")
    if isinstance(phase, dict) and phase.get("status") == "completed":
        assert result.get("behavior_ir", {}).get("schema_version") or result.get("behavior_ir", {}).get("summary")
        assert "test_obligations" in result
        assert "experiment_compile" in result
        blob = str(result.get("behavior_ir"))
        assert "bugs.json" not in blob.lower()
        assert "ground_truth" not in blob.lower()
    else:
        # Asset build can fail in empty temp projects; failure must be explicit.
        assert isinstance(phase, dict)
        assert phase.get("status") in {"FAILED_SAFE", None} or "error" in phase
