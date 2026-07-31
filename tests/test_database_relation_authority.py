from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.database_mapping_authority import (
    ACTION_APPROVE_READ_ONLY_OBSERVER,
    ACTION_REJECT_MAPPING,
    DATABASE_MAPPING_DECISION_SCHEMA,
)
from ai_test_asset_center.enterprise_knowledge_center.database_relation_authority import (
    apply_database_relation_authority_decisions,
    database_relation_candidate_fingerprint,
)


def _candidate() -> dict:
    return {
        "schema": "qualibug.database-relation-observer-candidate.v1",
        "candidate_id": "relation-candidate:orders-lines",
        "candidate_kind": "relation",
        "root_observer_id": "observer:orders",
        "operation_schema_binding_id": "binding:update-order",
        "interface_id": "api:PATCH:/orders/{id}",
        "method": "PATCH",
        "path": "/orders/{id}",
        "database_relationship_id": "fk:order_lines:orders",
        "parent_table_id": "table:orders",
        "parent_columns": ["id"],
        "root_selected_identity_key": ["id"],
        "child_table_id": "table:order_lines",
        "child_columns": ["order_id"],
        "predicate_pairs": [
            {
                "ordinal": 0,
                "child_database_field_name": "order_id",
                "parent_database_field_name": "id",
                "parent_database_field_id": "field:orders:id",
                "parent_field_binding_id": "binding:orders:id",
                "value_source": "request.parameter.id",
            }
        ],
        "available_child_fields": [
            {
                "database_field_id": "field:order_lines:amount",
                "database_field_name": "amount",
                "database_declared_type": "NUMERIC(12,2)",
                "nullable": False,
                "source_id": "source:pdm",
                "source_locator": "#/tables/order_lines/amount",
            }
        ],
        "source_id": "source:pdm",
        "source_locator": "#/foreignKeys/order_lines_orders",
        "root_mapping_decision_refs": ["decision:orders", "decision:orders:id"],
        "status": "PENDING_RELATION_AUTHORITY",
        "observer_candidate_only": True,
        "observer_authority_allowed": False,
        "relation_mapping_confirmed": False,
        "read_only": True,
        "write_target_allowed": False,
        "oracle_authority_allowed": False,
    }


def _decision(candidate: dict, action: str) -> dict:
    fingerprint = database_relation_candidate_fingerprint(candidate)
    return {
        "schema": DATABASE_MAPPING_DECISION_SCHEMA,
        "decision_id": f"decision:relation:{action.lower()}",
        "project_id": "relation_authority",
        "candidate_kind": "relation",
        "candidate_id": candidate["candidate_id"],
        "candidate_fingerprint": fingerprint,
        "candidate_projection": {},
        "action": action,
        "actor": {"name": "qa", "role": "qa_lead"},
        "rationale": "verified exact foreign key",
        "decided_at_utc": "2026-07-31T00:00:00Z",
        "audit_receipt_id": "audit:relation",
    }


def _asset(candidate: dict) -> dict:
    return {
        "project_id": "relation_authority",
        "database_relation_observer_candidates": [candidate],
        "coverage_gaps": [],
    }


def test_no_relation_decision_keeps_candidate_pending() -> None:
    result = apply_database_relation_authority_decisions(
        _asset(_candidate()),
        ledger={"decisions": []},
    )

    row = result["database_relation_observer_candidates"][0]
    assert row["status"] == "PENDING_RELATION_AUTHORITY"
    assert row["relation_authority"]["status"] == "UNRESOLVED"
    assert row["observer_authority_allowed"] is False
    assert result["database_relation_authority_receipt"]["automatic_approval_count"] == 0


def test_current_exact_approval_grants_read_only_relation_observation_only() -> None:
    candidate = _candidate()
    result = apply_database_relation_authority_decisions(
        _asset(candidate),
        ledger={"decisions": [_decision(candidate, ACTION_APPROVE_READ_ONLY_OBSERVER)]},
    )

    row = result["database_relation_observer_candidates"][0]
    assert row["status"] == "APPROVED_READ_ONLY_RELATION_OBSERVER"
    assert row["observer_authority_allowed"] is True
    assert row["relation_mapping_confirmed"] is True
    assert row["write_target_allowed"] is False
    assert row["oracle_authority_allowed"] is False
    authority = row["relation_authority"]
    assert authority["status"] == "APPROVED"
    assert authority["write_target_authority_granted"] is False
    assert authority["oracle_authority_granted"] is False
    assert authority["business_mapping_authority_granted"] is False


def test_rejected_relation_never_compiles_as_observer() -> None:
    candidate = _candidate()
    result = apply_database_relation_authority_decisions(
        _asset(candidate),
        ledger={"decisions": [_decision(candidate, ACTION_REJECT_MAPPING)]},
    )

    row = result["database_relation_observer_candidates"][0]
    assert row["status"] == "REJECTED_BY_OPERATOR"
    assert row["observer_authority_allowed"] is False
    assert row["relation_mapping_confirmed"] is False


def test_fk_or_root_identity_drift_invalidates_old_approval() -> None:
    original = _candidate()
    decision = _decision(original, ACTION_APPROVE_READ_ONLY_OBSERVER)

    changed_fk = deepcopy(original)
    changed_fk["child_columns"] = ["parent_order_id"]
    changed_fk["predicate_pairs"][0]["child_database_field_name"] = "parent_order_id"
    result = apply_database_relation_authority_decisions(
        _asset(changed_fk),
        ledger={"decisions": [decision]},
    )
    row = result["database_relation_observer_candidates"][0]
    assert row["status"] == "PENDING_RELATION_AUTHORITY"
    assert row["relation_authority"]["status"] == "STALE_DECISION"
    assert row["relation_authority"]["candidate_drift_detected"] is True

    changed_identity = deepcopy(original)
    changed_identity["root_selected_identity_key"] = ["tenant_id", "id"]
    identity_result = apply_database_relation_authority_decisions(
        _asset(changed_identity),
        ledger={"decisions": [decision]},
    )
    identity_row = identity_result["database_relation_observer_candidates"][0]
    assert identity_row["relation_authority"]["status"] == "STALE_DECISION"
    assert identity_row["observer_authority_allowed"] is False


def test_unrelated_table_field_decisions_in_shared_ledger_are_ignored() -> None:
    candidate = _candidate()
    unrelated = {
        "schema": DATABASE_MAPPING_DECISION_SCHEMA,
        "decision_id": "decision:table",
        "candidate_kind": "table",
        "candidate_id": "table-candidate:orders",
        "candidate_fingerprint": "x",
        "action": ACTION_APPROVE_READ_ONLY_OBSERVER,
    }
    result = apply_database_relation_authority_decisions(
        _asset(candidate),
        ledger={"decisions": [unrelated]},
    )

    row = result["database_relation_observer_candidates"][0]
    assert row["relation_authority"]["status"] == "UNRESOLVED"
    assert result["database_relation_authority_receipt"]["decision_count"] == 0
