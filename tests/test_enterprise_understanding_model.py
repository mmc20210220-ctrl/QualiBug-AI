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
    temporal_constraints: list[str] | None = None,
    time_window_constraints: list[dict] | None = None,
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
        "temporal_constraints": temporal_constraints or [],
        "time_window_constraints": time_window_constraints or [],
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


def test_source_backed_alias_merges_cross_document_object_identity() -> None:
    alias_fact = {
        "fact_id": "fact-alias",
        "kind": "TERM_ALIAS",
        "status": "ACCEPTED",
        "canonical_term": "生产任务单",
        "alias": "MO",
        "raw_statement": "生产任务单（MO）",
        "source_spans": _span("glossary", "生产任务单（MO）"),
    }
    mo_fact = _fact(
        "fact-mo-view",
        "管理员可以查看MO",
        entities=["MO"],
        action="查看",
        actors=["管理员"],
    )
    canonical_fact = _fact(
        "fact-mo-create",
        "计划员可以审核生产任务单",
        entities=["生产任务单"],
        action="审核",
        actors=["计划员"],
    )

    model = build_enterprise_understanding_model(
        _asset([alias_fact, mo_fact, canonical_fact])
    )

    assert [row["name"] for row in model["business_objects"]] == ["生产任务单"]
    assert "MO" in model["business_objects"][0]["aliases"]
    assert {row["name"] for row in model["operations"]} == {"查看", "审核"}
    assert all(row["object_refs"] == ["生产任务单"] for row in model["operations"])
    assert model["term_resolution"]["alias_to_object"]["MO"] == "生产任务单"
    assert model["gate"]["entry_allowed"] is True


def test_multi_condition_operation_without_combinator_is_visible_unknown() -> None:
    fact = _fact(
        "fact-multi-cond",
        "当金额超过1000时，如果审批未完成，管理员不得提交订单",
        entities=["订单"],
        action="提交",
        conditions=["金额超过1000", "审批未完成"],
        actors=["管理员"],
        critical=True,
    )
    fact["modality"] = "MUST_NOT"
    fact["condition_combinator"] = "UNRESOLVED"

    model = build_enterprise_understanding_model(_asset([fact]))

    assert any(
        row["reason_code"] == "CONDITION_COMBINATOR_UNRESOLVED" for row in model["unknowns"]
    )
    operation = model["operations"][0]
    assert operation["condition_combinator"] == "UNRESOLVED"
    assert operation["status"] == "PARTIAL"
    assert model["gate"]["entry_allowed"] is False


def test_conditioned_multi_outcome_projects_conditional_process() -> None:
    facts = [
        _fact(
            "fact-high",
            "订单在待审核状态下，当金额超过10000时审核后进入待终审",
            entities=["订单"],
            action="审核",
            states=[{"from_state": "待审核", "to_state": "待终审", "raw": "待审核到待终审"}],
            conditions=["金额超过10000"],
            actors=["财务主管"],
        ),
        _fact(
            "fact-low",
            "订单在待审核状态下，当金额不超过10000时审核后进入已通过",
            entities=["订单"],
            action="审核",
            states=[{"from_state": "待审核", "to_state": "已通过", "raw": "待审核到已通过"}],
            conditions=["金额不超过10000"],
            actors=["财务主管"],
        ),
    ]

    model = build_enterprise_understanding_model(_asset(facts))

    assert not any(row["reason_code"] == "LIFECYCLE_TARGET_CONTRADICTION" for row in model["unknowns"])
    assert len(model["processes"]) == 1
    process = model["processes"][0]
    assert process["process_type"] == "LIFECYCLE_CONDITIONAL"
    assert process["status"] == "UNDERSTOOD"
    assert len(process["branches"]) == 2
    outcomes = {row["to_state"] for row in process["branches"]}
    assert outcomes == {"待终审", "已通过"}
    assert all(row["conditions"] for row in process["branches"])
    assert model["gate"]["status"] == "PASS"


def test_operation_distinguished_branches_include_return_and_withdraw() -> None:
    facts = [
        _fact(
            "fact-create",
            "订单从新建状态流转到草稿",
            entities=["订单"],
            action="创建",
            states=[{"from_state": "新建", "to_state": "草稿", "raw": "新建到草稿"}],
            actors=["业务员"],
        ),
        _fact(
            "fact-submit",
            "订单从草稿状态流转到待审批",
            entities=["订单"],
            action="提交",
            states=[{"from_state": "草稿", "to_state": "待审批", "raw": "草稿到待审批"}],
            actors=["业务员"],
        ),
        _fact(
            "fact-approve",
            "订单从待审批状态流转到已通过",
            entities=["订单"],
            action="审批通过",
            states=[{"from_state": "待审批", "to_state": "已通过", "raw": "待审批到已通过"}],
            actors=["经理"],
        ),
        _fact(
            "fact-reject",
            "订单从待审批状态流转到已驳回",
            entities=["订单"],
            action="驳回",
            states=[{"from_state": "待审批", "to_state": "已驳回", "raw": "待审批到已驳回"}],
            actors=["经理"],
        ),
        _fact(
            "fact-withdraw",
            "订单从待审批状态流转到草稿",
            entities=["订单"],
            action="撤回",
            states=[{"from_state": "待审批", "to_state": "草稿", "raw": "待审批到草稿"}],
            actors=["业务员"],
        ),
        _fact(
            "fact-reedit",
            "订单从已驳回状态流转到草稿",
            entities=["订单"],
            action="重新编辑",
            states=[{"from_state": "已驳回", "to_state": "草稿", "raw": "已驳回到草稿"}],
            actors=["业务员"],
        ),
    ]

    model = build_enterprise_understanding_model(_asset(facts))

    process = next(row for row in model["processes"] if row["process_type"] != "MULTI_OBJECT_LINKED")
    assert process["process_type"] in {"LIFECYCLE_CONDITIONAL", "LIFECYCLE_NONLINEAR", "LIFECYCLE_WITH_LOOP"}
    assert process["branches"]
    path_kinds = {row["path_kind"] for row in process["exception_paths"]}
    assert "WITHDRAW" in path_kinds
    assert "RETURN" in path_kinds
    assert process["loops"] or "LOOP" in process.get("process_features", [])
    assert process["trigger"].get("state") == "新建"
    assert not any(row["reason_code"] == "PROCESS_BRANCH_UNDERDETERMINED" for row in model["unknowns"])
    assert model["gate"]["entry_allowed"] is True


def test_undetermined_branches_are_visible_partial_not_silent_drop() -> None:
    seed = _fact(
        "fact-seed",
        "管理员可以查看订单",
        entities=["订单"],
        action="查看",
        actors=["管理员"],
    )
    asset = _asset([seed])
    asset["state_machines"] = [
        {
            "name": "订单状态",
            "object": "订单",
            "source_id": "sm-order",
            "state_machine_id": "sm-order",
            "states": ["S0", "S1", "S2"],
            "transitions": [
                {"from_state": "S0", "to_state": "S1", "raw": "S0到S1"},
                {"from_state": "S0", "to_state": "S2", "raw": "S0到S2"},
            ],
        }
    ]

    model = build_enterprise_understanding_model(asset)

    assert len(model["processes"]) == 1
    process = model["processes"][0]
    assert process["process_type"] == "LIFECYCLE_PARTIAL"
    assert process["status"] == "PARTIAL"
    assert any(row["reason_code"] == "PROCESS_BRANCH_UNDERDETERMINED" for row in model["unknowns"])
    assert model["gate"]["entry_allowed"] is False


def test_document_order_does_not_invent_branch_sequence() -> None:
    seed = _fact(
        "fact-seed-order",
        "仓管可以查看出库单",
        entities=["出库单"],
        action="查看",
        actors=["仓管"],
    )
    asset = _asset([seed])
    asset["state_machines"] = [
        {
            "name": "出库单状态",
            "object": "出库单",
            "source_id": "sm-out",
            "state_machine_id": "sm-out",
            "states": ["待处理", "已拣货", "已复核"],
            "transitions": [
                {"from_state": "待处理", "to_state": "已拣货", "raw": "先写的拣货"},
                {"from_state": "待处理", "to_state": "已复核", "raw": "后写的复核"},
            ],
        }
    ]

    model = build_enterprise_understanding_model(asset)
    process = model["processes"][0]
    assert process["branches"] == []
    assert process["steps"] == []
    assert "PROCESS_BRANCH_UNDERDETERMINED" in {row["reason_code"] for row in model["unknowns"]}


def test_source_parallel_marker_projects_parallel_group() -> None:
    facts = [
        _fact(
            "fact-pick",
            "出库单在待处理状态下同时进行拣货后进入拣货中",
            entities=["出库单"],
            action="拣货",
            states=[{"from_state": "待处理", "to_state": "拣货中", "raw": "待处理到拣货中"}],
            conditions=["同时进行拣货"],
            actors=["仓管"],
        ),
        _fact(
            "fact-check",
            "出库单在待处理状态下同时进行复核后进入复核中",
            entities=["出库单"],
            action="复核",
            states=[{"from_state": "待处理", "to_state": "复核中", "raw": "待处理到复核中"}],
            conditions=["同时进行复核"],
            actors=["质检"],
        ),
    ]

    model = build_enterprise_understanding_model(_asset(facts))
    process = model["processes"][0]
    assert process["parallel_groups"]
    assert process["process_type"] in {"LIFECYCLE_PARALLEL", "LIFECYCLE_NONLINEAR", "LIFECYCLE_CONDITIONAL"}
    assert "PARALLEL" in process.get("process_features", [])


def test_unique_relation_chain_projects_multi_object_process() -> None:
    facts = [
        _fact(
            "fact-order-pay",
            "销售订单从待支付状态流转到已支付",
            entities=["销售订单"],
            action="付款",
            states=[{"from_state": "待支付", "to_state": "已支付", "raw": "待支付到已支付"}],
            actors=["客户"],
        ),
        _fact(
            "fact-order-ship",
            "销售订单从已支付状态流转到已发货",
            entities=["销售订单"],
            action="发货",
            states=[{"from_state": "已支付", "to_state": "已发货", "raw": "已支付到已发货"}],
            actors=["仓库管理员"],
        ),
        _fact(
            "fact-task-start",
            "生产任务从待开工状态流转到生产中",
            entities=["生产任务"],
            action="开工",
            states=[{"from_state": "待开工", "to_state": "生产中", "raw": "待开工到生产中"}],
            actors=["计划员"],
        ),
        _fact(
            "fact-task-done",
            "生产任务从生产中状态流转到已完工",
            entities=["生产任务"],
            action="完工",
            states=[{"from_state": "生产中", "to_state": "已完工", "raw": "生产中到已完工"}],
            actors=["计划员"],
        ),
        _fact(
            "fact-generate",
            "销售订单审核通过后生成生产任务",
            entities=["销售订单", "生产任务"],
            action="创建",
            conditions=["销售订单审核通过"],
        ),
    ]

    model = build_enterprise_understanding_model(_asset(facts))
    multi = [row for row in model["processes"] if row["process_type"] == "MULTI_OBJECT_LINKED"]
    assert len(multi) == 1
    assert [step["object_ref"] for step in multi[0]["steps"]] == ["销售订单", "生产任务"]
    assert multi[0]["object_links"][0]["relation_type"] == "GENERATES"
    assert not any(
        row["reason_code"] == "MULTI_OBJECT_PROCESS_UNDERDETERMINED" for row in model["unknowns"]
    )


def test_compensation_branch_is_visible_exception_path() -> None:
    facts = [
        _fact(
            "fact-paid",
            "订单从待支付状态流转到已支付",
            entities=["订单"],
            action="付款",
            states=[{"from_state": "待支付", "to_state": "已支付", "raw": "待支付到已支付"}],
            actors=["客户"],
        ),
        _fact(
            "fact-shipped",
            "订单从已支付状态流转到已发货",
            entities=["订单"],
            action="发货",
            states=[{"from_state": "已支付", "to_state": "已发货", "raw": "已支付到已发货"}],
            actors=["仓管"],
        ),
        _fact(
            "fact-refund",
            "订单从已支付状态流转到已退款",
            entities=["订单"],
            action="退款",
            states=[{"from_state": "已支付", "to_state": "已退款", "raw": "已支付到已退款"}],
            actors=["财务"],
        ),
    ]

    model = build_enterprise_understanding_model(_asset(facts))
    process = model["processes"][0]
    assert any(row["path_kind"] == "COMPENSATION" for row in process["exception_paths"])
    assert process["process_type"] in {"LIFECYCLE_CONDITIONAL", "LIFECYCLE_NONLINEAR"}


def _lifecycle_pair_facts(object_name: str, prefix: str) -> list[dict]:
    return [
        _fact(
            f"{prefix}-a",
            f"{object_name}从待处理状态流转到处理中",
            entities=[object_name],
            action="开工",
            states=[{"from_state": "待处理", "to_state": "处理中", "raw": "待处理到处理中"}],
            actors=["经办人"],
        ),
        _fact(
            f"{prefix}-b",
            f"{object_name}从处理中状态流转到已完成",
            entities=[object_name],
            action="完工",
            states=[{"from_state": "处理中", "to_state": "已完成", "raw": "处理中到已完成"}],
            actors=["经办人"],
        ),
    ]


def test_message_await_projects_multi_object_orchestration() -> None:
    facts = [
        *_lifecycle_pair_facts("销售订单", "order"),
        *_lifecycle_pair_facts("生产任务", "task"),
        _fact(
            "fact-await",
            "生产任务等待销售订单支付消息后开工",
            entities=["生产任务", "销售订单"],
            action="开工",
            conditions=["等待销售订单支付消息后"],
        ),
    ]

    model = build_enterprise_understanding_model(_asset(facts))
    relations = [row for row in model["object_relations"] if row["relation_type"] == "AWAITS"]
    assert relations
    multi = [
        row
        for row in model["processes"]
        if row["process_type"] in {"MULTI_OBJECT_ORCHESTRATION", "MULTI_OBJECT_LINKED"}
    ]
    assert len(multi) == 1
    process = multi[0]
    assert process["process_type"] == "MULTI_OBJECT_ORCHESTRATION"
    assert "ASYNC_MESSAGE_JOIN" in process["process_features"]
    assert process["waits"]
    assert process["waits"][0]["wait_kind"] == "MESSAGE_WAIT"
    assert [step["object_ref"] for step in process["steps"]] == ["销售订单", "生产任务"]


def test_timed_wait_and_cross_system_markers_project_orchestration() -> None:
    facts = [
        *_lifecycle_pair_facts("销售订单", "order"),
        *_lifecycle_pair_facts("出库单", "out"),
        _fact(
            "fact-notify",
            "销售订单跨系统通知出库单，出库单须在支付之后24小时以内处理",
            entities=["销售订单", "出库单"],
            action="通知",
            conditions=["跨系统通知", "支付之后24小时以内"],
            temporal_constraints=["支付之后24小时以内"],
            time_window_constraints=[
                {
                    "raw": "支付之后24小时以内",
                    "anchor": "支付",
                    "relation": "之后",
                    "duration": "24小时",
                    "window_ms": 86_400_000,
                    "source_backed": True,
                    "observer_operation_ref": "GET:/api/outbound/{id}",
                    "predicate": {
                        "json_path": "$.status",
                        "operator": "equals",
                        "expected_value": "DONE",
                    },
                    "async_policy": {
                        "enabled": True,
                        "expected_max_delay_ms": 86_400_000,
                        "poll_interval_ms": 60_000,
                        "max_attempts": 1_440,
                        "required_stable_observations": 1,
                        "terminal_condition": "source_declared_predicate",
                    },
                }
            ],
        ),
    ]

    model = build_enterprise_understanding_model(_asset(facts))
    notify = next(row for row in model["object_relations"] if row["relation_type"] == "NOTIFIES")
    assert "CROSS_SYSTEM" in notify["orchestration_markers"]
    multi = next(
        row for row in model["processes"] if row["process_type"] == "MULTI_OBJECT_ORCHESTRATION"
    )
    assert "CROSS_SYSTEM" in multi["process_features"]
    assert "TIMED_WAIT" in multi["process_features"]
    wait = next(row for row in multi["waits"] if row["wait_kind"] == "TIMED_WAIT")
    assert wait["observer_operation_ref"] == "GET:/api/outbound/{id}"
    assert wait["predicate"]["expected_value"] == "DONE"
    assert wait["async_policy"]["expected_max_delay_ms"] == 86_400_000
    assert wait["source_refs"]


def test_explicit_join_marker_projects_multi_object_join() -> None:
    facts = [
        *_lifecycle_pair_facts("采购申请", "req"),
        *_lifecycle_pair_facts("库存检查", "stock"),
        *_lifecycle_pair_facts("采购订单", "po"),
        _fact(
            "fact-join-req",
            "采购申请均完成后生成采购订单",
            entities=["采购申请", "采购订单"],
            action="创建",
            conditions=["均完成后"],
        ),
        _fact(
            "fact-join-stock",
            "库存检查均完成后生成采购订单",
            entities=["库存检查", "采购订单"],
            action="创建",
            conditions=["均完成后"],
        ),
    ]

    model = build_enterprise_understanding_model(_asset(facts))
    multi = next(
        row for row in model["processes"] if row["process_type"] == "MULTI_OBJECT_ORCHESTRATION"
    )
    assert multi["joins"]
    assert multi["joins"][0]["join_kind"] == "SOURCE_EXPLICIT_JOIN"
    assert set(multi["joins"][0]["incoming_object_refs"]) == {"采购申请", "库存检查"}
    assert multi["joins"][0]["target_object_ref"] == "采购订单"
    assert multi["status"] == "UNDERSTOOD"
    assert multi["entry_mode"] == "SOURCE_DECLARED_MULTI_START_JOIN"
    assert set(multi["trigger"]["object_refs"]) == {"采购申请", "库存检查"}
    assert not any(
        row["reason_code"]
        in {
            "MULTI_OBJECT_PROCESS_UNDERDETERMINED",
            "MULTI_OBJECT_PROCESS_START_UNDERDETERMINED",
        }
        for row in model["unknowns"]
    )


def test_join_without_marker_stays_underdetermined() -> None:
    facts = [
        *_lifecycle_pair_facts("采购申请", "req"),
        *_lifecycle_pair_facts("库存检查", "stock"),
        *_lifecycle_pair_facts("采购订单", "po"),
        _fact(
            "fact-gen-req",
            "采购申请审核通过后生成采购订单",
            entities=["采购申请", "采购订单"],
            action="创建",
        ),
        _fact(
            "fact-gen-stock",
            "库存检查通过后生成采购订单",
            entities=["库存检查", "采购订单"],
            action="创建",
        ),
    ]

    model = build_enterprise_understanding_model(_asset(facts))
    assert not any(
        row["process_type"] == "MULTI_OBJECT_ORCHESTRATION" for row in model["processes"]
    )
    assert any(
        row["reason_code"] == "MULTI_OBJECT_PROCESS_UNDERDETERMINED" for row in model["unknowns"]
    )


def test_operation_projects_compensation_and_structured_slots() -> None:
    fact = _fact(
        "fact-cancel-comp",
        "取消订单后必须补偿释放库存",
        entities=["订单"],
        action="取消",
        conditions=["取消订单后"],
        actors=["管理员"],
    )
    fact["modality"] = "MUST"
    fact["postconditions"] = ["必须补偿释放库存"]
    fact["compensation"] = ["补偿释放库存"]
    fact["compensations"] = ["补偿释放库存"]
    fact["data_effects"] = [
        {"statement": "释放库存", "action": "释放", "entity": "库存", "source_backed": True}
    ]
    fact["quantity_constraints"] = [
        {"raw": "超过1000", "operator": "超过", "value": "1000", "unit": "", "source_backed": True}
    ]
    fact["exception_scope"] = ["管理员"]
    fact["authorization_delegation"] = {
        "raw": "管理员授权财务代为审批",
        "delegator": "管理员",
        "delegatee": "财务",
        "source_backed": True,
    }

    model = build_enterprise_understanding_model(_asset([fact]))
    operation = model["operations"][0]
    assert "补偿释放库存" in operation["compensations"]
    assert "必须补偿释放库存" in operation["effects"] or "释放库存" in operation["effects"]
    assert operation["quantity_constraints"]
    assert operation["exception_scopes"] == ["管理员"]
    assert operation["authorization_delegations"]
    assert operation["authorization_delegations"][0]["delegatee"] == "财务"
    rule = next(row for row in model["rules"] if row["fact_id"] == "fact-cancel-comp")
    assert "补偿释放库存" in rule["compensations"]
    assert rule["postconditions"] == ["必须补偿释放库存"]
    assert rule["quantity_constraints"]
    assert rule["exception_scope"] == ["管理员"]
    assert rule["authorization_delegation"]["delegatee"] == "财务"
    assert rule["data_effects"]


def test_model_rules_preserve_condition_frame_slot() -> None:
    fact = _fact(
        "fact-frame-rule",
        "若订单已支付且库存充足，则仓库可以发货，否则系统标记缺货",
        entities=["订单"],
        action="发货",
        actors=["仓库"],
        conditions=["订单已支付", "库存充足"],
    )
    fact["condition_combinator"] = "AND"
    fact["condition_frame"] = {
        "kind": "IF_THEN_ELSE",
        "combinator": "AND",
        "branch": "THEN",
        "source_backed": True,
    }
    model = build_enterprise_understanding_model(_asset([fact]))
    rule = model["rules"][0]
    assert rule["condition_frame"]["kind"] == "IF_THEN_ELSE"
    assert rule["condition_frame"]["combinator"] == "AND"
    assert rule["condition_combinator"] == "AND"


def test_conflict_facts_promote_into_model_evidence_without_auto_pick() -> None:
    asset = _asset([])
    asset["cross_document_conflicts"] = [
        {
            "conflict_id": "conflict-modality-1",
            "status": "UNRESOLVED",
            "kind": "BUSINESS_MODALITY_CONTRADICTION",
            "reason": "same subject/action has incompatible modalities MUST_NOT and MAY",
            "automatic_resolution_allowed": False,
            "authority_decision": {
                "status": "UNRESOLVED",
                "selected_fact_id": "",
                "operator_required": True,
                "automatic_resolution_allowed": False,
            },
            "facts": [
                {
                    "fact_id": "fact-deny",
                    "source_id": "policy_v1",
                    "source_locator": "policy_v1.md#规则",
                    "quote": "普通用户不得修改已提交采购申请",
                    "modality": "MUST_NOT",
                    "statement": "普通用户不得修改已提交采购申请",
                },
                {
                    "fact_id": "fact-allow",
                    "source_id": "policy_v2",
                    "source_locator": "policy_v2.md#规则",
                    "quote": "普通用户可以修改已提交采购申请",
                    "modality": "MAY",
                    "statement": "普通用户可以修改已提交采购申请",
                },
            ],
        }
    ]
    model = build_enterprise_understanding_model(asset)
    conflict = model["conflicts"][0]
    assert conflict["message"].startswith("same subject/action")
    assert conflict["operator_action"]
    assert conflict["authority_decision"]["selected_fact_id"] == ""
    assert len(conflict["evidence"]) >= 2
    quotes = {row.get("quote") for row in conflict["evidence"]}
    assert "普通用户不得修改已提交采购申请" in quotes
    assert "普通用户可以修改已提交采购申请" in quotes
    assert model["gate"]["status"] == "BLOCKED_ENTERPRISE_UNDERSTANDING_CONFLICTING_FACTS"
    assert model["gate"]["unresolved_conflicts"][0]["conflict_id"] == "conflict-modality-1"
