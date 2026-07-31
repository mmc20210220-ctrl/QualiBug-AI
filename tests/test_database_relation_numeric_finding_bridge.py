from __future__ import annotations

import json

from ai_test_asset_center.database_relation_numeric_experiment_projection import ASSERTION_KIND
from ai_test_asset_center.database_relation_numeric_finding_bridge import (
    build_database_relation_finding_evidence,
    enrich_database_relation_finding,
)


def _assertion_receipt() -> dict:
    relation_key = [
        {
            "child_database_field_name": "order_id",
            "parent_database_field_name": "id",
        }
    ]
    aggregate_requests = [
        {
            "aggregate": "SUM",
            "database_field_id": "field:order_lines:amount",
            "database_field_name": "amount",
            "alias": "related_value",
        }
    ]
    return {
        "assertion_id": "assert:order-total",
        "kind": ASSERTION_KIND,
        "status": "VIOLATION",
        "reason_code": "DATABASE_RELATION_CONSERVATION_VIOLATED",
        "expected": {
            "database_relation_observer_ref": "relation-observer:order-lines",
            "database_relation_draft_id": "draft:relation:after",
            "database_relationship_id": "fk:order_lines:orders",
            "relation_key": relation_key,
            "root_observer_contract_ref": "observer:orders",
            "root_database_draft_id": "draft:orders:after",
            "root_table_ref": "table:orders",
            "root_database_field_id": "field:orders:total",
            "root_database_field_name": "total",
            "child_table_ref": "table:order_lines",
            "child_database_field_id": "field:order_lines:amount",
            "child_database_field_name": "amount",
            "aggregate": "SUM",
            "aggregate_alias": "related_value",
            "comparison_operator": "EQ",
            "aggregate_on_left": False,
        },
        "actual": {
            "root_value": "30",
            "aggregate_value": "25",
            "left_value": "30",
            "right_value": "25",
            "difference": "5",
            "tolerance": "0",
            "lineage_match": True,
            "identity_match": True,
            "relation_key_match": True,
            "aggregate_request_match": True,
            "observer_performed_oracle_verdict": False,
            "root_snapshot": {
                "draft_id": "draft:orders:after",
                "phase": "AFTER",
                "phase_receipt_id": "root-receipt",
                "source_observer_id": "approved_database_readback",
                "campaign_id": "campaign-1",
                "execution_id": "execution-1",
                "database_table_ref": "table:orders",
                "database_table_name": "orders",
                "match_status": "MATCHED_ONE",
                "row_count": 1,
                "row_fingerprint": "root-row-fingerprint",
                "identity_key": ["id"],
                "identity_parameter_fingerprints": ["identity-o-1"],
                "field_name": "total",
                "field_value": "30",
                "rows": [{"id": "o-1", "total": "30"}],
                "raw_sql": "SELECT secret",
            },
            "relation_snapshot": {
                "draft_id": "draft:relation:after",
                "phase_receipt_id": "relation-receipt",
                "source_observer_id": "approved_database_relation_aggregate",
                "campaign_id": "campaign-1",
                "execution_id": "execution-1",
                "relation_observer_ref": "relation-observer:order-lines",
                "root_observer_ref": "observer:orders",
                "database_relationship_id": "fk:order_lines:orders",
                "parent_table_ref": "table:orders",
                "child_table_ref": "table:order_lines",
                "child_table_name": "order_lines",
                "relation_key": relation_key,
                "relation_parameter_fingerprints": ["identity-o-1"],
                "aggregate_requests": aggregate_requests,
                "aggregate_alias": "related_value",
                "aggregate_value": "25",
                "aggregate_fingerprint": "aggregate-25",
                "client_side_filter_used": False,
                "raw_rows_retained": False,
                "payload_oracle_verdict_emitted": False,
                "rows": [
                    {"order_id": "o-1", "amount": "10", "password": "secret"}
                ],
                "predicate_values": ["o-1"],
                "dsn": "sqlite:///secret",
            },
        },
    }


def test_build_relation_finding_evidence_is_exact_and_secret_free() -> None:
    evidence = build_database_relation_finding_evidence(_assertion_receipt())

    assert evidence["database_relation_observer_ref"] == (
        "relation-observer:order-lines"
    )
    assert evidence["database_relationship_id"] == "fk:order_lines:orders"
    assert evidence["root_database_draft_id"] == "draft:orders:after"
    assert evidence["relation_key"] == [
        {
            "child_database_field_name": "order_id",
            "parent_database_field_name": "id",
        }
    ]
    assert evidence["root_table_ref"] == "table:orders"
    assert evidence["child_table_ref"] == "table:order_lines"
    assert evidence["aggregate"] == "SUM"
    assert evidence["aggregate_alias"] == "related_value"
    assert evidence["relation_key_match"] is True
    assert evidence["aggregate_request_match"] is True
    assert evidence["root_value"] == "30"
    assert evidence["aggregate_value"] == "25"
    assert evidence["difference"] == "5"
    assert evidence["root_snapshot"]["phase_receipt_id"] == "root-receipt"
    relation = evidence["relation_snapshot"]
    assert relation["phase_receipt_id"] == "relation-receipt"
    assert relation["database_relationship_id"] == "fk:order_lines:orders"
    assert relation["aggregate_requests"] == [
        {
            "aggregate": "SUM",
            "database_field_id": "field:order_lines:amount",
            "database_field_name": "amount",
            "alias": "related_value",
        }
    ]
    assert evidence["observer_performed_oracle_verdict"] is False
    assert evidence["oracle_authority"] == "ContractOracle"
    assert evidence["database_observer_authority"] == "FACT_ONLY"
    assert evidence["raw_child_rows_retained"] is False
    serialized = json.dumps(evidence)
    assert "SELECT secret" not in serialized
    assert "sqlite:///secret" not in serialized
    assert '"o-1"' not in serialized
    assert '"password"' not in serialized
    assert '"rows"' not in serialized


def test_enrichment_replaces_legacy_http_db_snapshot_without_upgrading_delivery() -> None:
    assertion = _assertion_receipt()
    result = {
        "finding": {
            "finding_id": "finding:order-total",
            "status": "candidate",
            "gate_passed": False,
            "customer_delivery_status": "candidate",
            "final_review_status": "PENDING_DELIVERY_GATE",
            "evidence": {"assertion": assertion, "api_response": {"status": 200}},
            "raw_evidence": {
                "db_snapshot": {"legacy_http_body": {"total": "30"}},
                "http_response": {"status": 200},
            },
            "failed_assertions": [assertion],
        }
    }

    enriched = enrich_database_relation_finding(result)
    finding = enriched["finding"]

    assert finding["category"] == ASSERTION_KIND
    assert finding["evidence"]["api_response"] == {"status": 200}
    assert finding["evidence"]["database_evidence_basis"] == (
        "APPROVED_ROOT_AND_FK_AGGREGATE_PHASE_RECEIPTS"
    )
    assert finding["raw_evidence"]["legacy_http_body_used_as_db_snapshot"] is False
    snapshot = finding["raw_evidence"]["db_snapshot"]
    assert snapshot["database_relationship_id"] == "fk:order_lines:orders"
    assert snapshot["scope_match"] == {
        "relation_key_match": True,
        "aggregate_request_match": True,
    }
    assert snapshot["actual"]["difference"] == "5"
    assert finding["gate_passed"] is False
    assert finding["customer_delivery_status"] == "candidate"
    assert finding["final_review_status"] == "PENDING_DELIVERY_GATE"
