from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.typed_fact_conflicts import (
    reconcile_typed_fact_conflicts,
)


def _base(fact_id: str, fact_type: str, predicate: str = "审批") -> dict:
    return {
        "fact_id": fact_id,
        "kind": "RULE",
        "fact_type": fact_type,
        "status": "ACCEPTED",
        "subject": {"actor_refs": ["经理"], "entity_refs": ["订单"]},
        "object": {"entity_refs": ["订单"]},
        "predicate": predicate,
        "action": {"canonical": predicate, "raw": predicate},
        "scope": {"organization": "本部门"},
        "exception_scope": [],
        "source_spans": [
            {
                "source_id": f"source:{fact_id}",
                "locator": f"rules.docx#{fact_id}",
                "quote": fact_id,
                "quote_hash": f"sha256:{fact_id}",
            }
        ],
    }


def test_condition_logic_conflict_is_fail_closed_and_pending_facts_survive(tmp_path) -> None:
    left = _base("fact:and", "PERMISSION_RULE")
    right = _base("fact:or", "PERMISSION_RULE")
    for fact, combinator in ((left, "AND"), (right, "OR")):
        fact["condition_frame"] = {
            "kind": "ALL" if combinator == "AND" else "ANY",
            "combinator": combinator,
            "conditions": ["状态为待审批", "所属部门一致"],
        }
    pending = _base("fact:pending", "BUSINESS_RULE", "提交")
    pending["status"] = "PENDING"
    pending["ambiguities"] = ["BUSINESS_SUBJECT_UNRESOLVED"]
    asset = {
        "project_id": "demo",
        "business_fact_ledger": {"items": [left, right, pending]},
        "cross_document_conflicts": [],
        "enterprise_comprehension_gate": {"status": "PASS", "entry_allowed": True},
        "coverage_gaps": [],
    }

    result = reconcile_typed_fact_conflicts(
        asset,
        project_id="demo",
        root=tmp_path,
    )

    conflicts = result["cross_document_conflicts"]
    assert len(conflicts) == 1
    assert conflicts[0]["kind"] == "CONDITION_LOGIC_CONTRADICTION"
    assert conflicts[0]["status"] == "UNRESOLVED"
    assert result["enterprise_comprehension_gate"]["entry_allowed"] is False
    statuses = {
        fact["fact_id"]: fact["status"]
        for fact in result["business_fact_ledger"]["items"]
    }
    assert statuses == {
        "fact:and": "CONFLICTING",
        "fact:or": "CONFLICTING",
        "fact:pending": "PENDING",
    }
    receipt = result["typed_business_fact_conflict_receipt"]
    assert receipt["fact_count_before_reconciliation"] == 3
    assert receipt["fact_count_after_reconciliation"] == 3
    assert receipt["all_fact_statuses_preserved"] is True


def test_formula_and_cardinality_conflicts_use_typed_slots(tmp_path) -> None:
    formula_a = _base("fact:formula-a", "DERIVED_VALUE", "DERIVED_AS")
    formula_b = _base("fact:formula-b", "DERIVED_VALUE", "DERIVED_AS")
    formula_a["formula_constraints"] = [
        {"lhs": "退款金额", "rhs": "实付金额-优惠"}
    ]
    formula_b["formula_constraints"] = [
        {"lhs": "退款金额", "rhs": "实付金额"}
    ]

    cardinality_a = _base("fact:card-a", "CARDINALITY_CONSTRAINT", "关联")
    cardinality_b = _base("fact:card-b", "CARDINALITY_CONSTRAINT", "关联")
    cardinality_a["subject"]["entity_refs"] = ["发票"]
    cardinality_a["object"]["entity_refs"] = ["结算单"]
    cardinality_b["subject"]["entity_refs"] = ["发票"]
    cardinality_b["object"]["entity_refs"] = ["结算单"]
    cardinality_a["claims"] = [
        {"claim_type": "CARDINALITY_CONSTRAINT", "value": {"maximum": 1}}
    ]
    cardinality_b["claims"] = [
        {"claim_type": "CARDINALITY_CONSTRAINT", "value": {"maximum": "MANY"}}
    ]

    asset = {
        "project_id": "demo",
        "business_fact_ledger": {
            "items": [formula_a, formula_b, cardinality_a, cardinality_b]
        },
        "cross_document_conflicts": [],
        "enterprise_comprehension_gate": {"status": "PASS", "entry_allowed": True},
        "coverage_gaps": [],
    }

    result = reconcile_typed_fact_conflicts(
        asset,
        project_id="demo",
        root=tmp_path,
    )

    kinds = {row["kind"] for row in result["cross_document_conflicts"]}
    assert kinds == {"FORMULA_CONTRADICTION", "CARDINALITY_CONTRADICTION"}
    assert result["typed_business_fact_conflict_receipt"]["conflict_count"] == 2
    assert result["typed_business_fact_conflict_receipt"][
        "automatic_resolution_allowed"
    ] is False
