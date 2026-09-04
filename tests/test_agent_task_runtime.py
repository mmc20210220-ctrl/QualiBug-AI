from __future__ import annotations

from pathlib import Path

import pytest

from ai_test_asset_center.agent_task_store import (
    AgentTaskConflict,
    AgentTaskNotFound,
    AgentTaskValidationError,
    append_agent_task_event,
    cancel_agent_task,
    create_agent_task,
    get_agent_task,
    list_agent_task_events,
)
from ai_test_asset_center.private_pilot_agent_task_handlers import (
    AgentTaskHandlersMixin,
    _parse_agent_task_route,
)
from ai_test_asset_center.private_pilot_http_routing import HttpRoutingMixin
from ai_test_asset_center.private_pilot_service import PrivatePilotHandler


def test_agent_task_persists_goal_without_granting_execution(tmp_path: Path) -> None:
    task = create_agent_task(
        tmp_path,
        tenant_id="tenant-a",
        project_id="project-a",
        goal="检查当前版本是否存在发布阻断风险",
        intent="release_readiness",
        actor_role="qa_lead",
        correlation_id="corr-a",
    )

    assert task["status"] == "CREATED"
    assert task["runtime_grounding_status"] == "NOT_REQUESTED"
    assert task["source_snapshot"] == {"status": "NOT_PINNED", "snapshot_ref": ""}
    assert task["selected_test_targets"] == []
    assert task["execution_run_id"] == ""
    assert task["event_count"] == 1
    assert task["latest_event"]["event_type"] == "TASK_CREATED"
    assert task["latest_event"]["detail"]["execution_authority"] == "NOT_REQUESTED"

    loaded = get_agent_task(
        tmp_path,
        tenant_id="tenant-a",
        project_id="project-a",
        task_id=task["task_id"],
    )
    assert loaded["goal"] == task["goal"]
    assert loaded["intent"] == "release_readiness"


def test_agent_task_scope_is_tenant_and_project_bound(tmp_path: Path) -> None:
    task = create_agent_task(
        tmp_path,
        tenant_id="tenant-a",
        project_id="project-a",
        goal="分析现有需求",
        intent="analyze_requirements",
    )

    with pytest.raises(AgentTaskNotFound):
        get_agent_task(
            tmp_path,
            tenant_id="tenant-b",
            project_id="project-a",
            task_id=task["task_id"],
        )
    with pytest.raises(AgentTaskNotFound):
        get_agent_task(
            tmp_path,
            tenant_id="tenant-a",
            project_id="project-b",
            task_id=task["task_id"],
        )


def test_agent_task_event_ledger_records_only_explicit_transitions(tmp_path: Path) -> None:
    task = create_agent_task(
        tmp_path,
        tenant_id="tenant-a",
        project_id="project-a",
        goal="验证当前改动",
        intent="verify_changes",
    )
    append_agent_task_event(
        tmp_path,
        tenant_id="tenant-a",
        project_id="project-a",
        task_id=task["task_id"],
        event_type="UNDERSTANDING_STARTED",
        status="UNDERSTANDING",
        detail={"source": "explicit_test_transition"},
    )
    cancelled = cancel_agent_task(
        tmp_path,
        tenant_id="tenant-a",
        project_id="project-a",
        task_id=task["task_id"],
    )

    events = list_agent_task_events(
        tmp_path,
        tenant_id="tenant-a",
        project_id="project-a",
        task_id=task["task_id"],
    )
    assert [event["event_type"] for event in events] == [
        "TASK_CREATED",
        "UNDERSTANDING_STARTED",
        "TASK_CANCELLED",
    ]
    assert cancelled["status"] == "CANCELLED"

    with pytest.raises(AgentTaskConflict):
        append_agent_task_event(
            tmp_path,
            tenant_id="tenant-a",
            project_id="project-a",
            task_id=task["task_id"],
            event_type="PLANNING_STARTED",
            status="PLANNING",
        )


def test_agent_task_rejects_missing_goal_and_unknown_intent(tmp_path: Path) -> None:
    with pytest.raises(AgentTaskValidationError):
        create_agent_task(
            tmp_path,
            tenant_id="tenant-a",
            project_id="project-a",
            goal="",
            intent="release_readiness",
        )
    with pytest.raises(AgentTaskValidationError):
        create_agent_task(
            tmp_path,
            tenant_id="tenant-a",
            project_id="project-a",
            goal="检查",
            intent="invent_everything",
        )


def test_agent_task_route_contract_is_project_scoped() -> None:
    assert _parse_agent_task_route("/api/v1/projects/acme/agent-tasks") == (
        "collection",
        "acme",
        "",
    )
    assert _parse_agent_task_route("/api/v1/projects/acme/agent-tasks/agt_1") == (
        "task",
        "acme",
        "agt_1",
    )
    assert _parse_agent_task_route(
        "/api/v1/projects/acme/agent-tasks/agt_1/events"
    ) == ("events", "acme", "agt_1")
    assert _parse_agent_task_route(
        "/api/v1/projects/acme/agent-tasks/agt_1/cancel"
    ) == ("cancel", "acme", "agt_1")
    assert _parse_agent_task_route("/api/v1/agent-tasks/agt_1") is None


def test_agent_task_handler_precedes_generic_http_router() -> None:
    mro = list(PrivatePilotHandler.__mro__)
    assert mro.index(AgentTaskHandlersMixin) < mro.index(HttpRoutingMixin)
