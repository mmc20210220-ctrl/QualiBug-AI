from __future__ import annotations

import json

from ai_test_asset_center.database_relation_delta_experiment_projection import (
    ASSERTION_KIND,
)
from ai_test_asset_center.database_relation_delta_finding_bridge import (
    build_database_relation_delta_finding_evidence,
    enrich_database_relation_delta_finding,
)


def _root_snapshot(phase: str, value: str) -> dict:
    return {
        "draft_id": f"draft:accounts:{phase.lower()}",
        "phase": phase,
        "phase_receipt_id": f"root-{phase.lower()}-receipt",
        "source_observer_id": "approved_database_readback",
        "campaign_id": "campaign-1",
        "execution_id": "execution-1",
        "database_table_ref": "table:accounts",
        "database_table_name": "accounts",
        "match_status": "MATCHED_ONE",
        "row_count": 1,
        "row_fingerprint": f"root-{phase}-{value}",
        "identity_key": ["id"],
        "identity_parameter_fingerprints": ["identity-a-1"],
        "field_name": "balance",
        "field_value": value,
        "rows": [{"id": "a-1", "balance": value, "password": "secret"}],
        "raw_sql": "SELECT secret",
    }


def _relation_snapshot(phase: str, value: str, count: int) -> dict:
    return {
        "draft_id": f"draft:relation:{phase.lower()}",
        "phase": phase,
        "phase_receipt_id": f"relation-{phase.lower()}-receipt",
        "receipt_id": f"relation-{phase.lower()}",
        "source_observer_id": "approved_database_relation_aggregate",
        "campaign_id": "campaign-1",
        "execution_id": "execution-1",
        "relation_observer_ref": "relation-observer:ledger",
        "root_observer_ref": "observer:accounts",
        "database_relationship_id": "fk:ledger:accounts",
        "parent_table_ref": "table:accounts",
        "child_table_ref": "table:ledger_entries",
        "child_table_name": "ledger_entries",
        "relation_key": [
            {
                "child_database_field_name": "account_id",
                "parent_database_field_name": "id",
            }
        ],
        "relation_parameter_fingerprints": ["identity-a-1"],
        "aggregate_requests": [
            {
                "aggregate": "SUM",
                "database_field_id": "field:ledger_entries:amount",
                "database_field_name": "amount",
                "alias": "related_value",
            },
            {
                "aggregate": "COUNT",
                "database_field_id": "",
                "database_field_name": "",
                "alias": "related_scope_count",
            },
        ],
        "aggregate_alias": "related_value",
        "aggregate_value": value,
        "scope_count_alias": "related_scope_count",
        "scope_count": count,
        "aggregate_fingerprint": f"relation-{phase}-{value}",
        "aggregate_request_match": True,
        "client_side_filter_used": False,
        "raw_rows_retained": False,
        "rows": [
            {
                "account_id": "a-1",
                "amount": value,
                "password": "secret",
            }
        ],
        "predicate_values": ["a-1"],
        "dsn": "sqlite:///secret",
    }


def _assertion_receipt() -> dict:
    return {
        "assertion_id": "assert:balance-ledger-delta",
        "kind": ASSERTION_KIND,
        "status": "VIOLATION",
        "reason_code": "DATABASE_RELATION_DELTA_CONSERVATION_VIOLATED",
        "expected": {
            "database_relation_observer_ref": "relation-observer:ledger",
            "database_relationship_id": "fk:ledger:accounts",
            "relation_key": [
                {
                    "child_database_field_name": "account_id",
                    "parent_database_field_name": "id",
                }
            ],
            "relation_pair_id": "pair-1",
            "relation_before_draft_id": "draft:relation:before",
            "relation_after_draft_id": "draft:relation:after",
            "root_observer_contract_ref": "observer:accounts",
            "root_before_draft_id": "draft:accounts:before",
            "root_after_draft_id": "draft:accounts:after",
            "root_table_ref": "table:accounts",
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
        },
        "actual": {
            "root_before": "100",
            "root_after": "85",
            "root_delta": "-15",
            "relation_before": "20",
            "relation_after": "30",
            "relation_delta": "10",
            "left_coefficient": "-1",
            "right_coefficient": "1",
            "weighted_left_delta": "15",
            "weighted_right_delta": "10",
            "difference": "5",
            "tolerance": "0",
            "lineage_match": True,
            "root_identity_match": True,
            "relation_identity_match": True,
            "cross_observer_identity_match": True,
            "relation_scope_match": True,
            "aggregate_request_match": True,
            "observer_performed_oracle_verdict": False,
            "root_before_snapshot": _root_snapshot("BEFORE", "100"),
            "root_after_snapshot": _root_snapshot("AFTER", "85"),
            "relation_before_snapshot": _relation_snapshot(
                "BEFORE",
                "20",
                1,
            ),
            "relation_after_snapshot": _relation_snapshot(
                "AFTER",
                "30",
                2,
            ),
        },
    }


def test_delta_finding_evidence_is_exact_and_secret_free() -> None:
    evidence = build_database_relation_delta_finding_evidence(
        _assertion_receipt()
    )

    assert evidence["database_relationship_id"] == "fk:ledger:accounts"
    assert evidence["root_delta"] == "-15"
    assert evidence["relation_delta"] == "10"
    assert evidence["difference"] == "5"
    assert evidence["aggregate_request_match"] is True
    assert evidence["relation_scope_match"] is True
    assert evidence["root_before_snapshot"]["phase_receipt_id"] == (
        "root-before-receipt"
    )
    assert evidence["relation_after_snapshot"]["phase_receipt_id"] == (
        "relation-after-receipt"
    )
    assert evidence["observer_performed_oracle_verdict"] is False
    assert evidence["oracle_authority"] == "ContractOracle"
    assert evidence["database_observer_authority"] == "FACT_ONLY"
    serialized = json.dumps(evidence)
    assert "SELECT secret" not in serialized
    assert "sqlite:///secret" not in serialized
    assert '"a-1"' not in serialized
    assert '"password"' not in serialized
    assert '"rows"' not in serialized
    assert '"predicate_values"' not in serialized


def test_enrichment_replaces_legacy_snapshot_without_upgrading_delivery() -> None:
    assertion = _assertion_receipt()
    result = {
        "finding": {
            "finding_id": "finding:balance-ledger",
            "status": "candidate",
            "gate_passed": False,
            "customer_delivery_status": "candidate",
            "final_review_status": "PENDING_DELIVERY_GATE",
            "evidence": {
                "assertion": assertion,
                "api_response": {"status": 200},
            },
            "raw_evidence": {
                "db_snapshot": {"legacy_http_body": {"balance": "85"}},
                "http_response": {"status": 200},
            },
            "failed_assertions": [assertion],
        }
    }

    enriched = enrich_database_relation_delta_finding(result)
    finding = enriched["finding"]

    assert finding["category"] == ASSERTION_KIND
    assert finding["evidence"]["api_response"] == {"status": 200}
    assert finding["evidence"]["database_evidence_basis"] == (
        "APPROVED_ROOT_AND_FK_AGGREGATE_BEFORE_AFTER_PHASE_RECEIPTS"
    )
    assert finding["raw_evidence"]["legacy_http_body_used_as_db_snapshot"] is False
    assert finding["raw_evidence"]["db_snapshot"]["actual"]["difference"] == "5"
    assert finding["gate_passed"] is False
    assert finding["customer_delivery_status"] == "candidate"
    assert finding["final_review_status"] == "PENDING_DELIVERY_GATE"
