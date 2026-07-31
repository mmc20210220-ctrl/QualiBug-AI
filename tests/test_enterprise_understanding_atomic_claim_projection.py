from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.atomic_claim_projection import (
    project_atomic_claim_facts,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.builder import (
    build_enterprise_understanding_model,
)


def _parent_fact() -> dict:
    statement = "订单审批通过后，系统生成出库单、扣减库存并发送发货通知。"
    return {
        "fact_id": "fact:order-approved-effects",
        "kind": "RULE",
        "fact_type": "BUSINESS_RULE",
        "status": "ACCEPTED",
        "language": "zh-CN",
        "statement_frame_id": "frame:order-approved",
        "subject": {"actor_refs": ["系统"], "entity_refs": ["订单"]},
        "object": {"entity_refs": ["订单"]},
        "action": {"canonical": "审批通过", "raw": "审批通过"},
        "conditions": ["订单审批通过后"],
        "condition_combinator": "SINGLE_CONDITION",
        "condition_frame": {
            "kind": "LEAF",
            "combinator": "SINGLE_CONDITION",
            "conditions": ["订单审批通过后"],
            "exception_scopes": [],
        },
        "scope": {},
        "modality": "ASSERTS",
        "polarity": "POSITIVE",
        "exceptions": [],
        "exception_scope": [],
        "postconditions": ["生成出库单", "扣减库存", "发送发货通知"],
        "state_effects": [],
        "data_effects": [
            {"statement": "生成出库单", "action": "生成", "entity": "出库单"},
            {"statement": "扣减库存", "action": "扣减", "entity": "库存"},
            {"statement": "发送发货通知", "action": "发送", "entity": "发货通知"},
        ],
        "compensation": [],
        "compensations": [],
        "raw_statement": statement,
        "source_spans": [
            {
                "source_id": "source:rules",
                "locator": "rules.docx#paragraph=2",
                "quote": statement,
                "quote_hash": "sha256:statement",
                "document_block_id": "block:effects",
                "address_kind": "EXACT_SOURCE_LOCATOR",
            }
        ],
        "claims": [
            {
                "claim_id": "claim:create-outbound",
                "claim_type": "DATA_EFFECT",
                "predicate": "生成",
                "object_refs": ["出库单"],
                "value": {
                    "statement": "生成出库单",
                    "action": "生成",
                    "entity": "出库单",
                    "source_backed": True,
                },
                "source_backed": True,
            },
            {
                "claim_id": "claim:decrease-inventory",
                "claim_type": "DATA_EFFECT",
                "predicate": "扣减",
                "object_refs": ["库存"],
                "value": {
                    "statement": "扣减库存",
                    "action": "扣减",
                    "entity": "库存",
                    "source_backed": True,
                },
                "source_backed": True,
            },
            {
                "claim_id": "claim:send-notification",
                "claim_type": "DATA_EFFECT",
                "predicate": "发送",
                "object_refs": ["发货通知"],
                "value": {
                    "statement": "发送发货通知",
                    "action": "发送",
                    "entity": "发货通知",
                    "source_backed": True,
                },
                "source_backed": True,
            },
        ],
        "confidence": 1.0,
        "critical": False,
    }


def test_atomic_data_effects_become_independent_bindable_operations() -> None:
    asset = {
        "asset_id": "asset:demo",
        "business_fact_ledger": {
            "schema": "qualibug.business-fact-ledger.v2",
            "items": [_parent_fact()],
        },
        "enterprise_comprehension_gate": {"status": "PASS", "entry_allowed": True},
        "cross_document_conflicts": [],
        "coverage_gaps": [],
        "business_objects": [],
        "data_tables": [],
        "field_dictionary": [],
        "interfaces": [],
    }

    projected = project_atomic_claim_facts(asset)
    assert projected["atomic_claim_fact_projection_receipt"]["status"] == "PASS"
    assert projected["atomic_claim_fact_projection_receipt"]["projected_fact_count"] == 3
    assert len(projected["business_fact_ledger"]["items"]) == 4

    # Projection is idempotent and does not duplicate child facts on the model rebuild path.
    projected_again = project_atomic_claim_facts(projected)
    assert len(projected_again["business_fact_ledger"]["items"]) == 4

    model = build_enterprise_understanding_model(projected_again)
    operations = {
        (operation["name"], tuple(operation["object_refs"]))
        for operation in model["operations"]
    }
    assert ("审批通过", ("订单",)) in operations
    assert ("生成", ("出库单",)) in operations
    assert ("扣减", ("库存",)) in operations
    assert ("发送", ("发货通知",)) in operations

    child_operations = {
        operation["name"]: operation
        for operation in model["operations"]
        if operation["name"] in {"生成", "扣减", "发送"}
    }
    assert child_operations["生成"]["fact_refs"]
    assert "生成出库单" in child_operations["生成"]["effects"]
    assert "扣减库存" in child_operations["扣减"]["effects"]
    assert "发送发货通知" in child_operations["发送"]["effects"]
