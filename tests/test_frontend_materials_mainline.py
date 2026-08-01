from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "frontend" / "src" / "App.tsx"
SIDEBAR = ROOT / "frontend" / "src" / "components" / "Sidebar.tsx"
PAGE = ROOT / "frontend" / "src" / "pages" / "Materials.tsx"
CLIENT = ROOT / "frontend" / "src" / "api" / "knowledge-connectors.ts"
HANDLER = ROOT / "ai_test_asset_center" / "private_pilot_connector_handlers.py"
AUTO_SYNC = ROOT / "ai_test_asset_center" / "connector_auto_sync.py"
ARCHITECTURE = ROOT / "ai_test_asset_center" / "architecture_roots.json"
SIMPLICITY = ROOT / "docs" / "PRODUCT_SIMPLICITY_PRINCIPLE.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_enterprise_materials_is_a_real_authenticated_route_not_a_redirect():
    app = _text(APP)
    assert "import { Materials } from './pages/Materials';" in app
    assert '<Route path="/materials" element={<Materials />} />' in app
    assert 'path="/materials" element={<Navigate' not in app


def test_sidebar_exposes_online_first_enterprise_materials_mainline():
    sidebar = _text(SIDEBAR)
    assert "{ to: 'materials', icon: 'materials', label: '企业资料' }" in sidebar
    assert sidebar.index("to: 'materials'") < sidebar.index("to: 'settings'")


def test_materials_page_keeps_online_primary_and_upload_supplemental():
    page = _text(PAGE)
    assert "连接一次，系统自动读取、识别、去重、更新和恢复" in page
    assert page.index("自动维护") < page.index("补充方式")
    assert "listKnowledgeConnectors" in page
    assert "listConnectorTypes" in page
    assert "connectKnowledgeConnector" in page
    assert "listConnectorResources" in page
    assert "last_failed_sync_at_utc" in page
    assert "最近失败" in page
    assert "refreshKnowledgeConnector" in page
    assert "ingestKnowledge" in page
    assert "统一企业知识库" in page


def test_normal_user_path_is_two_steps_and_daily_maintenance_disappears():
    page = _text(PAGE)
    assert "两步完成" in page
    assert "选择连接器并填写授权" in page
    assert "选择资料范围" in page
    assert "保存并开始读取" in page
    assert "后续更新和重试由系统处理" in page
    assert "自动更新" in page
    assert "系统正在自动恢复" in page
    assert "立即更新" not in page
    assert "测试连接" not in page
    assert "资料源标识" not in page
    assert "删除策略" not in page
    assert "降级策略" not in page
    assert "同步游标" not in page
    for term in (
        "Source Occurrence",
        "Sync Epoch",
        "Checkpoint",
        "Cursor",
        "Snapshot Fingerprint",
        "Retire Missing",
    ):
        assert term not in page


def test_manual_check_and_advanced_choices_are_hidden_behind_disclosure():
    page = _text(PAGE)
    assert page.count('className="materials-advanced"') >= 3
    assert "遇到问题时" in page
    assert "现在检查一次" in page
    assert "认证方式" in page
    assert "高级资料范围" in page
    assert "selectedManifest.auth_modes.map" in page
    assert "selectedManifest.credential_fields" in page or "manifestFields" in page


def test_frontend_connector_client_owns_safe_orchestration_and_friendly_errors():
    client = _text(CLIENT)
    assert "export async function connectFeishuKnowledge" in client
    orchestration = client[client.index("export async function connectFeishuKnowledge"):]
    assert orchestration.index("configureFeishuConnector") < orchestration.index("testKnowledgeConnector")
    assert orchestration.index("testKnowledgeConnector") < orchestration.index("syncKnowledgeConnector")
    assert "上次更新状态不完整，系统已保留原有资料并会自动重试" in client
    assert "在线资料授权范围不足" in client
    assert "原有资料不受影响，系统会自动重试" in client
    assert "KnowledgeConnectorAutoSync" in client
    assert "maintenance_required_by_user" in client


def test_frontend_connector_client_never_exposes_raw_checkpoint_contract():
    client = _text(CLIENT)
    assert "next_cursor_returned_to_client" in client
    assert "checkpoint_storage" in client
    assert "source_content_returned" in client
    assert "connectorRequest" in client
    assert "credentials: 'include'" in client


def test_http_and_background_use_one_managed_sync_authority():
    handler = _text(HANDLER)
    auto_sync = _text(AUTO_SYNC)
    assert 'parts[:3] != ["api", "v1", "projects"]' in handler
    assert '_ROUTE_MARKER = "knowledge-connectors"' in handler
    assert "_require_project_scope(project)" in handler
    assert "_require_role" in handler
    assert "run_managed_feishu_sync" in handler
    assert "sync_feishu_connector(" not in handler
    assert "commit_connector_sync_checkpoint(" not in handler
    assert "run_managed_feishu_sync" in auto_sync
    assert "run_connector_auto_sync_sweep" in auto_sync
    assert "new_registry_created" in auto_sync

    architecture = json.loads(_text(ARCHITECTURE))
    overrides = architecture["module_class_overrides"]
    assert overrides["ai_test_asset_center.connector_connection_profiles"] == "core"
    assert overrides["ai_test_asset_center.connector_auto_sync"] == "core"
    assert overrides["ai_test_asset_center.private_pilot_connector_handlers"] == "core"


def test_product_simplicity_is_a_repository_level_design_gate():
    principle = _text(SIMPLICITY)
    assert "操作极简" in principle
    assert "理解成本最低" in principle
    assert "傻瓜式操作" in principle
    assert "接近零维护" in principle
    assert "系统能判断的，不让用户配置" in principle
    assert "工程术语不得进入普通用户路径" in principle
