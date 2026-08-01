from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai_test_asset_center.enterprise_knowledge_center._chinese_business_comprehension import (
    analyze_chinese_business_source,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.structured_fact_compiler import (
    compile_structure_first_business_facts,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _asset() -> dict[str, Any]:
    return {
        "business_objects": [
            {"object": value}
            for value in (
                "订单", "组织", "采购订单", "订单头", "订单明细", "发票", "结算单",
                "库存", "退款金额", "实付金额", "优惠金额", "应付金额", "单价", "数量",
                "发货通知",
            )
        ],
        "roles": [
            {"role": value}
            for value in ("管理员", "订单创建人", "创建人", "财务人员", "系统", "其他人")
        ],
        "rule_library": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }


def _compact(fact: dict[str, Any]) -> dict[str, Any]:
    action = fact.get("action") if isinstance(fact.get("action"), dict) else {}
    subject = fact.get("subject") if isinstance(fact.get("subject"), dict) else {}
    object_part = fact.get("object") if isinstance(fact.get("object"), dict) else {}
    return {
        "id": fact.get("fact_id"),
        "status": fact.get("status"),
        "fact_type": fact.get("fact_type") or fact.get("kind"),
        "predicate": fact.get("predicate") or action.get("canonical") or action.get("raw"),
        "actors": subject.get("actor_refs") or [],
        "subjects": subject.get("entity_refs") or [],
        "objects": object_part.get("entity_refs") or [],
        "conditions": fact.get("conditions") or (fact.get("condition_frame") or {}).get("conditions") or [],
        "modality": fact.get("modality"),
        "quantity": fact.get("quantity_constraints") or [],
        "formula": fact.get("formula_constraints") or [],
        "postconditions": fact.get("postconditions") or [],
        "raw": fact.get("raw_statement"),
    }


def _compat(statement: str) -> list[dict[str, Any]]:
    _coverage, facts, _glossary = analyze_chinese_business_source(
        {"source_id": "mutation", "filename": "mutation.md", "text": statement},
        asset=_asset(),
    )
    return [_compact(fact) for fact in facts]


def _structured(statement: str) -> list[dict[str, Any]]:
    locator = f"mutation.md#line=1;chars=0-{len(statement)}"
    source = {
        "source_id": "mutation",
        "filename": "mutation.md",
        "text": statement,
        "document_structure": {
            "schema": "qualibug.document-structure-ir.v1",
            "blocks": [
                {
                    "block_id": "block:1",
                    "type": "PARAGRAPH",
                    "region": "body",
                    "text": statement,
                    "order": 1,
                    "source_locator": locator,
                    "evidence_address": {
                        "source_id": "mutation",
                        "source_locator": locator,
                        "address_kind": "EXACT_SOURCE_LOCATOR",
                    },
                }
            ],
        },
    }
    base = _asset()
    base["business_fact_ledger"] = {
        "schema": "qualibug.business-fact-ledger.v1",
        "items": [],
    }
    base["enterprise_comprehension_gate"] = {"status": "PASS", "entry_allowed": True}
    result = compile_structure_first_business_facts(base, [source])
    return [
        _compact(fact)
        for fact in _rows((result.get("business_fact_ledger") or {}).get("items"))
    ]


CASES = [
    {"id": "exact_one_only_can", "mode": "structured", "text": "每张发票仅能关联一个结算单。", "expect": {"type": "CARDINALITY_CONSTRAINT", "predicate": "EXACTLY_ONE", "objects": ["发票", "结算单"]}},
    {"id": "at_most_one", "mode": "structured", "text": "每张发票至多关联一个结算单。", "expect": {"type": "CARDINALITY_CONSTRAINT", "maximum": "1", "objects": ["发票", "结算单"]}},
    {"id": "at_least_one", "mode": "structured", "text": "每张发票至少关联一个结算单。", "expect": {"type": "CARDINALITY_CONSTRAINT", "minimum": "1", "objects": ["发票", "结算单"]}},
    {"id": "formula_compute_as", "mode": "structured", "text": "退款金额按实付金额减去优惠金额计算。", "expect": {"type": "DERIVED_VALUE", "formula_tokens": ["退款金额", "实付金额", "优惠金额"]}},
    {"id": "formula_multiply", "mode": "structured", "text": "应付金额为单价乘以数量。", "expect": {"type": "DERIVED_VALUE", "formula_tokens": ["应付金额", "单价", "数量"]}},
    {"id": "only_actor_permission", "mode": "compat", "text": "仅订单创建人可撤回尚未审批的订单。", "expect": {"predicate": "撤回", "actor": "订单创建人", "object": "订单", "condition": "尚未审批"}},
    {"id": "only_if_then", "mode": "compat", "text": "只有当库存充足时，系统才可发货。", "expect": {"predicate": "发货", "actor": "系统", "condition": "库存充足"}},
    {"id": "exception_prohibition", "mode": "compat", "text": "除管理员外，其他人不得删除已发货订单。", "expect": {"predicate": "删除", "modality": "MUST_NOT", "object": "订单"}},
    {"id": "temporal_within", "mode": "compat", "text": "付款成功后24小时内，财务人员必须开具发票。", "expect": {"predicate": "开具", "actor": "财务人员", "object": "发票"}},
    {"id": "relation_belongs_exact_one", "mode": "structured", "text": "每个订单必须归属于一个组织。", "expect": {"objects": ["订单", "组织"]}},
    {"id": "composition_mixed_cardinality", "mode": "structured", "text": "一个采购订单包含一个订单头和多个订单明细。", "expect_pairs": [["采购订单", "订单头"], ["采购订单", "订单明细"]]},
    {"id": "false_action_creator_field", "mode": "compat", "text": "订单创建人字段不得为空。", "forbid_predicates": ["创建"]},
    {"id": "false_action_refund_field", "mode": "compat", "text": "退款金额不得超过实付金额。", "forbid_predicates": ["退款"]},
    {"id": "false_action_shipping_notice", "mode": "compat", "text": "发货通知创建人字段由系统维护。", "forbid_predicates": ["发货", "创建"]},
    {"id": "cross_sentence_coreference", "mode": "compat", "text": "订单创建后进入待审批状态。只有创建人可以撤回该订单。若订单已审批，则不得撤回。", "expect": {"predicate": "撤回", "actor": "创建人", "object": "订单"}},
]


def _contains_tokens(value: Any, tokens: list[str]) -> bool:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return all(token in serialized for token in tokens)


def run() -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for case in CASES:
        try:
            facts = _structured(case["text"]) if case["mode"] == "structured" else _compat(case["text"])
            accepted = [
                fact
                for fact in facts
                if _text(fact.get("status")).upper() in {"", "ACCEPTED", "CONFIRMED"}
            ]
            checks: list[dict[str, Any]] = []
            expected = case.get("expect") or {}
            if expected:
                matches: list[dict[str, Any]] = []
                for fact in accepted:
                    coordinates = [*(fact.get("subjects") or []), *(fact.get("objects") or [])]
                    match = True
                    if expected.get("type") and _text(fact.get("fact_type")).upper() != expected["type"]:
                        match = False
                    if expected.get("predicate") and expected["predicate"] not in _text(fact.get("predicate")):
                        match = False
                    if expected.get("actor") and expected["actor"] not in (fact.get("actors") or []):
                        match = False
                    if expected.get("object") and expected["object"] not in coordinates:
                        match = False
                    if expected.get("condition") and not _contains_tokens(fact.get("conditions"), [expected["condition"]]):
                        match = False
                    if expected.get("modality") and _text(fact.get("modality")).upper() != expected["modality"]:
                        match = False
                    if expected.get("objects") and not all(value in coordinates for value in expected["objects"]):
                        match = False
                    if expected.get("minimum") and not _contains_tokens(fact.get("quantity"), [expected["minimum"]]):
                        match = False
                    if expected.get("maximum") and not _contains_tokens(fact.get("quantity"), [expected["maximum"]]):
                        match = False
                    if expected.get("formula_tokens") and not _contains_tokens(fact.get("formula"), expected["formula_tokens"]):
                        match = False
                    if match:
                        matches.append(fact)
                checks.append({"kind": "expected_fact", "pass": bool(matches), "matches": matches})
            if case.get("expect_pairs"):
                pairs = []
                for pair in case["expect_pairs"]:
                    found = any(
                        all(value in [*(fact.get("subjects") or []), *(fact.get("objects") or [])] for value in pair)
                        for fact in accepted
                    )
                    pairs.append({"pair": pair, "pass": found})
                checks.append({"kind": "expected_pairs", "pass": all(row["pass"] for row in pairs), "pairs": pairs})
            forbidden = case.get("forbid_predicates") or []
            if forbidden:
                bad = [
                    fact
                    for fact in accepted
                    if any(value == _text(fact.get("predicate")) for value in forbidden)
                ]
                checks.append({"kind": "forbidden_predicates", "pass": not bad, "bad": bad})
            results.append(
                {
                    "id": case["id"],
                    "mode": case["mode"],
                    "text": case["text"],
                    "pass": all(check["pass"] for check in checks) if checks else True,
                    "checks": checks,
                    "facts": facts,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "id": case["id"],
                    "mode": case["mode"],
                    "text": case["text"],
                    "pass": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return {
        "schema": "qualibug.chinese-explicit-fact-mutation-probe.v1",
        "case_count": len(results),
        "pass_count": sum(1 for result in results if result["pass"]),
        "fail_count": sum(1 for result in results if not result["pass"]),
        "results": results,
        "product_writeback_allowed": False,
        "probe_used_as_training_ground_truth": False,
    }


if __name__ == "__main__":
    output = Path(".github/chinese-fact-mutation-report.json")
    report = run()
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"pass": report["pass_count"], "fail": report["fail_count"]}, ensure_ascii=False))
