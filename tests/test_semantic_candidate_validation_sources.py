from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center._candidate_validation import (
    candidates_to_behavior_ir_entries,
    project_validated_candidates_to_asset_spaces,
    validate_and_promote_candidates,
)
from ai_test_asset_center.enterprise_knowledge_center._semantic_extraction import (
    validate_semantic_candidates,
)


def _candidate(*, name: str = "订单", kind: str = "entity", source_id: str) -> dict:
    return {
        "name": name,
        "kind": kind,
        "source_id": source_id,
        "source_locator": f"{source_id}.md#chars=0-20",
        "verbatim_quote": f"{name}业务说明",
        "confidence": 0.7,
        "status": "CANDIDATE",
    }


def test_repetition_inside_one_document_is_not_multi_source_evidence() -> None:
    candidate = _candidate(source_id="prd-1")

    receipt = validate_and_promote_candidates([candidate, dict(candidate)])

    assert receipt.validated == []
    assert len(receipt.pending) == 2
    assert all(
        item["pending_reason"] == "no_independent_source_evidence"
        for item in receipt.pending
    )


def test_two_distinct_sources_can_validate_same_typed_candidate() -> None:
    receipt = validate_and_promote_candidates(
        [
            _candidate(source_id="prd-1"),
            _candidate(source_id="api-1"),
        ]
    )

    assert len(receipt.validated) == 2
    assert receipt.pending == []
    assert all(
        "multi_source_consistency" in item["promotion_evidence"]
        for item in receipt.validated
    )
    assert receipt.validated[0]["promotion_evidence_sources"][
        "multi_source_consistency"
    ] == ["api-1"]


def test_same_source_api_text_does_not_self_validate_candidate() -> None:
    receipt = validate_and_promote_candidates(
        [_candidate(source_id="prd-1")],
        interfaces=[
            {
                "interface_id": "interface:GET:/orders",
                "source_id": "prd-1",
                "path": "/订单",
                "summary": "查看订单",
            }
        ],
    )

    assert receipt.validated == []
    assert len(receipt.pending) == 1


def test_independent_api_source_is_recorded_as_promotion_evidence() -> None:
    receipt = validate_and_promote_candidates(
        [_candidate(source_id="prd-1")],
        interfaces=[
            {
                "interface_id": "interface:GET:/orders",
                "source_id": "api-1",
                "path": "/订单",
                "summary": "查看订单",
            }
        ],
    )

    assert len(receipt.validated) == 1
    promoted = receipt.validated[0]
    assert promoted["promotion_evidence_sources"]["cross_ref_api_path"] == [
        "api-1"
    ]


def test_only_validated_entity_candidate_can_enter_entity_space() -> None:
    entries = candidates_to_behavior_ir_entries(
        [
            _candidate(name="订单", kind="entity", source_id="prd-1"),
            _candidate(name="金额", kind="field", source_id="prd-1"),
        ],
        [_candidate(name="管理员", kind="actor", source_id="prd-1")],
    )

    assert entries[0]["object"] == "订单"
    assert entries[0]["semantic_candidate_kind"] == "entity"
    assert "object" not in entries[1]
    assert entries[1]["semantic_candidate_kind"] == "field"
    assert entries[1]["_cannot_enter_entity_space"] is True
    assert "object" not in entries[2]
    assert entries[2]["behavior_ir_promotion_status"] == "PENDING_DIAGNOSTIC_ONLY"


def test_validated_typed_candidates_enter_their_existing_asset_spaces() -> None:
    validated = [
        {**_candidate(name="订单", kind="entity", source_id="prd-1"), "candidate_id": "entity-order", "status": "VALIDATED"},
        {**_candidate(name="客户", kind="entity", source_id="prd-1"), "candidate_id": "entity-customer", "status": "VALIDATED"},
        {
            **_candidate(name="金额", kind="field", source_id="prd-1"),
            "candidate_id": "field-amount",
            "owner": "订单",
            "status": "VALIDATED",
            "typed_binding_status": "COMPLETE",
            "verbatim_quote": "订单包含金额字段",
        },
        {
            **_candidate(name="已支付", kind="state", source_id="prd-1"),
            "candidate_id": "state-paid",
            "owner": "订单",
            "status": "VALIDATED",
            "typed_binding_status": "COMPLETE",
            "verbatim_quote": "订单状态为已支付",
        },
        {**_candidate(name="审核员", kind="actor", source_id="prd-1"), "candidate_id": "actor-reviewer", "status": "VALIDATED"},
        {
            **_candidate(name="归属", kind="relation", source_id="prd-1"),
            "candidate_id": "relation-owner",
            "source_entity": "订单",
            "target_entity": "客户",
            "status": "VALIDATED",
            "typed_binding_status": "COMPLETE",
            "verbatim_quote": "订单归属客户",
        },
    ]

    projected = project_validated_candidates_to_asset_spaces(validated)

    objects = {row["object"]: row for row in projected["business_objects"]}
    assert set(objects) == {"订单", "客户"}
    assert objects["订单"]["key_business_fields"] == ["金额"]
    assert [row["role"] for row in projected["roles"]] == ["审核员"]
    assert projected["state_machines"][0]["object"] == "订单"
    assert projected["state_machines"][0]["states"] == ["已支付"]
    assert projected["entity_relations"][0]["from_entity"] == "订单"
    assert projected["entity_relations"][0]["to_entity"] == "客户"
    assert projected["entity_relations"][0]["relation_type"] == "归属"
    assert projected["coverage_gaps"] == []
    assert projected["projection_receipt"]["projected_by_kind"] == {
        "actor": 1,
        "entity": 2,
        "field": 1,
        "relation": 1,
        "state": 1,
    }


def test_candidate_gate_revalidates_typed_bindings_before_projection() -> None:
    candidates = [
        {
            **_candidate(name="金额", kind="field", source_id=source_id),
            "owner": "账户",
            "typed_binding_status": "COMPLETE",
            "verbatim_quote": "订单包含金额字段",
        }
        for source_id in ("prd-1", "api-1")
    ]

    receipt = validate_and_promote_candidates(candidates)

    assert receipt.validated == []
    assert len(receipt.rejected) == 2
    assert all(
        row["reason"] == "typed_binding_not_in_quote:owner"
        for row in receipt.rejected
    )


def test_projection_authority_rejects_forged_complete_typed_binding() -> None:
    projected = project_validated_candidates_to_asset_spaces(
        [{
            **_candidate(name="金额", kind="field", source_id="prd-1"),
            "candidate_id": "forged-field",
            "owner": "订单",
            "status": "VALIDATED",
            "typed_binding_status": "COMPLETE",
            "verbatim_quote": "金额字段",
        }],
        business_objects=[{
            "object": "订单",
            "source": "declared_prd",
            "key_business_fields": [],
        }],
    )

    assert projected["business_objects"][0]["key_business_fields"] == []
    assert projected["coverage_gaps"][0]["code"] == (
        "SEMANTIC_FIELD_OWNER_UNRESOLVED"
    )
    assert projected["coverage_gaps"][0]["missing_bindings"] == ["owner"]


def test_typed_candidate_missing_source_bound_owner_stays_a_visible_gap() -> None:
    projected = project_validated_candidates_to_asset_spaces([
        {
            **_candidate(name="金额", kind="field", source_id="prd-1"),
            "candidate_id": "field-amount",
            "status": "VALIDATED",
            "typed_binding_status": "INCOMPLETE",
            "typed_binding_gaps": ["owner"],
            "verbatim_quote": "金额字段",
        },
        {
            **_candidate(name="已支付", kind="state", source_id="prd-1"),
            "candidate_id": "state-paid",
            "owner": "未声明对象",
            "status": "VALIDATED",
            "typed_binding_status": "COMPLETE",
            "verbatim_quote": "未声明对象状态为已支付",
        },
        {
            **_candidate(name="归属", kind="relation", source_id="prd-1"),
            "candidate_id": "relation-owner",
            "source_entity": "订单",
            "status": "VALIDATED",
            "typed_binding_status": "INCOMPLETE",
            "typed_binding_gaps": ["target_entity"],
            "verbatim_quote": "订单归属",
        },
    ])

    assert projected["business_objects"] == []
    assert projected["state_machines"] == []
    assert projected["entity_relations"] == []
    assert {row["code"] for row in projected["coverage_gaps"]} == {
        "SEMANTIC_FIELD_OWNER_UNRESOLVED",
        "SEMANTIC_RELATION_ENDPOINT_UNRESOLVED",
        "SEMANTIC_STATE_OWNER_UNRESOLVED",
    }


def test_typed_bindings_survive_extraction_only_when_locally_source_anchored() -> None:
    source = "订单包含金额字段。订单状态为已支付。审核员负责复核。订单归属客户。"
    candidates = [
        {
            "kind": "field",
            "name": "金额",
            "owner": "订单",
            "verbatim_quote": "订单包含金额字段",
        },
        {
            "kind": "state",
            "name": "已支付",
            "owner": "订单",
            "verbatim_quote": "订单状态为已支付",
        },
        {
            "kind": "relation",
            "name": "归属",
            "source_entity": "订单",
            "target_entity": "客户",
            "verbatim_quote": "订单归属客户",
        },
    ]

    validated, rejected = validate_semantic_candidates(candidates, source, "prd-1")

    assert rejected == []
    assert validated[0]["owner"] == "订单"
    assert validated[1]["owner"] == "订单"
    assert validated[2]["source_entity"] == "订单"
    assert validated[2]["target_entity"] == "客户"
    assert all(row["typed_binding_status"] == "COMPLETE" for row in validated)

    invalid, rejected = validate_semantic_candidates(
        [{
            "kind": "field",
            "name": "金额",
            "owner": "账户",
            "verbatim_quote": "订单包含金额字段",
        }],
        source,
        "prd-1",
    )

    assert invalid == []
    assert rejected[0]["reason"] == "typed_binding_not_in_quote:owner"


def test_incremental_projection_recomputes_multi_source_entity_validation() -> None:
    from ai_test_asset_center.enterprise_knowledge_center.composition import (
        _incremental_refresh_semantic_candidate_projection,
    )

    asset = {
        "semantic_candidates": [
            _candidate(name="陌生业务对象", source_id="prd-1"),
            _candidate(name="陌生业务对象", source_id="api-1"),
        ],
        "interfaces": [],
        "data_tables": [],
        "rule_library": [],
        "state_machines": [],
        "business_objects": [
            {
                "object": "过期候选",
                "source": "semantic_extraction_validated",
            }
        ],
    }

    added = _incremental_refresh_semantic_candidate_projection(asset)

    assert added == 1
    assert [row["object"] for row in asset["business_objects"]] == [
        "陌生业务对象"
    ]
    receipt = asset["candidate_validation_receipt"]
    assert receipt["validated_count"] == 2
    assert receipt["pending_count"] == 0


def test_incremental_projection_refreshes_all_typed_asset_spaces() -> None:
    from ai_test_asset_center.enterprise_knowledge_center.composition import (
        _incremental_refresh_semantic_candidate_projection,
    )

    candidates: list[dict] = []
    for source_id in ("prd-1", "api-1"):
        candidates.extend([
            {**_candidate(name="订单", kind="entity", source_id=source_id)},
            {**_candidate(name="客户", kind="entity", source_id=source_id)},
            {
                **_candidate(name="金额", kind="field", source_id=source_id),
                "owner": "订单",
                "typed_binding_status": "COMPLETE",
                "verbatim_quote": "订单包含金额字段",
            },
            {
                **_candidate(name="已支付", kind="state", source_id=source_id),
                "owner": "订单",
                "typed_binding_status": "COMPLETE",
                "verbatim_quote": "订单状态为已支付",
            },
            {**_candidate(name="审核员", kind="actor", source_id=source_id)},
            {
                **_candidate(name="归属", kind="relation", source_id=source_id),
                "source_entity": "订单",
                "target_entity": "客户",
                "typed_binding_status": "COMPLETE",
                "verbatim_quote": "订单归属客户",
            },
        ])
    asset = {
        "semantic_candidates": candidates,
        "interfaces": [],
        "data_tables": [],
        "rule_library": [],
        "state_machines": [],
        "business_objects": [],
        "roles": [],
        "entity_relations": [],
        "coverage_gaps": [],
    }

    _incremental_refresh_semantic_candidate_projection(asset)

    objects = {row["object"]: row for row in asset["business_objects"]}
    assert objects["订单"]["key_business_fields"] == ["金额"]
    assert {row["role"] for row in asset["roles"]} == {"审核员"}
    assert asset["state_machines"][0]["states"] == ["已支付"]
    assert asset["entity_relations"][0]["relation_type"] == "归属"
    assert asset["typed_semantic_projection_receipt"]["gap_count"] == 0

    asset["semantic_candidates"] = []
    _incremental_refresh_semantic_candidate_projection(asset)

    assert asset["business_objects"] == []
    assert asset["roles"] == []
    assert asset["state_machines"] == []
    assert asset["entity_relations"] == []
    assert asset["typed_semantic_projection_receipt"]["validated_input_count"] == 0


def test_incremental_validation_does_not_self_validate_from_stale_semantic_state() -> None:
    from ai_test_asset_center.enterprise_knowledge_center.composition import (
        _incremental_refresh_semantic_candidate_projection,
    )

    candidates = [
        {
            **_candidate(name="已支付", kind="state", source_id=source_id),
            "owner": "订单",
            "verbatim_quote": "订单状态为已支付",
        }
        for source_id in ("prd-1", "api-1")
    ]
    asset = {
        "semantic_candidates": candidates,
        "interfaces": [],
        "data_tables": [],
        "rule_library": [],
        "business_objects": [{
            "object": "订单",
            "source": "declared_prd",
            "key_business_fields": [],
        }],
        "roles": [],
        "state_machines": [],
        "entity_relations": [],
        "coverage_gaps": [],
    }

    _incremental_refresh_semantic_candidate_projection(asset)
    assert asset["candidate_validation_receipt"]["validated_count"] == 2
    assert asset["state_machines"][0]["states"] == ["已支付"]

    asset["semantic_candidates"] = [candidates[1]]
    _incremental_refresh_semantic_candidate_projection(asset)

    assert asset["candidate_validation_receipt"]["validated_count"] == 0
    assert asset["candidate_validation_receipt"]["pending_count"] == 1
    assert asset["state_machines"] == []


def test_incremental_refresh_removes_stale_typed_additions_from_existing_models() -> None:
    from ai_test_asset_center.enterprise_knowledge_center.composition import (
        _incremental_refresh_semantic_candidate_projection,
    )

    candidates: list[dict] = []
    for source_id in ("prd-1", "api-1"):
        candidates.extend([
            {
                **_candidate(name="金额", kind="field", source_id=source_id),
                "owner": "订单",
                "verbatim_quote": "订单包含金额字段",
            },
            {
                **_candidate(name="已支付", kind="state", source_id=source_id),
                "owner": "订单",
                "verbatim_quote": "订单状态为已支付",
            },
            {
                **_candidate(name="备注", kind="field", source_id=source_id),
                "owner": "订单",
                "verbatim_quote": "订单包含备注字段",
            },
            {
                **_candidate(name="已完成", kind="state", source_id=source_id),
                "owner": "订单",
                "verbatim_quote": "订单状态为已完成",
            },
            {
                **_candidate(name="归属", kind="relation", source_id=source_id),
                "source_entity": "订单",
                "target_entity": "客户",
                "verbatim_quote": "订单归属客户",
            },
        ])
    asset = {
        "semantic_candidates": candidates,
        "interfaces": [],
        "data_tables": [],
        "rule_library": [],
        "business_objects": [{
            "object": "订单",
            "source": "declared_prd",
            "key_business_fields": ["编号", "金额"],
        }, {
            "object": "客户",
            "source": "declared_prd",
            "key_business_fields": [],
        }],
        "roles": [],
        "state_machines": [{
            "state_machine_id": "order-lifecycle",
            "object": "订单",
            "states": ["待处理", "已支付"],
            "transitions": [],
            "source_id": "prd-1",
        }],
        "entity_relations": [{
            "relation_id": "declared-order-customer",
            "from_entity": "订单",
            "to_entity": "客户",
            "relation_type": "归属",
            "derivation": "declared_prd",
        }],
        "coverage_gaps": [{"code": "SEMANTIC_OTHER_GAP"}],
    }

    _incremental_refresh_semantic_candidate_projection(asset)
    assert asset["business_objects"][0]["key_business_fields"] == ["编号", "金额", "备注"]
    assert asset["state_machines"][0]["states"] == ["待处理", "已支付", "已完成"]
    assert len(asset["entity_relations"][0]["semantic_candidate_refs"]) == 2

    asset["semantic_candidates"] = []
    _incremental_refresh_semantic_candidate_projection(asset)

    assert asset["business_objects"][0]["key_business_fields"] == ["编号", "金额"]
    assert asset["state_machines"][0]["states"] == ["待处理", "已支付"]
    assert asset["entity_relations"] == [{
        "relation_id": "declared-order-customer",
        "from_entity": "订单",
        "to_entity": "客户",
        "relation_type": "归属",
        "derivation": "declared_prd",
    }]
    assert asset["coverage_gaps"] == [{"code": "SEMANTIC_OTHER_GAP"}]
    assert "semantic_candidate_refs" not in asset["business_objects"][0]
    assert "semantic_state_bindings" not in asset["state_machines"][0]


def test_full_asset_build_projects_validated_typed_semantics(
    tmp_path,
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    from ai_test_asset_center.enterprise_knowledge_center import (
        build_enterprise_business_knowledge_asset,
        ingest_enterprise_knowledge_documents,
    )
    from ai_test_asset_center.enterprise_knowledge_center import (
        _semantic_extraction as semantic_extraction,
    )
    from ai_test_asset_center.enterprise_knowledge_center import (
        _api_mainline_base as api_mainline,
    )

    monkeypatch.setattr(semantic_extraction, "provider_status", lambda: "configured")
    monkeypatch.setattr(
        semantic_extraction,
        "semantic_extraction_availability",
        lambda requested: {
            "available": bool(requested),
            "reason": "configured",
            "detail": "test provider",
        },
    )

    def fake_semantic_batch(targets, *, max_chunks_per_source=None):
        results = []
        for source, _source_text in targets:
            source_id = source["source_id"]
            candidates = [
                {
                    "kind": "entity",
                    "name": "订单",
                    "source_id": source_id,
                    "verbatim_quote": "订单包含金额字段",
                    "confidence": 0.8,
                    "status": "CANDIDATE",
                    "typed_binding_status": "NOT_REQUIRED",
                    "typed_binding_gaps": [],
                },
                {
                    "kind": "field",
                    "name": "金额",
                    "owner": "订单",
                    "source_id": source_id,
                    "verbatim_quote": "订单包含金额字段",
                    "confidence": 0.8,
                    "status": "CANDIDATE",
                    "typed_binding_status": "COMPLETE",
                    "typed_binding_gaps": [],
                },
                {
                    "kind": "entity",
                    "name": "客户",
                    "source_id": source_id,
                    "verbatim_quote": "订单归属客户",
                    "confidence": 0.8,
                    "status": "CANDIDATE",
                    "typed_binding_status": "NOT_REQUIRED",
                    "typed_binding_gaps": [],
                },
                {
                    "kind": "relation",
                    "name": "归属",
                    "source_entity": "订单",
                    "target_entity": "客户",
                    "source_id": source_id,
                    "verbatim_quote": "订单归属客户",
                    "confidence": 0.8,
                    "status": "CANDIDATE",
                    "typed_binding_status": "COMPLETE",
                    "typed_binding_gaps": [],
                },
            ]
            receipt_payload = {
                "source_id": source_id,
                "status": "COMPLETED",
                "candidate_count": len(candidates),
                "max_chunks": max_chunks_per_source,
            }
            receipt = SimpleNamespace(
                source_id=source_id,
                source_digest=f"digest:{source_id}",
                max_chunks=max_chunks_per_source,
                status="COMPLETED",
                error="",
                unprocessed_ranges=[],
                candidates_validated=candidates,
                to_dict=lambda payload=receipt_payload: dict(payload),
            )
            results.append((source, receipt))
        return results, {
            "schema_version": "qualibug.semantic-extraction-batch.v1",
            "status": "COMPLETED",
            "target_source_count": len(results),
        }

    monkeypatch.setattr(
        semantic_extraction,
        "run_semantic_extraction_batch",
        fake_semantic_batch,
    )
    monkeypatch.setattr(
        api_mainline,
        "_extract_entity_relations",
        lambda *_args: [{
            "relation_id": "declared-order-customer",
            "from_entity": "订单",
            "to_entity": "客户",
            "relation_type": "归属",
            "derivation": "declared_prd",
        }],
    )
    project_id = "typed_semantic_full_build"
    ingest_enterprise_knowledge_documents(
        project_id,
        [
            {
                "filename": "prd.md",
                "text": "产品定义：订单包含金额字段。订单归属客户。",
            },
            {
                "filename": "api_notes.md",
                "text": "接口说明：订单包含金额字段。订单归属客户。",
            },
        ],
        root=tmp_path,
        actor={"name": "tester", "role": "project_owner"},
    )

    asset = build_enterprise_business_knowledge_asset(
        project_id,
        root=tmp_path,
        options={
            "enable_semantic_extraction": True,
            "semantic_rule_extraction_mode": "augment",
        },
    )

    order = next(row for row in asset["business_objects"] if row["object"] == "订单")
    assert order["key_business_fields"] == ["金额"]
    assert asset["candidate_validation_receipt"]["validated_count"] == 8
    assert asset["typed_semantic_projection_receipt"]["projected_by_kind"] == {
        "entity": 4,
        "field": 2,
        "relation": 2,
    }
    relation = asset["entity_relations"][0]
    assert relation["derivation"] == "declared_prd"
    assert len(relation["semantic_candidate_refs"]) == 2
    assert asset["typed_semantic_projection_receipt"]["gap_count"] == 0
    assert asset["summary"]["typed_semantic_projected_count"] == 8
