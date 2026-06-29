from __future__ import annotations

"""Structured frontend task journeys for browser-driven defect discovery."""

from typing import Any


FRONTEND_TASK_JOURNEYS: tuple[dict[str, Any], ...] = (
    {
        "journey_id": "select_project",
        "title": "切换当前项目",
        "entry_route": "/projects",
        "design_source_id": "phase106-project-workspace-design-v1",
        "required_project_context": False,
        "steps": [
            "load_project_list",
            "assert_project_cards_visible",
            "select_current_project",
        ],
        "required_components": ["project_switcher", "project_card_list"],
        "expected_feedback": ["selected_project_persisted", "project_context_updated"],
        "success_signals": [
            "project_switcher_visible",
            "selected_project_persisted",
            "project_context_updated",
        ],
        "failure_signals": [
            "blank_page",
            "missing_project_switcher",
            "project_context_not_updated",
        ],
        "failure_patterns": [
            "missing_project_switcher",
            "project_context_not_updated",
        ],
        "defect_family": "uiux",
        "risk_tags": ["journey_break", "project_scope_binding"],
    },
    {
        "journey_id": "open_project_detail",
        "title": "进入项目详情",
        "entry_route": "/projects/:projectId",
        "design_source_id": "phase106-project-workspace-design-v1",
        "required_project_context": True,
        "steps": [
            "load_project_detail",
            "assert_current_project_visible",
            "assert_route_guard_ready",
        ],
        "required_components": ["project_summary", "project_switcher", "project_route_guard"],
        "expected_feedback": ["project_id_visible", "project_summary_visible", "route_navigation_success"],
        "success_signals": [
            "project_id_visible",
            "project_summary_visible",
            "route_navigation_success",
        ],
        "failure_signals": [
            "blank_page",
            "project_not_found",
            "navigation_blocked",
        ],
        "failure_patterns": [
            "project_not_found",
            "navigation_blocked",
        ],
        "defect_family": "ui",
        "risk_tags": ["ui_navigation", "context_continuity"],
    },
    {
        "journey_id": "create_project_draft",
        "title": "创建项目草案",
        "entry_route": "/projects",
        "design_source_id": "phase106-project-workspace-design-v1",
        "required_project_context": False,
        "steps": [
            "load_project_list",
            "click_create_project_draft",
            "wait_for_feedback_banner",
        ],
        "required_components": ["create_project_button", "workspace_state_gate"],
        "expected_feedback": ["draft_create_action_visible", "workspace_feedback_success", "project_list_refresh"],
        "success_signals": [
            "draft_create_action_visible",
            "workspace_feedback_success",
            "project_list_refresh",
        ],
        "failure_signals": [
            "missing_create_action",
            "feedback_error",
            "project_list_not_refreshed",
        ],
        "failure_patterns": [
            "missing_create_action",
            "feedback_error",
            "project_list_not_refreshed",
        ],
        "defect_family": "uiux",
        "risk_tags": ["task_completion", "workspace_feedback"],
    },
    {
        "journey_id": "enter_command_center",
        "title": "进入质量驾驶舱",
        "entry_route": "/projects/:projectId",
        "design_source_id": "phase106-project-workspace-design-v1",
        "required_project_context": True,
        "steps": [
            "load_project_detail",
            "assert_current_project_visible",
            "open_command_center",
        ],
        "required_components": ["project_summary", "project_switcher", "command_center_entry"],
        "expected_feedback": ["current_project_visible", "navigation_success"],
        "success_signals": [
            "command_center_link_visible",
            "project_scoped_path_visible",
            "route_navigation_success",
        ],
        "failure_signals": [
            "missing_cta",
            "project_context_lost",
            "navigation_blocked",
        ],
        "failure_patterns": [
            "missing_cta",
            "project_context_lost",
            "navigation_blocked",
        ],
        "defect_family": "uiux",
        "risk_tags": ["journey_break", "project_scope_binding"],
    },
    {
        "journey_id": "open_risk_evidence",
        "title": "打开风险证据链",
        "entry_route": "/projects/:projectId",
        "design_source_id": "phase106-project-workspace-design-v1",
        "required_project_context": True,
        "steps": [
            "load_project_detail",
            "assert_current_project_visible",
            "open_risk_evidence",
        ],
        "required_components": ["project_summary", "project_switcher", "risk_evidence_entry"],
        "expected_feedback": ["risk_api_path_visible", "route_navigation_success"],
        "success_signals": [
            "risk_api_path_visible",
            "project_scoped_path_visible",
            "route_navigation_success",
        ],
        "failure_signals": [
            "missing_risk_entry",
            "project_context_lost",
            "navigation_blocked",
        ],
        "failure_patterns": [
            "missing_risk_entry",
            "project_context_lost",
            "navigation_blocked",
        ],
        "defect_family": "uiux",
        "risk_tags": ["risk_visibility", "project_scope_binding"],
    },
)


def build_frontend_task_journeys() -> list[dict[str, Any]]:
    """Return normalized frontend task journeys for manifest export."""
    return [dict(item) for item in FRONTEND_TASK_JOURNEYS]
