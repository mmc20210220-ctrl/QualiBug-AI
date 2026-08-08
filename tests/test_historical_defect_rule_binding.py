"""Unit tests for historical_defect_rule_binding (H3 adapter).

Locks in: generic historical-doc discovery, entry parsing, amount-class
classification → rule candidates with origin historical_defect, coverage
notes for non-amount classes, and the NO_DOCUMENT receipt. Synthetic docs
only — no benchmark material, no GT.
"""
import json

import pytest

from ai_test_asset_center.historical_defect_rule_binding import (
    enrich_asset_with_historical_defect_rules,
)

_HISTORICAL_DOC = """# 历史缺陷记录

> 企业通常提供给测试团队的历史问题材料；不写出当前缺陷答案，只描述同类问题。

## HB-001 订单金额口径不一致

历史上曾出现前端展示金额与后端支付金额不一致，原因是优惠券和数量计算顺序不一致。

## HB-002 报表数据未按角色过滤

后台报表曾向非财务角色展示敏感金额字段。
"""


def test_amount_class_entry_becomes_rule_candidate(tmp_path):
    doc_dir = tmp_path / "projects" / "demo_project" / "input"
    doc_dir.mkdir(parents=True)
    (doc_dir / "HISTORICAL_BUGS.md").write_text(_HISTORICAL_DOC, encoding="utf-8")
    asset = {"rule_library": []}
    out, receipt = enrich_asset_with_historical_defect_rules(
        asset, root=tmp_path, project_id="demo_project"
    )
    assert receipt["status"] == "OK"
    assert receipt["entries_total"] == 2
    assert receipt["amount_class_rules_derived"] == 1
    rules = out["rule_library"]
    assert len(rules) == 1
    rule = rules[0]
    assert rule["origin"] == "historical_defect"
    assert rule["statement"] == "HB-001 订单金额口径不一致"
    assert rule["source_id"].startswith("historical_defect:")
    assert rule["confidence"] == 0.55
    # Non-amount class recorded as coverage note, never a rule.
    notes = receipt["coverage_notes"]
    assert any(
        note.get("entry_id") == "HB-002" and "recorded only" in note.get("note", "")
        for note in notes
    )


def test_no_document_receipt(tmp_path):
    out, receipt = enrich_asset_with_historical_defect_rules(
        {"rule_library": []}, root=tmp_path, project_id="empty_project"
    )
    assert receipt["status"] == "NO_DOCUMENT"
    assert receipt["documents"] == []
    assert out["rule_library"] == []


def test_generic_historical_file_names_are_discovered(tmp_path):
    # 历史缺陷.md naming must be discovered like the English names.
    doc_dir = tmp_path / "platform_inputs" / "demo_project"
    doc_dir.mkdir(parents=True)
    (doc_dir / "HISTORICAL_BUGS.md").write_text(
        "## HB-001 计算顺序错误\n金额计算结果不一致\n", encoding="utf-8"
    )
    out, receipt = enrich_asset_with_historical_defect_rules(
        {"rule_library": []}, root=tmp_path, project_id="demo_project"
    )
    assert receipt["status"] == "OK"
    assert len(out["rule_library"]) == 1


def test_receipt_is_persisted_on_asset(tmp_path):
    doc_dir = tmp_path / "platform_inputs" / "demo_project"
    doc_dir.mkdir(parents=True)
    (doc_dir / "HISTORICAL_BUGS.md").write_text(
        "## HB-001 订单金额口径不一致\n金额不一致。\n", encoding="utf-8"
    )
    out, receipt = enrich_asset_with_historical_defect_rules(
        {"rule_library": []}, root=tmp_path, project_id="demo_project"
    )
    assert out["historical_defect_rule_receipt"] == receipt
    assert receipt["schema_version"] == "qualibug.historical-defect-rule-binding.v1"


def test_derived_rules_flow_through_binding_channel(tmp_path):
    """Historical amount-class rules must be consumable by the generic
    rule-contract binding stage (integration, no duplication of machinery)."""
    from ai_test_asset_center import rule_contract_validation_binding as binding

    doc_dir = tmp_path / "platform_inputs" / "demo_project"
    doc_dir.mkdir(parents=True)
    (doc_dir / "HISTORICAL_BUGS.md").write_text(
        "## HB-001 订单金额口径不一致\n金额计算顺序不一致。\n", encoding="utf-8"
    )
    out, _ = enrich_asset_with_historical_defect_rules(
        {"rule_library": []}, root=tmp_path, project_id="demo_project"
    )
    ir = {
        "entities": [
            {
                "id": "ent_order",
                "table": "orders",
                "fields": [
                    {"name": "total_amount", "field_id": "cf_ta", "semantic_type": "AMOUNT"},
                    {"name": "discount_amount", "field_id": "cf_da", "semantic_type": "AMOUNT"},
                    {"name": "payable_amount", "field_id": "cf_pa", "semantic_type": "AMOUNT"},
                ],
            }
        ],
        "operations": [
            {"id": "op_order", "method": "POST", "path": "/api/orders", "summary": "创建订单"},
        ],
        "invariants": [
            {
                "id": "bir_unbound_hb",
                "description": "HB-001 订单金额口径不一致",
                "expression": {"kind": "business_rule", "operator": "must_hold",
                               "operands": [], "raw": "HB-001 订单金额口径不一致"},
                "operation_refs": [],
                "source_rule_refs": ["historical:HB-001"],
            }
        ],
    }
    derived, receipt = binding._derive_validation_invariants(ir, out)
    assert any(
        "historical:HB-001" in inv["source_rule_refs"] for inv in derived
    )
    inv = next(inv for inv in derived if "historical:HB-001" in inv["source_rule_refs"])
    assert inv["expression"]["kind"] == "validation"
    assert "op_order" in inv["operation_refs"]
