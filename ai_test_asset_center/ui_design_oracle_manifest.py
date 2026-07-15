from __future__ import annotations

"""Structured UI design oracles for frontend runtime validation."""

from typing import Any


UI_DESIGN_SOURCES: tuple[dict[str, Any], ...] = (
    {
        "source_id": "phase106-project-workspace-design-v1",
        "source_type": "product_spec",
        "title": "项目工作区设计预期",
        "artifact_hint": "Phase106D project workspace spec",
    },
)


UI_SCREEN_ORACLES: tuple[dict[str, Any], ...] = (
    {
        "screen_id": "project_list",
        "route": "/projects",
        "title": "项目列表页",
        "design_source_id": "phase106-project-workspace-design-v1",
        "expected_components": [
            "topbar",
            "project_switcher",
            "project_card_list",
            "create_project_button",
            "workspace_state_gate",
        ],
        "expected_states": ["loading", "success", "empty", "error", "offline"],
        "required_feedback": [
            "loading_indicator",
            "empty_state_message",
            "error_feedback",
        ],
        "layout_rules": [
            "topbar_visible_above_fold",
            "project_switcher_visible",
            "primary_cta_visible",
        ],
        "risk_tags": ["task_completion", "workspace_loading", "project_selection"],
    },
    {
        "screen_id": "project_detail",
        "route": "/projects/:projectId",
        "title": "项目详情页",
        "design_source_id": "phase106-project-workspace-design-v1",
        "expected_components": [
            "topbar",
            "project_summary",
            "project_switcher",
            "project_route_guard",
            "project_scoped_api_paths",
        ],
        "expected_states": ["loading", "success", "error", "offline"],
        "required_feedback": [
            "current_project_visible",
            "navigation_entry_visible",
        ],
        "layout_rules": [
            "current_project_context_visible",
            "detail_header_visible",
        ],
        "risk_tags": ["context_continuity", "ui_navigation", "project_scope_binding"],
    },
)


UI_JOURNEY_ORACLES: tuple[dict[str, Any], ...] = (
    {
        "journey_id": "select_project",
        "title": "切换当前项目",
        "entry_route": "/projects",
        "design_source_id": "phase106-project-workspace-design-v1",
        "required_components": [
            "project_switcher",
            "project_card_list",
        ],
        "expected_feedback": [
            "selected_project_persisted",
            "project_context_updated",
        ],
        "failure_patterns": [
            "missing_project_switcher",
            "project_context_not_updated",
        ],
        "defect_family": "uiux",
        "risk_tags": ["journey_break", "project_scope_binding"],
    },
    {
        "journey_id": "enter_command_center",
        "title": "进入质量驾驶舱",
        "entry_route": "/projects/:projectId",
        "design_source_id": "phase106-project-workspace-design-v1",
        "required_components": [
            "project_summary",
            "project_switcher",
            "command_center_entry",
        ],
        "expected_feedback": [
            "current_project_visible",
            "navigation_success",
        ],
        "failure_patterns": [
            "missing_cta",
            "project_context_lost",
            "navigation_blocked",
        ],
        "defect_family": "uiux",
        "risk_tags": ["journey_break", "project_scope_binding"],
    },
)

UI_ORACLE_MATCH_HINTS: dict[str, dict[str, Any]] = {
    "project_switcher": {
        "roles": ["combobox", "listbox", "button"],
        "testids": ["project-switcher"],
        "keywords": ["当前项目", "切换", "project"],
        "tokens": ["当前项目切换", "ProjectSwitcher", "设为当前项目"],
    },
    "create_project_button": {
        "roles": ["button", "link"],
        "testids": ["create-project", "create-project-draft"],
        "keywords": ["创建项目", "草案"],
        "tokens": ["创建项目草案"],
    },
    "command_center_entry": {
        "roles": ["link", "button"],
        "testids": ["command-center"],
        "keywords": ["质量驾驶舱", "command"],
        "tokens": ["质量驾驶舱", "command-center"],
    },
    "risk_evidence_entry": {
        "roles": ["link", "button"],
        "testids": ["risk-evidence"],
        "keywords": ["风险证据", "risks"],
        "tokens": ["风险证据链", "/risks"],
    },
    "loading_indicator": {
        "roles": ["status", "progressbar"],
        "testids": ["loading"],
        "keywords": ["加载", "loading"],
        "tokens": ["正在加载", "加载", "loading"],
    },
    "error_feedback": {
        "roles": ["alert", "status"],
        "testids": ["error"],
        "keywords": ["失败", "error"],
        "tokens": ["失败", "error", "加载失败"],
    },
    "empty_state_message": {
        "roles": ["status"],
        "testids": ["empty"],
        "keywords": ["暂无", "空态", "empty"],
        "tokens": ["暂无", "空态", "暂无可用项目"],
    },
    "topbar": {
        "tokens": ["顶部状态区", "运行模式", "后端状态", "QualiBug"],
    },
    "project_card_list": {
        "tokens": ["项目列表", "进入项目详情", "继续当前旅程"],
    },
    "workspace_state_gate": {
        "tokens": ["统一加载态", "统一失败态", "统一空态", "统一离线态"],
    },
    "project_summary": {
        "tokens": ["项目详情", "项目 ID", "上线建议"],
    },
    "project_route_guard": {
        "tokens": ["项目级状态缓存准备中", "返回项目列表"],
    },
    "project_scoped_api_paths": {
        "tokens": ["/api/v1/projects/{projectId}", "项目级 API 请求"],
    },
    "current_project_visible": {
        "tokens": ["当前项目", "项目 ID"],
    },
    "navigation_entry_visible": {
        "tokens": ["进入", "返回项目列表"],
    },
    "navigation_success": {
        "tokens": ["http", "/projects"],
    },
    "selected_project_persisted": {
        "tokens": ["qualibug.selectedProjectId", "selectedProjectId"],
    },
    "project_context_updated": {
        "tokens": ["当前项目", "ProjectSwitcher"],
    },
}


def build_ui_design_sources() -> list[dict[str, Any]]:
    return [dict(item) for item in UI_DESIGN_SOURCES]


def build_ui_screen_oracles() -> list[dict[str, Any]]:
    return [dict(item) for item in UI_SCREEN_ORACLES]


def build_ui_journey_oracles() -> list[dict[str, Any]]:
    return [dict(item) for item in UI_JOURNEY_ORACLES]


def build_ui_oracle_match_hints() -> dict[str, dict[str, Any]]:
    return {str(key): dict(value) for key, value in UI_ORACLE_MATCH_HINTS.items()}
