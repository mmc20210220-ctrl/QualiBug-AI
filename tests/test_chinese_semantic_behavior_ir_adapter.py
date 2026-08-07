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
        "actor:user", "entity:order", "op:query",
    }
    return lambda kind, ref: ref in accepted_set


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
