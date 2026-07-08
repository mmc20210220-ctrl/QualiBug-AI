"""主链 2 regression: uploaded enterprise docs must drive the test plan.

Two real breakpoints were fixed:
  A) blind_project_runner._sync_input_only_knowledge_asset used to read ONLY
     platform_inputs/{project}; uploads land in platform_workspace/{project}/input,
     and when no raw files existed it returned asset=None (never falling back to
     the registry-built asset) -> uploaded knowledge was dropped.
  B) scan() -> v12_pipeline never read the structured enterprise knowledge asset;
     _knowledge_asset_planning_text flattens rule_library / permission_matrix /
     risk_domains so they enrich the behavior-contract build.
"""
from __future__ import annotations

import json

import pytest

from ai_test_asset_center.blind_project_runner import _sync_input_only_knowledge_asset
from ai_test_asset_center.v12_pipeline import _knowledge_asset_planning_text


def _write_asset(root, project: str, asset: dict) -> None:
    # Mirror enterprise_knowledge_center._paths: the asset lives under
    # platform_workspace/{project}/defect_discovery/.
    d = root / "platform_workspace" / project / "defect_discovery"
    d.mkdir(parents=True, exist_ok=True)
    (d / "enterprise_business_knowledge_asset.json").write_text(
        json.dumps(asset, ensure_ascii=False), encoding="utf-8"
    )


def test_sync_loads_asset_from_registry_when_no_raw_files(tmp_path):
    """Fix A: uploaded docs (asset built from registry) must reach the plan even
    when neither platform_inputs nor platform_workspace/.../input has raw files."""
    project = "demo_kb"
    _write_asset(tmp_path, project, {
        "project_id": project,
        "rule_library": [{"rule_id": "r1", "statement": "订单金额必须等于单价×数量", "risk_type": "money_consistency"}],
        "permission_matrix": [{"permission_id": "p1", "statement": "普通用户不能访问/admin接口"}],
        "risk_domains": [{"risk_id": "h1", "statement": "历史bug: 退款后库存未回滚"}],
        "summary": {"rule_count": 1, "knowledge_ready": True},
    })
    result = _sync_input_only_knowledge_asset(project, tmp_path)
    assert result["enabled"] is True
    assert isinstance(result["asset"], dict)
    assert result["asset"]["rule_library"][0]["statement"] == "订单金额必须等于单价×数量"


def test_sync_loads_when_workspace_input_present(tmp_path):
    """Fix A: a doc physically under platform_workspace/{project}/input (the real
    upload directory) is now picked up by the input-only scan, so an asset is
    produced/loaded (enabled) instead of being silently ignored."""
    project = "demo_kb2"
    ws = tmp_path / "platform_workspace" / project / "input"
    ws.mkdir(parents=True)
    (ws / "PRD.md").write_text("# PRD\n订单金额必须等于单价×数量。\n", encoding="utf-8")
    result = _sync_input_only_knowledge_asset(project, tmp_path)
    assert result["enabled"] is True
    assert isinstance(result["asset"], dict)


def test_sync_disabled_when_no_asset_and_no_files(tmp_path):
    """No docs anywhere -> still disabled (no false positive)."""
    project = "empty_proj"
    result = _sync_input_only_knowledge_asset(project, tmp_path)
    assert result["enabled"] is False
    assert result["asset"] is None


def test_knowledge_asset_planning_text_formats_rules_perms_risks():
    """Fix B: the asset's structured knowledge flattens into planning text the
    behavior-graph builder can parse — fully generic, no hardcoding."""
    asset = {
        "rule_library": [{"rule_id": "r1", "statement": "金额必须一致"}],
        "permission_matrix": [{"permission_id": "p1", "statement": "用户不能访问/admin"}],
        "risk_domains": [{"risk_id": "h1", "statement": "退款后库存未回滚"}],
    }
    text = _knowledge_asset_planning_text(asset)
    assert "金额必须一致" in text
    assert "用户不能访问/admin" in text
    assert "退款后库存未回滚" in text
    assert "业务规则" in text and "权限矩阵" in text and "历史风险" in text


def test_knowledge_asset_planning_text_empty_is_safe():
    assert _knowledge_asset_planning_text({}) == ""
    assert _knowledge_asset_planning_text({"rule_library": []}) == ""
