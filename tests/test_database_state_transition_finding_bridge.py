from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.database_state_transition_finding_bridge import (
    FINDING_EVIDENCE_SCHEMA,
    enrich_database_state_transition_finding,
)
from ai_test_asset_center.database_state_transition_oracle import (
    DATABASE_STATE_TRANSITION_ASSERTION_KIND,
)


def _assertion() -> dict:
    return {
        "schema_version": "qualibug.assertion-receipt.v1",
        "receipt_id": "assert-receipt-1",
        "campaign_id": "campaign-1",
        "execution_id": "execution-1",
        "assertion_id": "assert:orders:status",
        "kind": DATABASE_STATE_TRANSITION_ASSERTION_KIND,
        "status": "VIOLATION",
        "reason_code": "DATABASE_STATE_TRANSITION_NOT_OBSERVED",
        "passed": False,
        "expected": {
            "database_observer_contract_ref": "observer:orders",
            "database_table_ref": "table:orders",
            "database_field_id": "field:orders:status",
            "database_field_name": "status",
            "transition_policy": "MUST_TRANSITION",
            "before": "PENDING",
            "after": "PAID",
        },
        "actual": {
            "database_observer_contract_ref": "observer:orders",
            "database_table_ref": "table:orders",
            "database_field_id": "field:orders:status",
            "database_field_name": "status",
            "lineage_match": True,
            "identity_match": True,
            "observed_before": "PENDING",
            "observed_after": "PENDING",
            "observer_performed_oracle_verdict": False,
            "before_snapshot": {
                "phase": "BEFORE",
                "draft_id": "draft:orders:before",
                "phase_receipt_id": "readback-before",
                "source_observer_id": "approved_database_readback",
                "campaign_id": "campaign-1",
                "execution_id": "execution-1",
                "database_table_ref": "table:orders",
                "database_table_name": "orders",
                "match_status": "MATCHED_ONE",
                "row_count": 1,
                "row_fingerprint": "row-before-fingerprint",
                "identity_key": ["id"],
                "identity_parameter_fingerprints": ["identity-o-1-fingerprint"],
                "field_name": "status",
                "field_value": "PENDING",
                "raw_sql": "SELECT status FROM orders WHERE id='o-1'",
                "dsn": "postgresql://user:password@example/db",
                "predicate_values": ["o-1"],
            },
            "after_snapshot": {
                "phase": "AFTER",
                "draft_id": "draft:orders:after",
                "phase_receipt_id": "readback-after",
                "source_observer_id": "approved_database_readback",
                "campaign_id": "campaign-1",
                "execution_id": "execution-1",
                "database_table_ref": "table:orders",
                "database_table_name": "orders",
                "match_status": "MATCHED_ONE",
                "row_count": 1,
                "row_fingerprint": "row-after-fingerprint",
                "identity_key": ["id"],
                "identity_parameter_fingerprints": ["identity-o-1-fingerprint"],
                "field_name": "status",
                "field_value": "PENDING",
                "secret": "must-not-leak",
            },
        },
        "error": "",
        "observer_receipt_ids": ["aggregate-receipt"],
        "source_refs": [{"kind": "business_rule", "locator": "BR-ORDER-1"}],
        "harness_error": False,
    }


def _result() -> dict:
    assertion = _assertion()
    return {
        "status": "VIOLATION",
        "oracle_verdict": {
            "verdict": "customer_deliverable_defect_candidate",
            "status": "VIOLATION",
        },
        "finding": {
            "severity": "P1",
            "category": DATABASE_STATE_TRANSITION_ASSERTION_KIND,
            "gate_passed": False,
            "customer_delivery_status": "candidate",
            "final_review_status": "PENDING_DELIVERY_GATE",
            "oracle": {
                "oracle_name": "ContractOracle",
                "customer_deliverable": False,
                "customer_deliverable_candidate": True,
            },
            "evidence": {
                "assertion": assertion,
                "response": "HTTP 200",
            },
            "raw_evidence": {
                "response_raw": {
                    "status_code": 200,
                    "body": {"id": "o-1", "status": "PENDING"},
                },
                "db_snapshot": {
                    "before": {"fake": "http-control-body"},
                    "after": {"fake": "http-treatment-body"},
                },
            },
            "evidence_quality": {
                "level": "executed_candidate",
                "can_reproduce": False,
            },
            "failed_assertions": [assertion],
        },
    }


def _flatten_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(str(key).lower())
            keys.update(_flatten_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_flatten_keys(child))
    return keys


def test_replaces_legacy_http_db_snapshot_with_exact_database_evidence() -> None:
    original = _result()
    enriched = enrich_database_state_transition_finding(
        original,
        experiment={"experiment_id": "experiment:orders"},
    )

    assert enriched is not original
    finding = enriched["finding"]
    db = finding["raw_evidence"]["db_snapshot"]
    assert db["schema"] == FINDING_EVIDENCE_SCHEMA
    assert db["database_observer_contract_ref"] == "observer:orders"
    assert db["database_table_ref"] == "table:orders"
    assert db["database_field_id"] == "field:orders:status"
    assert db["database_field_name"] == "status"
    assert db["expected"] == {
        "transition_policy": "MUST_TRANSITION",
        "before": "PENDING",
        "after": "PAID",
    }
    assert db["actual"] == {
        "before": "PENDING",
        "after": "PENDING",
        "lineage_match": True,
        "identity_match": True,
    }
    assert db["before_receipt"]["phase_receipt_id"] == "readback-before"
    assert db["after_receipt"]["phase_receipt_id"] == "readback-after"
    assert finding["raw_evidence"]["legacy_http_body_used_as_db_snapshot"] is False
    assert finding["raw_evidence"]["response_raw"] == original["finding"]["raw_evidence"][
        "response_raw"
    ]
    assert original["finding"]["raw_evidence"]["db_snapshot"]["before"] == {
        "fake": "http-control-body"
    }


def test_database_evidence_is_secret_free_and_preserves_delivery_authority() -> None:
    enriched = enrich_database_state_transition_finding(_result())
    finding = enriched["finding"]
    database_evidence = finding["database_state_transition_evidence"]
    keys = _flatten_keys(database_evidence)

    assert "raw_sql" not in keys
    assert "sql" not in keys
    assert "dsn" not in keys
    assert "password" not in keys
    assert "secret" not in keys
    assert "predicate_values" not in keys
    assert database_evidence["raw_sql_retained"] is False
    assert database_evidence["dsn_retained"] is False
    assert database_evidence["secret_values_retained"] is False
    assert database_evidence["predicate_values_retained"] is False
    assert database_evidence["database_observer_authority"] == "FACT_ONLY"
    assert database_evidence["oracle_authority"] == "ContractOracle"
    assert database_evidence["observer_performed_oracle_verdict"] is False
    assert database_evidence["lineage_match"] is True
    assert finding["gate_passed"] is False
    assert finding["customer_delivery_status"] == "candidate"
    assert finding["final_review_status"] == "PENDING_DELIVERY_GATE"
    assert finding["oracle"]["customer_deliverable"] is False


def test_primary_non_database_failure_cannot_receive_secondary_database_evidence() -> None:
    result = _result()
    primary = {
        "assertion_id": "assert:http",
        "kind": "http_status",
        "status": "VIOLATION",
    }
    result["finding"]["evidence"]["assertion"] = primary
    result["finding"]["failed_assertions"] = [primary, _assertion()]
    baseline = deepcopy(result)

    assert enrich_database_state_transition_finding(result) == baseline


def test_non_database_assertion_result_is_unchanged() -> None:
    result = _result()
    result["finding"]["evidence"]["assertion"] = {
        "assertion_id": "assert:http",
        "kind": "http_status",
        "status": "VIOLATION",
    }
    result["finding"]["failed_assertions"] = [
        result["finding"]["evidence"]["assertion"]
    ]
    baseline = deepcopy(result)

    assert enrich_database_state_transition_finding(result) == baseline
