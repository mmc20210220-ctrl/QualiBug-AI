from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center._chinese_business_comprehension import (
    build_chinese_first_comprehension,
)
from ai_test_asset_center.enterprise_knowledge_center._chinese_document_context import (
    apply_chinese_document_context,
)


def _asset() -> dict:
    return {
        "asset_id": "asset:document-context-authority",
        "business_objects": [{"object": "订单"}],
        "data_tables": [],
        "roles": [{"role": "仓库管理员"}],
        "permission_matrix": [],
        "rule_library": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }


def test_filename_is_not_formal_reference_context() -> None:
    source = {
        "source_id": "prd-filename-only",
        "filename": "订单规则.md",
        "text": "其不得发货。",
    }
    enriched = build_chinese_first_comprehension(_asset(), [source])
    enriched = apply_chinese_document_context(enriched, [source])
    fact = next(
        row
        for row in enriched["business_fact_ledger"]["items"]
        if row.get("raw_statement") == "其不得发货"
    )

    assert fact["status"] == "PENDING"
    assert enriched["document_context_resolution_receipt"]["resolved_fact_count"] == 0
    assert enriched["document_semantic_trees"]["items"][0]["filename_is_business_context"] is False
    assert enriched["business_fact_ledger"]["document_context_contract"]["filename_context_forbidden"] is True


def test_explicit_role_heading_can_resolve_omitted_actor() -> None:
    source = {
        "source_id": "prd-role-heading",
        "filename": "权限规则.md",
        "text": "# 仓库管理员\n其可以查看订单。",
    }
    enriched = build_chinese_first_comprehension(_asset(), [source])
    enriched = apply_chinese_document_context(enriched, [source])
    fact = next(
        row
        for row in enriched["business_fact_ledger"]["items"]
        if row.get("raw_statement") == "其可以查看订单"
    )

    assert fact["status"] == "ACCEPTED"
    assert fact["subject"]["actor_refs"] == ["仓库管理员"]
    assert fact["subject"]["entity_refs"] == ["订单"]
    assert fact["document_context"]["filename_used_as_context"] is False
    assert any(
        row.get("resolved_actor") == "仓库管理员"
        for row in enriched["document_context_resolution_receipt"]["resolutions"]
    )


def test_actorless_statement_without_reference_marker_is_not_enriched() -> None:
    source = {
        "source_id": "prd-no-actor-inference",
        "filename": "权限规则.md",
        "text": "# 仓库管理员\n订单可以查看。",
    }
    enriched = build_chinese_first_comprehension(_asset(), [source])
    enriched = apply_chinese_document_context(enriched, [source])
    fact = next(
        row
        for row in enriched["business_fact_ledger"]["items"]
        if row.get("raw_statement") == "订单可以查看"
    )

    assert fact["status"] == "ACCEPTED"
    assert fact["subject"]["actor_refs"] == []
    assert enriched["document_context_resolution_receipt"]["resolved_fact_count"] == 0
