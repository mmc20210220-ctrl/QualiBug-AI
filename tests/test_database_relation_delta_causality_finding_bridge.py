from __future__ import annotations

import json

from ai_test_asset_center.database_relation_delta_causality_finding_bridge import (
    build_database_relation_causal_delta_finding_evidence,
    enrich_database_relation_causal_delta_finding,
)
from ai_test_asset_center.database_relation_delta_causality_projection import (
    ASSERTION_KIND,
)

_RELATION_DECISION_ID = "decision:relation"


def _causal_scope(phase: str) -> dict:
    return {
        "phase": phase,
        "draft_id": f"draft:relation:{phase.lower()}",
        "receipt_id": f"relation-{phase.lower()}",
        "phase_receipt_id": f"relation-{phase.lower()}-phase",
        "campaign_id": "campaign-1",
        "execution_id": "execution-1",
        "source_observer_id": "approved_database_relation_aggregate",
        "causal_attribution_applied": True,
        "causal_attribution_scope": {
            "causal_scope_fingerprint": "causal-scope-1",
            "operation_ref": "api:POST:/ledger",
            "value_source": "request.body.request_id",
            "child_database_field_id": "field:ledger_entries:request_id",
            "child_database_field_name": "request_id",
            "mapping_decision_id": _RELATION_DECISION_ID,
            "relation_mapping_decision_id": _RELATION_DECISION_ID,
            "relation_authority_match": True,
            "attribution_mode": "EXACT_REQUEST_CORRELATION",
        },
        "causal_attribution_parameter_fingerprints": ["fp-1"],
    }


def _assertion() -> dict:
    return {
        "assertion_id": "assert:causal-ledger",
        "kind": ASSERTION_KIND,
        "status": "VIOLATION",
        "reason_code": "DATABASE_RELATION_DELTA_CONSERVATION_VIOLATED",
        "expected": {
            "source_refs": [
                {"kind": "business_rule", "locator": "BR-BALANCE-LEDGER"}
            ],
            "root_field_binding_id": "binding:accounts:balance",
            "relation_mapping_decision_id": _RELATION_DECISION_ID,
            "semantic_pair_schema": "qualibug.database-relation-delta-semantic-pair.v1",
            "relation_pair_id": "relation-pair-1",
            "recomputed_relation_pair_id": "relation-pair-1",
            "database_relation_observer_ref": "relation-observer:ledger",
            "database_relationship_id": "fk:ledger:accounts",
            "relation_key": [
                {
                    "child_database_field_name": "account_id",
                    "parent_database_field_name": "id",
                }
            ],
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
            "causal_scope_fingerprint": "causal-scope-1",
            "operation_ref": "api:POST:/ledger",
            "treatment_step_id": "treatment-1",
            "value_source": "request.body.request_id",
            "mapping_decision_id": _RELATION_DECISION_ID,
            "causal_mapping_decision_id": _RELATION_DECISION_ID,
            "bound_causal_mapping_decision_id": _RELATION_DECISION_ID,
            "authority_basis": "APPROVED_DATABASE_RELATION_FIELD_CATALOG",
            "attribution_mode": "EXACT_REQUEST_CORRELATION",
        },
        "actual": {
            "source_evidence_present": True,
            "approved_binding_ids_present": True,
            "binding_match": True,
            "semantic_pair_match": True,
            "lineage_match": True,
            "relation_pair_match": True,
            "root_identity_match": True,
            "relation_identity_match": True,
            "cross_observer_identity_match": True,
            "aggregate_request_match": True,
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
            "relation_authority_match": True,
            "automatic_authority_selection_used": False,
            "causal_scope_semantic_match": True,
            "transport_receipt_integrity_valid": True,
            "transport_scope_match": True,
            "relation_scope_match": True,
            "causal_value_fingerprint_match": True,
            "causal_lineage_match": True,
            "validated_transport_receipt": {
                "schema": "qualibug.operation-causality-transport-receipt.v1",
                "receipt_id": "causal-transport-1",
                "causal_scope_fingerprint": "causal-scope-1",
                "operation_ref": "api:POST:/ledger",
                "treatment_step_id": "treatment-1",
                "value_source": "request.body.request_id",
                "preflight_value_fingerprint": "fp-1",
                "transport_value_fingerprint": "fp-1",
                "source_value_fingerprint_match": True,
                "request_semantics_fingerprint": "semantics-1",
                "transport_receipt_id": "transport-1",
                "status_code": 201,
                "campaign_id": "campaign-1",
                "execution_id": "execution-1",
                "status": "ATTRIBUTED",
                "reason_code": "",
                "transport_reached": True,
                "raw_causal_value_retained": False,
                "timestamp_window_attribution_used": False,
                "raw_value": "req-1",
            },
            "relation_before_causal_scope": _causal_scope("BEFORE"),
            "relation_after_causal_scope": _causal_scope("AFTER"),
            "observer_performed_oracle_verdict": False,
        },
    }


def test_causal_finding_is_exact_and_raw_identifier_free() -> None:
    evidence = build_database_relation_causal_delta_finding_evidence(_assertion())

    assert evidence["causal_scope_fingerprint"] == "causal-scope-1"
    assert evidence["operation_ref"] == "api:POST:/ledger"
    assert evidence["causal_mapping_decision_id"] == _RELATION_DECISION_ID
    assert evidence["bound_causal_mapping_decision_id"] == _RELATION_DECISION_ID
    assert evidence["relation_mapping_decision_id"] == _RELATION_DECISION_ID
    assert evidence["authority_basis"] == (
        "APPROVED_DATABASE_RELATION_FIELD_CATALOG"
    )
    assert evidence["relation_authority_match"] is True
    assert evidence["automatic_authority_selection_used"] is False
    assert evidence["causal_scope_semantic_match"] is True
    assert evidence["transport_receipt_integrity_valid"] is True
    assert evidence["transport_scope_match"] is True
    assert evidence["causal_value_fingerprint_match"] is True
    assert evidence["causal_lineage_match"] is True
    assert evidence["transport_receipt"]["transport_receipt_id"] == "transport-1"
    before_scope = evidence["relation_before_causal_scope"]
    assert before_scope["mapping_decision_id"] == _RELATION_DECISION_ID
    assert before_scope["relation_mapping_decision_id"] == _RELATION_DECISION_ID
    assert before_scope["relation_authority_match"] is True
    assert before_scope["causal_attribution_parameter_fingerprints"] == ["fp-1"]
    assert evidence["oracle_authority"] == "ContractOracle"
    assert evidence["operation_causality_observer_authority"] == "FACT_ONLY"
    serialized = json.dumps(evidence)
    assert "req-1" not in serialized
    assert "raw_value" not in serialized

    # The bridge retains explicit `*_retained: False` flags to declare that raw
    # predicate values and raw child rows were not captured; only the actual raw
    # data keys themselves must be absent, not the retention flags.
    forbidden_exact_keys = {
        "predicate_values",
        "rows",
        "raw_value",
        "value",
        "raw_sql",
        "sql",
        "aggregate_values",
    }

    def _has_forbidden_key(value: object) -> bool:
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).lower() in forbidden_exact_keys:
                    return True
                if _has_forbidden_key(child):
                    return True
        elif isinstance(value, list):
            return any(_has_forbidden_key(child) for child in value)
        return False

    assert not _has_forbidden_key(evidence)


def test_enrichment_does_not_upgrade_delivery_gate() -> None:
    assertion = _assertion()
    result = {
        "finding": {
            "finding_id": "finding:causal-ledger",
            "status": "candidate",
            "gate_passed": False,
            "customer_delivery_status": "candidate",
            "final_review_status": "PENDING_DELIVERY_GATE",
            "evidence": {"assertion": assertion},
            "raw_evidence": {
                "db_snapshot": {"legacy_http_body": {"request_id": "req-1"}}
            },
            "failed_assertions": [assertion],
        }
    }

    enriched = enrich_database_relation_causal_delta_finding(result)
    finding = enriched["finding"]
    assert finding["category"] == ASSERTION_KIND
    assert finding["gate_passed"] is False
    assert finding["customer_delivery_status"] == "candidate"
    assert finding["final_review_status"] == "PENDING_DELIVERY_GATE"
    assert finding["raw_evidence"]["legacy_http_body_used_as_db_snapshot"] is False
    snapshot = finding["raw_evidence"]["db_snapshot"]
    assert snapshot["relation_mapping_decision_id"] == _RELATION_DECISION_ID
    assert snapshot["actual"]["relation_authority_match"] is True
    assert snapshot["actual"]["causal_lineage_match"] is True
    serialized = json.dumps(finding["raw_evidence"])
    assert "req-1" not in serialized
