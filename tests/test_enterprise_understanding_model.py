from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import (
    build_enterprise_understanding_model,
    build_lifecycles,
    build_object_graph,
    enrich_asset_with_enterprise_understanding,
)


def _span(source_id: str, quote: str) -> list[dict]:
    return [
        {
            "source_id": source_id,
            "locator": f"{source_id}#section=业务规则",
            "quote": quote,
            "quote_hash": f"hash-{source_id}",
        }
    ]


def _fact(
    fact_id: str,
    statement: str,
    *,
    entities: list[str],
    action: str,
    states: list[dict] | None = None,
    conditions: list[str] | None = None,
    actors: list[str] | None = None,
    status: str = "ACCEPTED",
    critical: bool = False,
) -> dict:
    return {
        "fact_id": fact_id,
        "kind": "STATE_TRANSITION" if states else "RULE",
        "status": status,
        "critical": critical,
        "raw_statement": statement,
        "subject": {"entity_refs": entities, "actor_refs": actors or []},
        "object": {"entity_refs": entities},
        "action": {"canonical": action, "raw": action},
        "conditions": conditions or [],
        "trigger": {"raw": (conditions or [""])[0]},
        "state_effects": states or [],
        "postconditions": [],
        "data_effects": [],
        "exceptions": [],
        "temporal_constraints": [],
        "scope": {},
        "modality": "ASSERTS",
        "polarity": "POSITIVE",
        "source_spans": _span(fact_id, statement),
    }


def _asset(facts: list[dict]) -> dict:
    return {
        "asset_id": "asset-understanding-test",
        "business_fact_ledger": {
            "schema": "qualibug.business-fact-ledger.v1",
            "items": facts,
        },
        "business_objects": [],
        "roles": [],
        "permission_matrix": [],
        "data_tables": [],
        "field_dictionary": [],
        "state_machines": [],
        "entity_relations": [],
        "cross_document_conflicts": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
        "enterprise_comprehension_gate": {
            "status": "PASS",
            "entry_allowed": True,
        },
    }


def test_source_backed_object_relation_is_built_without_token_guessing() -> None:
    statement = "销售订单审核通过后生成生产任务"
    fact = _fact(
        "fact-relation",
        statement,
        entities=["销售订单", "生产任务"],
        action="创建",
        conditions=["销售订单审核通过"],
    )

    relations, unknowns = build_object_graph(
        _asset([fact]),
        [fact],
        ["销售订单", "生产任务"],
    )

    assert not unknowns
    assert len(relations) == 1
    relation = relations[0]
    assert relation["source_object_ref"] == "销售订单"
    assert relation["relation_type"] == "GENERATES"
    assert relation["target_object_ref"] == "生产任务"
    assert relation["evidence"][0]["fact_id"] == "fact-relation"


def test_document_order_and_two_object_mentions_do_not_create_relation() -> None:
    fact = _fact(
        "fact-no-relation",
        "销售订单与生产任务由计划员查看",
        entities=["销售订单", "生产任务"],
        action="查看",
    )

    relations, unknowns = build_object_graph(
        _asset([fact]),
        [fact],
        ["销售订单", "生产任务"],
    )

    assert relations == []
    assert unknowns == []


def test_lifecycle_missing_from_state_is_visible_unknown() -> None:
    statement = "订单支付成功后状态变为已支付"
    fact = _fact(
        "fact-lifecycle-partial",
        statement,
        entities=["订单"],
        action="付款",
        states=[{"from_state": "", "to_state": "已支付", "raw": "状态变为已支付"}],
        conditions=["订单支付成功后"],
    )

    lifecycles, unknowns = build_lifecycles(
        _asset([fact]),
        [fact],
        ["订单"],
    )

    assert lifecycles[0]["object_ref"] == "订单"
    assert lifecycles[0]["status"] == "PARTIAL"
    assert any(row["reason_code"] == "LIFECYCLE_FROM_STATE_UNKNOWN" for row in unknowns)


def test_complete_chinese_facts_build_closed_enterprise_understanding_model() -> None:
    facts = [
        _fact(
            "fact-pay",
            "订单从待支付状态流转到已支付",
            entities=["订单"],
            action="付款",
            states=[{"from_state": "待支付", "to_state": "已支付", "raw": "待支付到已支付"}],
            actors=["客户"],
        ),
        _fact(
            "fact-ship",
            "订单从已支付状态流转到已发货",
            entities=["订单"],
            action="发货",
            states=[{"from_state": "已支付", "to_state": "已发货", "raw": "已支付到已发货"}],
            actors=["仓库管理员"],
        ),
    ]

    model = build_enterprise_understanding_model(_asset(facts))

    assert model["schema"] == "qualibug.enterprise-business-understanding-model.v1"
    assert [row["name"] for row in model["business_objects"]] == ["订单"]
    assert {row["name"] for row in model["operations"]} == {"付款", "发货"}
    assert model["lifecycles"][0]["states"] == ["已发货", "已支付", "待支付"]
    assert len(model["processes"]) == 1
    assert [step["operation_ref"] for step in model["processes"][0]["steps"]] == ["付款", "发货"]
    assert model["unknowns"] == []
    assert model["gate"]["status"] == "PASS"
    assert model["gate"]["entry_allowed"] is True
    assert model["metrics"]["projection_contract"] == "INTERNAL_MODEL_CLOSURE_NOT_RECALL_OR_ACCURACY"


def test_pending_critical_chinese_fact_blocks_understanding_model() -> None:
    fact = _fact(
        "fact-pending",
        "其不得发货",
        entities=[],
        action="发货",
        status="PENDING",
        critical=True,
    )
    fact["ambiguities"] = ["COREFERENCE_UNRESOLVED"]

    enriched = enrich_asset_with_enterprise_understanding(_asset([fact]))

    model = enriched["enterprise_understanding_model"]
    assert model["gate"]["status"] == "BLOCKED_ENTERPRISE_UNDERSTANDING_CRITICAL_UNKNOWN"
    assert model["gate"]["entry_allowed"] is False
    assert enriched["enterprise_comprehension_gate"]["entry_allowed"] is False
    assert any(
        gap["kind"] == "BLOCKED_ENTERPRISE_UNDERSTANDING_MODEL_INCOMPLETE"
        for gap in enriched["coverage_gaps"]
    )


def test_upstream_conflict_reason_is_preserved() -> None:
    asset = _asset([])
    asset["enterprise_comprehension_gate"] = {
        "status": "BLOCKED_BUSINESS_COMPREHENSION_CONFLICTING_FACTS",
        "entry_allowed": False,
        "unresolved_business_fact_conflicts": [{"conflict_id": "conflict-1"}],
    }
    asset["cross_document_conflicts"] = [
        {
            "conflict_id": "conflict-1",
            "status": "UNRESOLVED",
            "kind": "BUSINESS_MODALITY_CONTRADICTION",
        }
    ]

    enriched = enrich_asset_with_enterprise_understanding(asset)

    assert enriched["enterprise_comprehension_gate"]["status"] == "BLOCKED_BUSINESS_COMPREHENSION_CONFLICTING_FACTS"
    assert enriched["enterprise_comprehension_gate"]["understanding_model"]["status"] == "BLOCKED_UPSTREAM_BUSINESS_COMPREHENSION_GATE"
    assert enriched["enterprise_understanding_model"]["conflicts"][0]["conflict_id"] == "conflict-1"
