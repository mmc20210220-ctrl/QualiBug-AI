from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import (
    build_enterprise_understanding_model,
)


def _span(source: str, quote: str) -> list[dict]:
    return [{
        "source_id": source,
        "locator": f"{source}#object",
        "quote": quote,
        "quote_hash": f"hash-{source}-{quote}",
    }]


def _rule(fact_id: str, objects: list[str], *, actor: str = "管理员") -> dict:
    statement = f"{actor}可以查看{'、'.join(objects)}"
    return {
        "fact_id": fact_id,
        "kind": "RULE",
        "status": "ACCEPTED",
        "raw_statement": statement,
        "subject": {"entity_refs": objects, "actor_refs": [actor]},
        "object": {"entity_refs": objects},
        "action": {"canonical": "查看", "raw": "查看"},
        "conditions": [],
        "condition_combinator": "",
        "state_effects": [],
        "postconditions": [],
        "data_effects": [],
        "exceptions": [],
        "scope": {},
        "modality": "MAY",
        "polarity": "POSITIVE",
        "source_spans": _span(fact_id, statement),
    }


def _asset(facts: list[dict], **extra) -> dict:
    value = {
        "asset_id": "object-recognition-test",
        "business_fact_ledger": {"items": facts},
        "business_objects": [],
        "roles": [],
        "permission_matrix": [],
        "data_tables": [],
        "field_dictionary": [],
        "state_machines": [],
        "entity_relations": [],
        "cross_document_conflicts": [],
        "coverage_gaps": [],
        "source_inventory": [],
        "summary": {},
        "governance": {},
        "enterprise_comprehension_gate": {"status": "PASS", "entry_allowed": True},
    }
    value.update(extra)
    return value


def test_source_backed_object_is_formal_and_original_mentions_survive() -> None:
    asset = _asset([_rule("r-order", ["销售订单"])])
    model = build_enterprise_understanding_model(asset)

    assert [row["name"] for row in model["business_objects"]] == ["销售订单"]
    assert model["business_object_recognition_gate"]["status"] == "PASS"
    fact = asset["business_fact_ledger"]["items"][0]
    assert fact["subject"]["entity_refs"] == ["销售订单"]
    assert fact["subject"]["resolved_entity_refs"]


def test_actor_pollution_blocks_but_valid_object_remains_typed() -> None:
    asset = _asset([_rule("r-collision", ["管理员", "销售订单"])])
    model = build_enterprise_understanding_model(asset)

    assert [row["name"] for row in model["business_objects"]] == ["销售订单"]
    gate = model["business_object_recognition_gate"]
    assert gate["status"] == "BLOCKED_BUSINESS_OBJECT_TYPE_CONFLICT"
    assert gate["entry_allowed"] is False
    assert model["gate"]["entry_allowed"] is False
    fact = asset["business_fact_ledger"]["items"][0]
    assert fact["subject"]["business_object_rejected_mentions"] == ["管理员"]


def test_explicit_object_collision_is_retained_for_review() -> None:
    asset = _asset(
        [_rule("r-explicit", ["管理员"])],
        business_objects=[{"object": "管理员", "object_id": "object:admin"}],
    )
    model = build_enterprise_understanding_model(asset)

    assert [row["name"] for row in model["business_objects"]] == ["管理员"]
    gate = model["business_object_recognition_gate"]
    assert gate["status"] == "PARTIAL_BUSINESS_OBJECT_RECOGNITION"
    assert gate["entry_allowed"] is True
    assert any(
        row["reason_code"] == "EXPLICIT_OBJECT_AUTHORITY_WITH_ROLE_COLLISION"
        for row in model["unknowns"]
    )


def test_source_entity_inventory_declares_object_and_display_alias() -> None:
    asset = _asset(
        [],
        data_tables=[{
            "table_id": "entity:ticket",
            "name": "Ticket",
            "description": "工单，核心业务实体",
            "source_id": "business-rules",
            "source_locator": "BUSINESS_RULES.md#core-entities",
            "derivation": "entity_inventory_table",
        }],
    )
    model = build_enterprise_understanding_model(asset)

    assert len(model["business_objects"]) == 1
    obj = model["business_objects"][0]
    assert {obj["name"], *obj["aliases"]} == {"Ticket", "工单"}
    recognition = model["business_object_recognition"]
    assert set(recognition["accepted_labels"]) == {"Ticket", "工单"}
    assert recognition["gate"]["metrics"]["accepted_alias_edge_count"] == 1


def test_raw_entity_mentions_are_audit_trace_not_object_authority() -> None:
    fact = _rule("r-clean-slot", ["工单"])
    fact["subject"]["entity_mentions"] = ["只有主管可以升级工单"]
    fact["object"]["entity_mentions"] = ["升级工单成功"]
    model = build_enterprise_understanding_model(_asset([fact]))

    assert [row["name"] for row in model["business_objects"]] == ["工单"]
    recognition = model["business_object_recognition"]
    candidate_labels = {
        label for row in recognition["candidates"] for label in row["labels"]
    }
    assert "只有主管可以升级工单" not in candidate_labels
    assert "升级工单成功" not in candidate_labels
    assert recognition["raw_entity_mentions_used_as_object_authority"] is False


def test_derived_atomic_fact_cannot_seed_novel_object_type() -> None:
    fact = _rule("r-derived", ["成功"])
    fact["derivation"] = "accepted_atomic_claim_projection"
    fact["parent_fact_ref"] = "r-parent"
    model = build_enterprise_understanding_model(_asset([fact]))

    assert model["business_objects"] == []
    recognition = model["business_object_recognition"]
    assert recognition["candidates"] == []
    assert recognition["gate"]["metrics"]["rejected_fact_mention_count"] == 1
    assert recognition["rejected_fact_mentions"][0]["reason_code"] == (
        "DERIVED_FACT_CANNOT_DECLARE_BUSINESS_OBJECT"
    )


def test_composite_object_slot_cannot_create_second_object() -> None:
    declared = {
        "object": "工单",
        "object_id": "object:ticket",
        "source_id": "business-rules",
        "source_locator": "BUSINESS_RULES.md#ticket",
        "description": "工单",
    }
    fact = _rule("r-composite", ["工单", "创建新工单"])
    model = build_enterprise_understanding_model(
        _asset([fact], business_objects=[declared])
    )

    assert [row["name"] for row in model["business_objects"]] == ["工单"]
    recognition = model["business_object_recognition"]
    assert recognition["gate"]["metrics"]["rejected_fact_mention_count"] == 1
    rejected = recognition["rejected_fact_mentions"][0]
    assert rejected["label"] == "创建新工单"
    assert rejected["reason_code"] == (
        "COMPOSITE_PHRASE_CONTAINS_DECLARED_BUSINESS_OBJECT"
    )


def test_context_injected_resource_name_cannot_seed_object_type() -> None:
    fact = _rule("r-context-resource", ["membership"])
    fact["raw_statement"] = '"description": "创建成功"'
    fact["normalized_statement"] = '"description":"创建成功"'
    fact["source_spans"] = _span("r-context-resource", fact["raw_statement"])
    model = build_enterprise_understanding_model(_asset([fact]))

    assert model["business_objects"] == []
    rejected = model["business_object_recognition"]["rejected_fact_mentions"]
    assert rejected[0]["label"] == "membership"
    assert rejected[0]["reason_code"] == "OBJECT_SLOT_LABEL_NOT_SOURCE_ATTESTED"


def test_unresolved_predicate_cannot_seed_object_type() -> None:
    fact = _rule("r-unresolved-predicate", ["必须停用旧分配记录"])
    fact["raw_statement"] = "转移工单时必须停用旧分配记录"
    fact["normalized_statement"] = fact["raw_statement"]
    fact["source_spans"] = _span("r-unresolved-predicate", fact["raw_statement"])
    fact["action"] = {}
    model = build_enterprise_understanding_model(_asset([fact]))

    assert model["business_objects"] == []
    rejected = model["business_object_recognition"]["rejected_fact_mentions"]
    assert rejected[0]["label"] == "必须停用旧分配记录"
    assert rejected[0]["reason_code"] == (
        "OBJECT_TYPE_SEED_REQUIRES_RESOLVED_BEHAVIOR"
    )


def test_unique_source_surface_is_typed_without_automatic_identity_union() -> None:
    asset = _asset(
        [_rule("r-transfer", ["工单"])],
        data_tables=[
            {
                "table_id": "entity:assignment",
                "name": "Assignment",
                "description": "工单分配记录",
                "source_id": "business-rules",
                "source_locator": "BUSINESS_RULES.md#core-entities",
                "derivation": "entity_inventory_table",
            }
        ],
    )
    fact = asset["business_fact_ledger"]["items"][0]
    fact["raw_statement"] = "转移工单时必须停用旧分配记录"
    fact["normalized_statement"] = fact["raw_statement"]
    fact["source_spans"] = _span("r-transfer", fact["raw_statement"])

    model = build_enterprise_understanding_model(asset)
    recognition = model["business_object_recognition"]

    assert "分配记录" in recognition["accepted_labels"]
    surface = next(
        row for row in recognition["candidates"] if row["labels"] == ["分配记录"]
    )
    assert surface["status"] == "ACCEPTED_SURFACE_FORM_IDENTITY_PENDING"
    assert surface["surface_parent_labels"] == ["工单分配记录"]
    assert surface["automatic_identity_union_allowed"] is False
    assert surface["identity_resolution_eligible"] is False
    assert recognition["surface_form_identity_union_allowed"] is False
    assert "分配记录" not in {
        row["name"] for row in model["business_objects"]
    }
    assert not any(
        {edge["left_label"], edge["right_label"]}
        == {"工单分配记录", "分配记录"}
        for edge in recognition["accepted_alias_edges"]
    )
    assert any(
        row["reason_code"]
        == "SOURCE_ATTESTED_OBJECT_SURFACE_IDENTITY_UNRESOLVED"
        for row in recognition["unknowns"]
    )


def test_ambiguous_surface_shared_by_two_declarations_is_not_typed() -> None:
    asset = _asset(
        [],
        data_tables=[
            {
                "table_id": "entity:assignment",
                "name": "Assignment",
                "description": "工单分配记录",
                "source_id": "business-rules",
                "source_locator": "BUSINESS_RULES.md#core-entities",
                "derivation": "entity_inventory_table",
            },
            {
                "table_id": "entity:escalation",
                "name": "Escalation",
                "description": "工单升级记录",
                "source_id": "business-rules",
                "source_locator": "BUSINESS_RULES.md#core-entities",
                "derivation": "entity_inventory_table",
            },
        ],
        interfaces=[
            {
                "interface_id": "api:GET:/records",
                "source_id": "openapi",
                "source_locator": "openapi.yaml#/paths/records/get",
                "openapi_summary": "获取记录列表",
            }
        ],
    )

    model = build_enterprise_understanding_model(asset)
    recognition = model["business_object_recognition"]

    assert "记录" not in recognition["accepted_labels"]
    assert all(row["labels"] != ["记录"] for row in recognition["candidates"])


def test_full_declaration_occurrence_does_not_invent_short_surface() -> None:
    asset = _asset(
        [],
        data_tables=[
            {
                "table_id": "entity:attachment",
                "name": "Attachment",
                "description": "工单附件",
                "source_id": "business-rules",
                "source_locator": "BUSINESS_RULES.md#core-entities",
                "derivation": "entity_inventory_table",
            }
        ],
        interfaces=[
            {
                "interface_id": "api:GET:/attachments",
                "source_id": "openapi",
                "source_locator": "openapi.yaml#/paths/attachments/get",
                "openapi_summary": "获取工单附件",
            }
        ],
    )

    recognition = build_enterprise_understanding_model(asset)[
        "business_object_recognition"
    ]

    assert "工单附件" in recognition["accepted_labels"]
    assert "附件" not in recognition["accepted_labels"]
