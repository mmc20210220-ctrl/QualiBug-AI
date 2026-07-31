from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.implicit_rule_governance import (
    enrich_asset_with_governed_implicit_rule_projection,
)


FORMULA = "期末库存 = 期初库存 + 入库数量 - 出库数量"


def _base(*, source_hash: str, source_version_id: str) -> dict:
    return {
        "source_inventory": [
            {
                "source_id": "rules.md",
                "status": "active",
                "content_hash": source_hash,
                "source_version_id": source_version_id,
                "source_type": "business_rules",
            }
        ],
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


def _formula_fact() -> dict:
    return {
        "fact_id": "fact:inventory-conservation",
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
                "source_id": "rules.md",
                "locator": "rules.md#inventory",
                "document_block_id": "inventory",
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
