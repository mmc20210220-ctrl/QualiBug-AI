from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "frontend" / "src" / "App.tsx"
SIDEBAR = ROOT / "frontend" / "src" / "components" / "Sidebar.tsx"
PAGE = ROOT / "frontend" / "src" / "pages" / "Materials.tsx"
CLIENT = ROOT / "frontend" / "src" / "api" / "knowledge-connectors.ts"
HANDLER = ROOT / "ai_test_asset_center" / "private_pilot_connector_handlers.py"
ARCHITECTURE = ROOT / "ai_test_asset_center" / "architecture_roots.json"


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
    assert "在线资料源优先，离线上传作为补充" in page
    assert page.index("主要采集方式") < page.index("补充采集方式")
    assert "listKnowledgeConnectors" in page
    assert "syncKnowledgeConnector" in page
    assert "ingestKnowledge" in page
    assert "统一来源清单" in page
    assert "Source Occurrence" in page


def test_frontend_connector_client_never_exposes_raw_checkpoint_contract():
    client = _text(CLIENT)
    assert "next_cursor_returned_to_client" in client
    assert "checkpoint_storage" in client
    assert "source_content_returned" in client
    assert "connectorRequest" in client
    assert "credentials: 'include'" in client


def test_private_routes_are_project_scoped_and_registered_as_core():
    handler = _text(HANDLER)
    assert 'parts[:3] != ["api", "v1", "projects"]' in handler
    assert '_ROUTE_MARKER = "knowledge-connectors"' in handler
    assert "_require_project_scope(project)" in handler
    assert "_require_role" in handler

    architecture = json.loads(_text(ARCHITECTURE))
    overrides = architecture["module_class_overrides"]
    assert overrides["ai_test_asset_center.connector_connection_profiles"] == "core"
    assert overrides["ai_test_asset_center.private_pilot_connector_handlers"] == "core"
