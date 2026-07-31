from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.implicit_rule_governance import (
    enrich_asset_with_governed_implicit_rule_projection,
)


FORMULA = "期末库存 = 期初库存 + 入库数量 - 出库数量"
IDEMPOTENCY = "同一付款请求不得重复成功扣款；重复提交时业务成功效果最多发生一次。"
STABLE_REF = "online-docs/implicit-rules/business-rules.md"


def _base(
    *,
    source_hash: str,
    source_version_id: str,
    source_id: str = "rules.md",
    source_refs: list[str] | None = None,
) -> dict:
    source = {
        "source_id": source_id,
        "status": "active",
        "content_hash": source_hash,
        "source_version_id": source_version_id,
        "source_type": "business_rules",
    }
    if source_refs:
        source["source_refs"] = list(source_refs)
    return {
        "source_inventory": [source],
        "field_dictionary": [],
        "data_tables": [],
        "interfaces": [],
        "permission_matrix": [],
        "state_machines": [],
        "relationships": [],
        "risk_domains": [],
        "oracle_library": [],
        "coverage_gaps": [],
        "rule_library": [],
        "business_fact_ledger": {"items": []},
    }


def _formula_fact(source_id: str = "rules.md") -> dict:
    return {
        "fact_id": f"fact:inventory-conservation:{source_id}",
        "fact_type": "DERIVED_VALUE",
        "status": "ACCEPTED",
        "raw_statement": FORMULA,
        "subject": {"entity_refs": ["期末库存"]},
        "formula_constraints": [
            {
                "raw": FORMULA,
                "lhs": "期末库存",
                "rhs": "期初库存 + 入库数量 - 出库数量",
            }
        ],
        "source_spans": [
            {
                "source_id": source_id,
                "locator": "rules.md#inventory",
                "document_block_id": "inventory",
            }
        ],
        "confidence": 1.0,
    }


def _idempotency_fact(source_id: str) -> dict:
    return {
        "fact_id": f"fact:payment-idempotency:{source_id}",
        "fact_type": "BUSINESS_RULE",
        "status": "ACCEPTED",
        "raw_statement": IDEMPOTENCY,
        "subject": {"entity_refs": ["付款请求"]},
        "action": {"canonical": "付款"},
        "source_spans": [
            {
                "source_id": source_id,
                "locator": "rules.md#payment",
                "document_block_id": "payment",
            }
        ],
        "confidence": 1.0,
    }


def test_source_prose_rule_is_typed_active_then_stale_without_executable_residue():
    first_input = _base(source_hash="hash-v1", source_version_id="srcv-v1")
    first_input["rule_library"] = [
        {
            "rule_id": "rule:source-inventory",
            "source_id": "rules.md",
            "source_type": "business_rules",
            "source_locator": "line:1",
            "statement": FORMULA,
            "rule_type": "conservation",
            "risk_type": "data_conservation",
            "severity": "P0",
        }
    ]
    first_input["business_fact_ledger"] = {"items": [_formula_fact()]}

    first = enrich_asset_with_governed_implicit_rule_projection(first_input)

    assert len(first["rule_library"]) == 1
    active = first["rule_library"][0]
    assert active["rule_id"] == "rule:source-inventory"
    assert active["derivation"] == "implicit_rule_entailment"
    assert active["logical_form"] == "CONSERVATION_EQUATION"
    assert active["operator"] == "equation_holds"
    assert active["authority_upgrade_receipt"]["parallel_rule_row_created"] is False
    assert first["implicit_rule_lifecycle_ledger"]["active_rule_count"] == 1
    assert first["implicit_rule_lifecycle_ledger"]["stale_rule_count"] == 0
    assert [row["rule_id"] for row in first["oracle_library"]] == [
        "rule:source-inventory"
    ]

    second_input = _base(source_hash="hash-v2", source_version_id="srcv-v2")
    second_input["implicit_rule_lifecycle_ledger"] = deepcopy(
        first["implicit_rule_lifecycle_ledger"]
    )

    second = enrich_asset_with_governed_implicit_rule_projection(second_input)

    assert second["rule_library"] == []
    lifecycle = second["implicit_rule_lifecycle_ledger"]
    stale = next(
        row
        for row in lifecycle["items"]
        if row["rule_id"] == "rule:source-inventory"
    )
    assert stale["status"] == "STALE"
    assert stale["execution_allowed"] is False
    assert stale["reason"] == "SOURCE_VERSION_CHANGED_RULE_NOT_REDERIVED"
    assert stale["rule_snapshot"]["logical_form"] == "CONSERVATION_EQUATION"
    assert lifecycle["active_rule_count"] == 0
    assert lifecycle["stale_rule_count"] == 1
    assert second["risk_domains"] == []
    assert second["oracle_library"] == []
    assert not any(
        row.get("from") == "rule:source-inventory"
        for row in second["relationships"]
    )
    assert second["implicit_rule_projection_gate"][
        "identity_reconciliation_before_lifecycle"
    ] is True
    assert second["implicit_rule_projection_gate"][
        "stale_rule_execution_allowed"
    ] is False


def test_same_semantic_rule_keeps_one_active_authority_across_content_versions():
    source_v1 = "canonical:rules:content-v1"
    first_input = _base(
        source_hash="hash-v1",
        source_version_id="srcv-v1",
        source_id=source_v1,
        source_refs=[STABLE_REF],
    )
    first_input["rule_library"] = [
        {
            "rule_id": "rule:parser:v1:payment",
            "source_id": source_v1,
            "source_type": "business_rules",
            "source_locator": "line:3",
            "statement": IDEMPOTENCY,
            "rule_type": "idempotency",
            "risk_type": "idempotency",
            "severity": "P0",
        }
    ]
    first_input["business_fact_ledger"] = {
        "items": [_idempotency_fact(source_v1)]
    }

    first = enrich_asset_with_governed_implicit_rule_projection(first_input)
    first_rule = first["rule_library"][0]
    durable_rule_id = first_rule["rule_id"]

    assert durable_rule_id.startswith("implicit_rule:")
    assert durable_rule_id != "rule:parser:v1:payment"
    assert first_rule["stable_source_refs"] == [STABLE_REF]
    assert first_rule["source_rule_origin"]["rule_id"] == (
        "rule:parser:v1:payment"
    )
    assert first_rule["rule_identity_authority"] == (
        "SOURCE_OCCURRENCE_REF_TYPED_SEMANTICS"
    )
    assert first["implicit_rule_lifecycle_ledger"]["active_rule_count"] == 1
    assert first["implicit_rule_lifecycle_ledger"]["stale_rule_count"] == 0

    source_v2 = "canonical:rules:content-v2"
    second_input = _base(
        source_hash="hash-v2",
        source_version_id="srcv-v2",
        source_id=source_v2,
        source_refs=[STABLE_REF],
    )
    second_input["rule_library"] = [
        {
            "rule_id": "rule:parser:v2:payment",
            "source_id": source_v2,
            "source_type": "business_rules",
            "source_locator": "line:3",
            "statement": IDEMPOTENCY,
            "rule_type": "idempotency",
            "risk_type": "idempotency",
            "severity": "P0",
        }
    ]
    second_input["business_fact_ledger"] = {
        "items": [_idempotency_fact(source_v2)]
    }
    second_input["implicit_rule_lifecycle_ledger"] = deepcopy(
        first["implicit_rule_lifecycle_ledger"]
    )

    second = enrich_asset_with_governed_implicit_rule_projection(second_input)

    assert len(second["rule_library"]) == 1
    second_rule = second["rule_library"][0]
    assert second_rule["rule_id"] == durable_rule_id
    assert second_rule["source_rule_origin"]["rule_id"] == (
        "rule:parser:v2:payment"
    )
    assert second_rule["stable_source_refs"] == [STABLE_REF]
    lifecycle = second["implicit_rule_lifecycle_ledger"]
    assert lifecycle["active_rule_count"] == 1
    assert lifecycle["stale_rule_count"] == 0
    item = next(row for row in lifecycle["items"] if row["rule_id"] == durable_rule_id)
    assert item["status"] == "ACTIVE"
    assert item["execution_allowed"] is True
    assert all(
        row.get("rule_id") != "rule:parser:v1:payment"
        for row in lifecycle["items"]
    )
    assert second["implicit_rule_identity_reconciliation_receipt"][
        "stable_rule_identity_count"
    ] == 1
