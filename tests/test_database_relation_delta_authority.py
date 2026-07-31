from __future__ import annotations

from ai_test_asset_center import assertion_dsl_base
from ai_test_asset_center import database_relation_delta_lineage as lineage
from ai_test_asset_center import database_relation_delta_oracle as base_oracle
from ai_test_asset_center.database_relation_delta_experiment_projection import (
    ASSERTION_KIND,
    _stable_id,
)
from ai_test_asset_center.database_relation_delta_projection_gate import (
    SEMANTIC_PAIR_SCHEMA,
    semantic_relation_delta_pair_id,
)
from ai_test_asset_center.non_http_observers import install_non_http_observers


install_non_http_observers()


def _spec(*, source_refs: list[dict] | None = None, approved: bool = True) -> dict:
    row = {
        "assertion_id": "assert:authority",
        "source_assertion_kind": "conservation",
        "source_refs": (
            [{"kind": "business_rule", "locator": "BR-AUTHORITY"}]
            if source_refs is None
            else source_refs
        ),
        "database_relation_observer_ref": "relation-observer:ledger",
        "database_relationship_id": "fk:ledger:accounts",
        "relation_key": [
            {
                "child_database_field_name": "account_id",
                "parent_database_field_name": "id",
            }
        ],
        "root_observer_contract_ref": "observer:accounts",
        "root_before_draft_id": "draft:accounts:before",
        "root_after_draft_id": "draft:accounts:after",
        "root_table_ref": "table:accounts",
        "root_field_binding_id": (
            "binding:accounts:balance" if approved else ""
        ),
        "root_database_field_id": "field:accounts:balance",
        "root_database_field_name": "balance",
        "child_table_ref": "table:ledger_entries",
        "child_database_field_id": "field:ledger_entries:amount",
        "child_database_field_name": "amount",
        "aggregate": "SUM",
        "aggregate_alias": "related_value",
        "scope_count_alias": "related_scope_count",
        "comparison_operator": "EQ",
        "aggregate_on_left": False,
        "left_coefficient": -1,
        "right_coefficient": 1,
        "comparison_phase_pair": "BEFORE_AFTER",
        "tolerance": "0",
        "database_relation_delta_binding": {
            "relation_mapping_decision_id": (
                "decision:ledger" if approved else ""
            ),
        },
    }
    pair_id = semantic_relation_delta_pair_id(row)
    before_id = _stable_id(
        "database_relation_observer_execution_draft",
        row["database_relation_observer_ref"],
        row["assertion_id"],
        "BEFORE",
        pair_id,
    )
    after_id = _stable_id(
        "database_relation_observer_execution_draft",
        row["database_relation_observer_ref"],
        row["assertion_id"],
        "AFTER",
        pair_id,
    )
    row.update(
        {
            "relation_pair_id": pair_id,
            "relation_before_draft_id": before_id,
            "relation_after_draft_id": after_id,
        }
    )
    row["database_relation_delta_binding"].update(
        {
            "semantic_pair_schema": SEMANTIC_PAIR_SCHEMA,
            "relation_pair_id": pair_id,
            "relation_before_draft_id": before_id,
            "relation_after_draft_id": after_id,
            "pair_covers_complete_assertion_semantics": True,
        }
    )
    return row


def test_source_rule_evidence_is_required_before_numeric_comparison() -> None:
    result = lineage.evaluate_database_relation_delta_with_lineage(
        {
            "spec": _spec(source_refs=[]),
            "observations": {
                "approved_database_observer_phase_receipts": [],
                "approved_database_relation_phase_receipts": [],
            },
        }
    )

    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_DELTA_SOURCE_EVIDENCE_MISSING"
    )
    assert result["actual"]["source_evidence_present"] is False


def test_approved_root_and_relation_binding_ids_are_required() -> None:
    result = lineage.evaluate_database_relation_delta_with_lineage(
        {
            "spec": _spec(approved=False),
            "observations": {
                "approved_database_observer_phase_receipts": [],
                "approved_database_relation_phase_receipts": [],
            },
        }
    )

    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_DELTA_APPROVED_BINDING_MISSING"
    )
    assert result["actual"]["approved_binding_ids_present"] is False


def test_base_installer_cannot_bypass_lineage_gated_evaluator() -> None:
    assert base_oracle.install_database_relation_delta_assertion is (
        lineage.install_database_relation_delta_assertion
    )
    assert (
        assertion_dsl_base._REGISTERED_ASSERTION_EVALUATORS[ASSERTION_KIND]
        is lineage.evaluate_database_relation_delta_with_lineage
    )
    before = tuple(assertion_dsl_base.registered_assertion_kinds())
    assert base_oracle.install_database_relation_delta_assertion() == ASSERTION_KIND
    after = tuple(assertion_dsl_base.registered_assertion_kinds())
    assert after == before
