from __future__ import annotations

from pathlib import Path

import pytest

from ai_test_asset_center import agent_task_grounding as grounding
from ai_test_asset_center.agent_task_grounding_store import apply_agent_task_grounding
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


def _persisted_analysis() -> dict:
    return {
        "schema": "qualibug.test-intelligence.analysis.v1",
        "summary": {"source_count": 2},
        "obligations": [
            {
                "obligation_id": "obl_1",
                "obligation_kind": "authorization",
                "title": "验证授权边界",
                "objective": "验证来源规则对应的授权行为",
                "operation_ref": "op_1",
                "actor_refs": ["actor_1"],
                "object_refs": ["object_1"],
                "source_refs": ["source_1"],
                "evidence": [
                    {
                        "source_id": "source_1",
                        "source_locator": "doc:section-1",
                        "quote_hash": "hash-1",
                        "fact_id": "fact-1",
                    }
                ],
            }
        ],
        "test_designs": [
            {
                "design_id": "design_1",
                "source_obligation_id": "obl_1",
                "action": {
                    "execution_surface": "NOT_SELECTED",
                    "binding_status": "NOT_GROUNDED",
                },
                "observer_binding_status": "NOT_GROUNDED",
                "oracle_binding_status": "NOT_GROUNDED",
            }
        ],
    }


def test_grounding_pins_existing_understanding_and_reuses_preflight(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        grounding,
        "_load_persisted_test_intelligence",
        lambda root, tenant_id, project_id: ("fp-current", _persisted_analysis()),
    )
    monkeypatch.setattr(
        grounding,
        "_test_intelligence_source_fingerprint",
        lambda root, tenant_id, project_id: "fp-current",
    )
    monkeypatch.setattr(
        grounding,
        "_scan_preflight_payload",
        lambda project_id, root, request=None: {
            "schema_version": "qualibug.environment-preflight.v1",
            "ready": True,
            "reasons": [],
            "input_checks": {"target": {"status": "passed"}},
            "target_policy_decision": {"write_allowed": True},
        },
    )
    task = {
        "task_id": "agt_1",
        "intent": "release_readiness",
    }

    result = grounding.build_agent_task_grounding(
        tmp_path,
        tenant_id="tenant-a",
        project_id="project-a",
        task=task,
    )

    assert result["source_snapshot"]["status"] == "PINNED"
    assert result["source_snapshot"]["source_revision_state"] == "CURRENT"
    assert result["selected_test_targets"] == ["obl_1"]
    assert result["grounding_summary"]["preflight_ready"] is True
    assert result["grounding_summary"]["runtime_bound_target_count"] == 0
    assert result["runtime_grounding_status"] == "BLOCKED"
    assert result["task_status"] == "BLOCKED"
    assert [item["code"] for item in result["grounding_blockers"]] == [
        "TEST_TARGET_RUNTIME_BINDING_PENDING"
    ]


def test_analysis_grounding_never_runs_scan_preflight(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        grounding,
        "_load_persisted_test_intelligence",
        lambda root, tenant_id, project_id: ("fp-current", _persisted_analysis()),
    )
    monkeypatch.setattr(
        grounding,
        "_test_intelligence_source_fingerprint",
        lambda root, tenant_id, project_id: "fp-current",
    )

    def forbidden_preflight(*args, **kwargs):
        raise AssertionError("analysis-only task must not invoke runtime preflight")

    monkeypatch.setattr(grounding, "_scan_preflight_payload", forbidden_preflight)
    result = grounding.build_agent_task_grounding(
        tmp_path,
        tenant_id="tenant-a",
        project_id="project-a",
        task={"task_id": "agt_1", "intent": "analyze_requirements"},
    )

    assert result["runtime_grounding_status"] == "NOT_REQUIRED"
    assert result["task_status"] == "READY"
    assert result["selected_test_targets"] == []


def test_grounding_persistence_emits_factual_events_idempotently(tmp_path: Path) -> None:
    task = create_agent_task(
        tmp_path,
        tenant_id="tenant-a",
        project_id="project-a",
        goal="检查发布风险",
        intent="release_readiness",
    )
    grounding_result = {
        "grounding_key": "grounding-1",
        "source_snapshot": {
            "status": "PINNED",
            "snapshot_ref": "uts_1",
            "source_revision_state": "CURRENT",
            "source_count": 2,
        },
        "selected_test_targets": ["obl_1"],
        "selected_test_target_snapshot": [{"obligation_id": "obl_1"}],
        "runtime_grounding_status": "BLOCKED",
        "runtime_context": {"preflight_ready": True},
        "grounding_blockers": [
            {
                "code": "TEST_TARGET_RUNTIME_BINDING_PENDING",
                "message": "pending",
                "source": "test_design",
            }
        ],
        "grounding_summary": {
            "selected_target_count": 1,
            "runtime_bound_target_count": 0,
            "preflight_ready": True,
        },
        "task_status": "BLOCKED",
    }

    first = apply_agent_task_grounding(
        tmp_path,
        tenant_id="tenant-a",
        project_id="project-a",
        task_id=task["task_id"],
        grounding=grounding_result,
        correlation_id="corr-ground",
    )
    second = apply_agent_task_grounding(
        tmp_path,
        tenant_id="tenant-a",
        project_id="project-a",
        task_id=task["task_id"],
        grounding=grounding_result,
        correlation_id="corr-ground",
    )
    events = list_agent_task_events(
        tmp_path,
        tenant_id="tenant-a",
        project_id="project-a",
        task_id=task["task_id"],
    )

    assert first["status"] == "BLOCKED"
    assert first["runtime_grounding_status"] == "BLOCKED"
    assert first["selected_test_targets"] == ["obl_1"]
    assert second["event_count"] == first["event_count"]
    assert [event["event_type"] for event in events] == [
        "TASK_CREATED",
        "UNDERSTANDING_SNAPSHOT_PINNED",
        "TEST_TARGET_SELECTION_EVALUATED",
        "RUNTIME_GROUNDING_EVALUATED",
    ]


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
        "/api/v1/projects/acme/agent-tasks/agt_1/ground"
    ) == ("ground", "acme", "agt_1")
    assert _parse_agent_task_route(
        "/api/v1/projects/acme/agent-tasks/agt_1/cancel"
    ) == ("cancel", "acme", "agt_1")
    assert _parse_agent_task_route("/api/v1/agent-tasks/agt_1") is None


def test_agent_task_handler_precedes_generic_http_router() -> None:
    mro = list(PrivatePilotHandler.__mro__)
    assert mro.index(AgentTaskHandlersMixin) < mro.index(HttpRoutingMixin)
