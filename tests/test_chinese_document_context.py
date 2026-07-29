from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center._chinese_business_comprehension import (
    build_chinese_first_comprehension,
)
from ai_test_asset_center.enterprise_knowledge_center._chinese_business_conflicts import (
    reconcile_chinese_business_fact_conflicts,
)
from ai_test_asset_center.enterprise_knowledge_center._chinese_document_context import (
    apply_chinese_document_context,
    build_chinese_document_semantic_tree,
)


def _asset(*objects: str) -> dict:
    return {
        "asset_id": "asset:document-context",
        "business_objects": [{"object": name} for name in objects],
        "data_tables": [],
        "roles": [],
        "permission_matrix": [],
        "rule_library": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }


def _understand(asset: dict, source: dict) -> dict:
    enriched = build_chinese_first_comprehension(asset, [source])
    enriched = apply_chinese_document_context(enriched, [source])
    return reconcile_chinese_business_fact_conflicts(enriched)


def test_document_tree_preserves_hierarchy_and_source_ranges() -> None:
    source = {
        "source_id": "prd-tree",
        "filename": "订单PRD.md",
        "text": "# 订单管理\n## 发货规则\n其不得发货。\n## 取消规则\n订单可以取消。",
    }

    tree = build_chinese_document_semantic_tree(source)
    nodes = tree["nodes"]

    assert tree["order_is_business_flow"] is False
    assert [node["level"] for node in nodes] == [0, 1, 2, 2]
    shipping = next(node for node in nodes if node["title"] == "发货规则")
    cancellation = next(node for node in nodes if node["title"] == "取消规则")
    assert shipping["parent_id"] == cancellation["parent_id"]
    assert shipping["end_offset"] <= cancellation["start_offset"]
    assert shipping["evidence"]["quote"] == "## 发货规则"


def test_unique_heading_context_resolves_pending_chinese_coreference() -> None:
    source = {
        "source_id": "prd-order",
        "filename": "业务规则.md",
        "text": "# 订单\n其不得发货。",
    }

    enriched = _understand(_asset("订单"), source)
    fact = next(
        row
        for row in enriched["business_fact_ledger"]["items"]
        if row.get("raw_statement") == "其不得发货"
    )

    assert fact["status"] == "ACCEPTED"
    assert fact["subject"]["entity_refs"] == ["订单"]
    assert fact["object"]["entity_refs"] == ["订单"]
    assert fact["document_context"]["cross_document_resolution_used"] is False
    assert fact["subject"]["resolution_evidence"][-1]["method"] == "unique_heading_context"
    assert enriched["document_context_resolution_receipt"]["resolved_fact_count"] == 1
    assert enriched["enterprise_comprehension_gate"]["entry_allowed"] is True
    assert any(
        rule.get("statement") == "其不得发货"
        for rule in enriched["rule_library"]
    )


def test_multiple_objects_in_heading_remain_ambiguous_and_blocked() -> None:
    source = {
        "source_id": "prd-ambiguous",
        "filename": "业务规则.md",
        "text": "# 订单与出库单\n其不得删除。",
    }

    enriched = _understand(_asset("订单", "出库单"), source)
    fact = next(
        row
        for row in enriched["business_fact_ledger"]["items"]
        if row.get("raw_statement") == "其不得删除"
    )

    assert fact["status"] == "PENDING"
    assert any(
        value.startswith("DOCUMENT_CONTEXT_HEADING_AMBIGUOUS")
        for value in fact["ambiguities"]
    )
    assert enriched["enterprise_comprehension_gate"]["entry_allowed"] is False
    assert enriched["document_context_resolution_receipt"]["unresolved_fact_count"] == 1
    assert not any(
        rule.get("statement") == "其不得删除"
        for rule in enriched["rule_library"]
    )


def test_section_change_prevents_previous_object_context_from_leaking() -> None:
    source = {
        "source_id": "prd-sections",
        "filename": "业务规则.md",
        "text": (
            "# 订单\n"
            "订单可以查看。\n"
            "# 出库单\n"
            "其不得删除。"
        ),
    }

    enriched = _understand(_asset("订单", "出库单"), source)
    fact = next(
        row
        for row in enriched["business_fact_ledger"]["items"]
        if row.get("raw_statement") == "其不得删除"
    )

    assert fact["status"] == "ACCEPTED"
    assert fact["subject"]["entity_refs"] == ["出库单"]
    assert "订单" not in fact["subject"]["entity_refs"]
    resolution = enriched["document_context_resolution_receipt"]["resolutions"][0]
    assert resolution["section_path"][-1] == "出库单"


def test_document_order_without_heading_or_same_section_fact_cannot_resolve() -> None:
    source = {
        "source_id": "prd-no-context",
        "filename": "通用说明.md",
        "text": "其不得发货。",
    }

    enriched = _understand(_asset("订单"), source)
    fact = next(
        row
        for row in enriched["business_fact_ledger"]["items"]
        if row.get("raw_statement") == "其不得发货"
    )

    assert fact["status"] == "PENDING"
    assert enriched["document_context_resolution_receipt"]["resolved_fact_count"] == 0
    assert enriched["document_context_resolution_receipt"]["unresolved"][0]["reason"] == "DOCUMENT_CONTEXT_NO_UNIQUE_REFERENCE"
    assert enriched["governance"]["document_order_cannot_create_business_flow"] is True
    assert enriched["governance"]["cross_document_proximity_cannot_resolve_references"] is True


def test_alias_aware_heading_and_prior_fact_collapse_to_one_identity() -> None:
    source = {
        "source_id": "prd-alias-context",
        "filename": "MO规则.md",
        "text": (
            "# 生产任务单\n"
            "生产任务单（MO）可以由计划员创建。\n"
            "该单据不得删除。"
        ),
    }
    asset = {
        "asset_id": "asset:alias-context",
        "business_objects": [{"object": "生产任务单"}, {"object": "MO"}],
        "data_tables": [],
        "roles": [{"role": "计划员"}],
        "permission_matrix": [],
        "rule_library": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }

    enriched = _understand(asset, source)
    deny = next(
        row
        for row in enriched["business_fact_ledger"]["items"]
        if row.get("action", {}).get("canonical") == "删除"
    )

    assert deny["status"] == "ACCEPTED"
    assert deny["subject"]["entity_refs"] == ["生产任务单"]
    assert "MO" not in deny["subject"]["entity_refs"]
    assert enriched["enterprise_comprehension_gate"]["entry_allowed"] is True


def test_same_section_prior_fact_resolves_without_object_in_heading() -> None:
    source = {
        "source_id": "prd-prior-fact",
        "filename": "发货说明.md",
        "text": (
            "# 发货规则\n"
            "订单可以查看。\n"
            "其不得发货。"
        ),
    }

    enriched = _understand(_asset("订单"), source)
    deny = next(
        row
        for row in enriched["business_fact_ledger"]["items"]
        if row.get("raw_statement") == "其不得发货"
    )

    assert deny["status"] == "ACCEPTED"
    assert deny["subject"]["entity_refs"] == ["订单"]
    methods = [row.get("method") for row in deny["subject"]["resolution_evidence"]]
    assert (
        "unique_prior_fact_in_same_section" in methods
        or "nearest_unambiguous_entity_context" in methods
    )
