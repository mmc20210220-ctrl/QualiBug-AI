"""P0-A: Chinese Semantic Frame → Behavior IR projection adapter.

Covers grounded-frame relation emission, ungrounded-frame zero contribution,
deterministic dedup merging, endpoint resolution guards, the deferred
state-transition family, and fail-closed validation.
"""

from __future__ import annotations

import pytest

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_semantic_behavior_ir_adapter import (
    apply_semantic_frames_to_behavior_ir,
    project_semantic_frames_to_behavior_ir,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_semantic_schema import (
    empty_frame,
    semantic_signature,
    validate_semantic_frame,
)


def _grounded_frame(
    *,
    frame_id: str,
    frame_type: str,
    modality: str = "ASSERTS",
    actor: str = "actor:user",
    operation: str = "",
    entity: str = "entity:order",
    ownership_kind: str = "",
) -> dict:
    frame = empty_frame(frame_id=frame_id, quote="合成 grounded 用例", source_id="source:test")
    frame["frame_type"] = frame_type
    frame["actor"]["mentions"] = ["普通用户"]
    frame["actor"]["grounded_actor_refs"] = [actor] if actor else []
    frame["actor"]["resolution_status"] = "GROUNDED" if actor else "OMITTED"
    frame["action"]["mentions"] = ["查询"]
    frame["action"]["grounded_operation_refs"] = [operation] if operation else []
    frame["action"]["resolution_status"] = "GROUNDED" if operation else "RESOLVED"
    frame["object"]["mentions"] = ["订单"]
    frame["object"]["grounded_entity_refs"] = [entity] if entity else []
    frame["object"]["resolution_status"] = "GROUNDED" if entity else "RESOLVED"
    frame["modality"] = {
        "type": modality,
        "raw_marker": "",
        "scope_refs": [],
        "resolution_status": "RESOLVED",
    }
    if ownership_kind:
        frame["scope"]["scope_type"] = "OWNERSHIP"
        frame["scope"]["ownership_relation"] = {
            "kind": ownership_kind,
            "target": "current_actor",
        }
        frame["scope"]["resolution_status"] = "RESOLVED"
    frame["technical_grounding"]["status"] = "GROUNDED"
    frame["technical_grounding"]["actor_refs"] = [actor] if actor else []
    frame["technical_grounding"]["entity_refs"] = [entity] if entity else []
    frame["resolution"]["reason_codes"] = []
    frame["resolution"]["status"] = "RESOLVED"
    frame["resolution"]["semantic_signature"] = semantic_signature(frame)
    assert validate_semantic_frame(frame) == []
    return frame


def _resolver_accepts_all(*, accepted: set[str] | None = None) -> object:
    accepted_set = accepted if accepted is not None else {
        "actor:user", "entity:order", "op:query", "op:submit",
        "op:query-status",
    }
    return lambda kind, ref: ref in accepted_set


def _timed_process_graph(*, graph_id: str = "graph:deadline") -> dict:
    return {
        "status": "COMPILED",
        "execution_graph_id": graph_id,
        "process_id": graph_id,
        "nodes": [
            {
                "node_id": "step:submit",
                "operation_ref": "op:submit",
                "actor_ref": "actor:user",
            },
            {
                "node_id": "step:query",
                "operation_ref": "op:query",
                "actor_ref": "actor:user",
            },
        ],
        "wait_contracts": [
            {
                "wait_id": "wait:query-deadline",
                "wait_kind": "TIMED_WAIT",
                "status": "BOUND",
                "source_backed": True,
                "source_node_id": "step:submit",
                "target_node_id": "step:query",
                "observer_operation_ref": "op:query-status",
                "predicate": {
                    "json_path": "$.state",
                    "operator": "equals",
                    "expected_value": "DONE",
                },
                "async_policy": {
                    "enabled": True,
                    "expected_max_delay_ms": 3_600_000,
                    "poll_interval_ms": 1_000,
                    "max_attempts": 3_600,
                    "required_stable_observations": 1,
                    "terminal_condition": "source_declared_predicate",
                },
                "time_window_constraints": [
                    {
                        "raw": "提交后1小时内",
                        "anchor": "提交后",
                        "duration": "1小时",
                        "window_ms": 3_600_000,
                        "source_backed": True,
                    }
                ],
                "source_refs": [{"source_id": "source:test", "locator": "r#b1"}],
            }
        ],
        "source_refs": [{"source_id": "source:test", "locator": "r#b1"}],
    }


def test_grounded_frames_emit_owns_permits_denies() -> None:
    frames = [
        _grounded_frame(
            frame_id="csf:own", frame_type="OWNERSHIP_RULE",
            ownership_kind="OWN",
        ),
        _grounded_frame(
            frame_id="csf:perm", frame_type="PERMISSION_RULE",
            modality="MAY", operation="op:query",
        ),
        _grounded_frame(
            frame_id="csf:deny", frame_type="PERMISSION_RULE",
            modality="MUST_NOT", operation="op:query",
        ),
    ]
    result = project_semantic_frames_to_behavior_ir(
        frames, ref_resolver=_resolver_accepts_all()
    )
    kinds = {(c["relation_type"], c["from_ref"], c["to_ref"]) for c in result["contributions"]}
    assert kinds == {
        ("owns", "actor:user", "entity:order"),
        ("permits", "actor:user", "op:query"),
        ("denies", "actor:user", "op:query"),
    }
    for contribution in result["contributions"]:
        assert contribution["source_refs"][0]["kind"] == "chinese_semantic_frame"
        assert contribution["source_refs"][0]["frame_id"]


def test_ungrounded_frames_contribute_nothing() -> None:
    # Production P0-A frames are ungrounded (TECHNICAL_GROUNDING_PENDING):
    # the projection must add nothing and receipt the skip.
    frame = _grounded_frame(frame_id="csf:ungr", frame_type="PERMISSION_RULE")
    frame["actor"]["grounded_actor_refs"] = []
    frame["actor"]["resolution_status"] = "RESOLVED"
    frame["action"]["grounded_operation_refs"] = []
    frame["object"]["grounded_entity_refs"] = []
    frame["object"]["resolution_status"] = "RESOLVED"
    frame["technical_grounding"]["status"] = "PENDING"
    frame["technical_grounding"]["actor_refs"] = []
    frame["technical_grounding"]["entity_refs"] = []
    frame["resolution"]["reason_codes"] = ["TECHNICAL_GROUNDING_PENDING"]
    frame["resolution"]["status"] = "PARTIALLY_RESOLVED"
    frame["resolution"]["semantic_signature"] = semantic_signature(frame)

    result = project_semantic_frames_to_behavior_ir([frame])
    assert result["contributions"] == []
    assert result["status"] == "PARTIAL"
    assert result["skips"][0]["reason_code"] == "TECHNICAL_GROUNDING_PENDING"
    assert result["receipt"]["payload"]["frames_considered"] == 1
    assert result["receipt"]["payload"]["contribution_count"] == 0


def test_permission_frame_without_grounded_operation_is_receipted() -> None:
    frame = _grounded_frame(
        frame_id="csf:noop", frame_type="PERMISSION_RULE",
        modality="MAY", operation="",
    )
    result = project_semantic_frames_to_behavior_ir([frame])
    assert result["contributions"] == []
    assert result["skips"][0]["reason_code"] == "ACTION_CONCEPT_UNRESOLVED"


def test_dangling_endpoint_is_blocked_not_emitted() -> None:
    frame = _grounded_frame(
        frame_id="csf:perm", frame_type="PERMISSION_RULE",
        modality="MAY", operation="op:ghost",
    )
    result = project_semantic_frames_to_behavior_ir(
        [frame], ref_resolver=_resolver_accepts_all(accepted={"actor:user"})
    )
    assert result["contributions"] == []
    assert result["skips"][0]["reason_code"] == "GROUNDING_EVIDENCE_INSUFFICIENT"


def test_state_transition_family_is_deferred_with_reason() -> None:
    frame = _grounded_frame(frame_id="csf:st", frame_type="STATE_TRANSITION")
    frame["state_transition"]["from_states"] = ["待审核"]
    frame["state_transition"]["to_states"] = ["已通过"]
    frame["state_transition"]["resolution_status"] = "RESOLVED"
    frame["resolution"]["semantic_signature"] = semantic_signature(frame)
    result = project_semantic_frames_to_behavior_ir([frame])
    assert result["contributions"] == []
    assert result["skips"][0]["reason_code"] == "INVARIANT_PROJECTION_DEFERRED"


def test_grounded_fixed_time_window_emits_temporal_invariant() -> None:
    frame = _grounded_frame(
        frame_id="csf:timed", frame_type="PERMISSION_RULE",
        modality="MAY", operation="op:query",
    )
    frame["time_constraints"] = [
        {
            "raw": "提交后1小时内",
            "anchor": "提交后",
            "relation": "WITHIN",
            "duration": "1小时",
            "window_ms": 3_600_000,
            "source_backed": True,
            "resolution_status": "RESOLVED",
            "origin": "own_clause",
            "evidence": [{"source_id": "source:test", "locator": "r#b1"}],
        }
    ]
    frame["resolution"]["semantic_signature"] = semantic_signature(frame)

    result = project_semantic_frames_to_behavior_ir(
        [frame], ref_resolver=_resolver_accepts_all()
    )
    invariants = [
        row for row in result["contributions"]
        if row["contribution_kind"] == "INVARIANT"
    ]
    assert len(invariants) == 1
    invariant = invariants[0]
    assert invariant["operation_refs"] == ["op:query"]
    assert invariant["expression"] == {
        "kind": "temporal",
        "operator": "within",
        "window_ms": 3_600_000,
        "anchor": "提交后",
        "duration": "1小时",
        "raw": "提交后1小时内",
        "temporal_semantics": "action_deadline",
        "anchor_grounding_status": "UNRESOLVED",
    }
    assert invariant["frame_family_evidence"] == {
        "grounded": True,
        "frame_type": "TIME_WINDOW_CONSTRAINT",
        "parent_frame_type": "PERMISSION_RULE",
        "frame_id": "csf:timed",
    }
    assert any(
        ref.get("kind") == "chinese_semantic_time_constraint"
        and ref.get("locator") == "r#b1"
        for ref in invariant["source_refs"]
    )


def test_fixed_time_window_binds_only_to_unique_source_process_wait() -> None:
    frame = _grounded_frame(
        frame_id="csf:timed-bound", frame_type="PERMISSION_RULE",
        modality="MAY", operation="op:query",
    )
    frame["time_constraints"] = [
        {
            "raw": "提交后1小时内",
            "anchor": "提交后",
            "relation": "WITHIN",
            "duration": "1小时",
            "window_ms": 3_600_000,
            "source_backed": True,
            "resolution_status": "RESOLVED",
        }
    ]
    frame["resolution"]["semantic_signature"] = semantic_signature(frame)

    result = project_semantic_frames_to_behavior_ir(
        [frame],
        ref_resolver=_resolver_accepts_all(),
        process_graphs=[_timed_process_graph()],
    )

    invariant = next(
        row for row in result["contributions"]
        if row["contribution_kind"] == "INVARIANT"
    )
    expression = invariant["expression"]
    assert expression["anchor_grounding_status"] == "BOUND"
    assert expression["completion_grounding_status"] == "BOUND"
    assert expression["anchor_operation_ref"] == "op:submit"
    assert expression["completion_operation_ref"] == "op:query"
    assert expression["completion_observer"] == "op:query-status"
    assert expression["process_graph_ref"] == "graph:deadline"
    assert expression["wait_contract_ref"] == "wait:query-deadline"


def test_ambiguous_process_wait_never_guesses_temporal_anchor() -> None:
    frame = _grounded_frame(
        frame_id="csf:timed-ambiguous", frame_type="PERMISSION_RULE",
        modality="MAY", operation="op:query",
    )
    frame["time_constraints"] = [
        {
            "raw": "提交后1小时内",
            "anchor": "提交后",
            "relation": "WITHIN",
            "duration": "1小时",
            "window_ms": 3_600_000,
            "source_backed": True,
            "resolution_status": "RESOLVED",
        }
    ]
    frame["resolution"]["semantic_signature"] = semantic_signature(frame)

    result = project_semantic_frames_to_behavior_ir(
        [frame],
        ref_resolver=_resolver_accepts_all(),
        process_graphs=[
            _timed_process_graph(graph_id="graph:deadline-1"),
            _timed_process_graph(graph_id="graph:deadline-2"),
        ],
    )

    invariant = next(
        row for row in result["contributions"]
        if row["contribution_kind"] == "INVARIANT"
    )
    assert invariant["expression"]["anchor_grounding_status"] == "UNRESOLVED"
    assert "anchor_operation_ref" not in invariant["expression"]
    assert any(
        row["reason_code"] == "TEMPORAL_PROCESS_WAIT_AMBIGUOUS"
        for row in result["skips"]
    )


def test_calendar_time_window_and_ambiguous_operation_stay_visible() -> None:
    calendar = _grounded_frame(
        frame_id="csf:calendar", frame_type="PERMISSION_RULE",
        modality="MAY", operation="op:query",
    )
    calendar["time_constraints"] = [
        {
            "raw": "提交后2个工作日内",
            "anchor": "提交后",
            "relation": "WITHIN",
            "duration": "2个工作日",
            "window_resolution_status": "UNRESOLVED",
            "window_resolution_reason": "TEMPORAL_CALENDAR_UNRESOLVED",
            "source_backed": True,
            "resolution_status": "RESOLVED",
        }
    ]
    calendar["resolution"]["semantic_signature"] = semantic_signature(calendar)
    calendar_result = project_semantic_frames_to_behavior_ir(
        [calendar], ref_resolver=_resolver_accepts_all()
    )
    assert not any(
        row["contribution_kind"] == "INVARIANT"
        for row in calendar_result["contributions"]
    )
    assert any(
        row["reason_code"] == "TEMPORAL_CALENDAR_UNRESOLVED"
        for row in calendar_result["skips"]
    )

    ambiguous = _grounded_frame(
        frame_id="csf:ambiguous", frame_type="PERMISSION_RULE",
        modality="MAY", operation="op:query",
    )
    ambiguous["action"]["grounded_operation_refs"] = ["op:query", "op:other"]
    ambiguous["time_constraints"] = [
        {
            "raw": "提交后1小时内",
            "anchor": "提交后",
            "relation": "WITHIN",
            "duration": "1小时",
            "window_ms": 3_600_000,
            "source_backed": True,
            "resolution_status": "RESOLVED",
        }
    ]
    ambiguous["resolution"]["semantic_signature"] = semantic_signature(ambiguous)
    ambiguous_result = project_semantic_frames_to_behavior_ir(
        [ambiguous], ref_resolver=_resolver_accepts_all(
            accepted={"actor:user", "entity:order", "op:query", "op:other"}
        )
    )
    assert ambiguous_result["contributions"] == []
    assert any(
        row["reason_code"] == "MULTIPLE_OPERATION_CANDIDATES"
        for row in ambiguous_result["skips"]
    )
    assert not any(
        row["reason_code"] == "TECHNICAL_GROUNDING_PENDING"
        for row in ambiguous_result["skips"]
    )
    assert ambiguous_result["receipt"]["payload"]["frames_with_contributions"] == 0


def test_invalid_frame_raises_fail_closed() -> None:
    frame = _grounded_frame(frame_id="csf:bad", frame_type="PERMISSION_RULE")
    frame["actor"]["resolution_status"] = "WHATEVER"
    with pytest.raises(ValueError, match="chinese_semantic_frame_invalid"):
        project_semantic_frames_to_behavior_ir([frame])


def test_apply_merges_by_deterministic_id_and_is_idempotent() -> None:
    frames = [
        _grounded_frame(
            frame_id="csf:own", frame_type="OWNERSHIP_RULE",
            ownership_kind="OWN",
        ),
        _grounded_frame(
            frame_id="csf:perm", frame_type="PERMISSION_RULE",
            modality="MAY", operation="op:query",
        ),
        _grounded_frame(
            frame_id="csf:ungr", frame_type="PERMISSION_RULE",
            modality="MAY", operation="",
        ),
    ]
    model: dict = {"relations": []}
    receipt = apply_semantic_frames_to_behavior_ir(
        model, frames, ref_resolver=_resolver_accepts_all()
    )
    assert len(model["relations"]) == 2
    assert receipt["payload"]["added_count"] == 2
    assert receipt["payload"]["deduped_count"] == 0
    assert receipt["payload"]["frames_considered"] == 3
    assert model["semantic_frame_projection_receipt"] is receipt

    # Applying the same frames again must not duplicate anything.
    apply_semantic_frames_to_behavior_ir(
        model, frames, ref_resolver=_resolver_accepts_all()
    )
    assert len(model["relations"]) == 2


def test_apply_merges_temporal_invariants_idempotently() -> None:
    frame = _grounded_frame(
        frame_id="csf:timed", frame_type="PERMISSION_RULE",
        modality="MAY", operation="op:query",
    )
    frame["time_constraints"] = [
        {
            "raw": "提交后1小时内",
            "anchor": "提交后",
            "relation": "WITHIN",
            "duration": "1小时",
            "window_ms": 3_600_000,
            "source_backed": True,
            "resolution_status": "RESOLVED",
        }
    ]
    frame["resolution"]["semantic_signature"] = semantic_signature(frame)
    model: dict = {"relations": [], "invariants": []}

    first = apply_semantic_frames_to_behavior_ir(
        model, [frame], ref_resolver=_resolver_accepts_all()
    )
    assert len(model["invariants"]) == 1
    assert first["payload"]["invariant_added_count"] == 1
    apply_semantic_frames_to_behavior_ir(
        model, [frame], ref_resolver=_resolver_accepts_all()
    )
    assert len(model["invariants"]) == 1


def test_apply_never_overwrites_existing_relations() -> None:
    # A pre-existing legacy relation with the same deterministic id stays put.
    frames = [
        _grounded_frame(
            frame_id="csf:own", frame_type="OWNERSHIP_RULE",
            ownership_kind="OWN",
        )
    ]
    model: dict = {"relations": []}
    apply_semantic_frames_to_behavior_ir(model, frames)
    legacy = dict(model["relations"][0])
    legacy["status"] = "conflicting"
    model["relations"] = [legacy]
    apply_semantic_frames_to_behavior_ir(model, frames)
    assert model["relations"] == [legacy]
