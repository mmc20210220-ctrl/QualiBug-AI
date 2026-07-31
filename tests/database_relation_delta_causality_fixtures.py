from __future__ import annotations

import hashlib
import json

from ai_test_asset_center.database_relation_delta_causality_integrity import (
    causal_scope_fingerprint,
)
from ai_test_asset_center.database_relation_delta_causality_projection import (
    ASSERTION_KIND,
)
from ai_test_asset_center.database_relation_delta_projection_gate import (
    SEMANTIC_PAIR_SCHEMA,
    semantic_relation_delta_pair_id,
)
from ai_test_asset_center.operation_causality_receipt_integrity import (
    seal_operation_causality_transport_receipt,
)

_RELATION_DECISION_ID = "decision:relation"


def _fingerprint(value: object) -> str:
    material = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def build_spec() -> dict:
    causal = {
        "schema": "qualibug.operation-causal-attribution.v1",
        "status": "BOUND",
        "operation_ref": "api:POST:/ledger",
        "treatment_step_id": "treatment-1",
        "value_source": "request.body.request_id",
        "child_database_field_id": "field:ledger_entries:request_id",
        "child_database_field_name": "request_id",
        "mapping_decision_id": _RELATION_DECISION_ID,
        "source_refs": [
            {"kind": "business_rule", "locator": "BR-LEDGER-REQUEST-ID"}
        ],
        "attribution_mode": "EXACT_REQUEST_CORRELATION",
        "response_generated_identifier_allowed": False,
        "timestamp_window_attribution_allowed": False,
        "automatic_field_mapping": False,
        "automatic_operation_selection": False,
    }
    row = {
        "assertion_id": "assert:causal-balance-ledger",
        "kind": ASSERTION_KIND,
        "source_assertion_kind": "conservation",
        "source_refs": [
            {"kind": "business_rule", "locator": "BR-BALANCE-LEDGER"}
        ],
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
        "root_field_binding_id": "binding:accounts:balance",
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
        "tolerance": "0",
        "comparison_phase_pair": "BEFORE_AFTER",
        "causal_attribution_contract": causal,
        "causal_attribution_required": True,
        "database_relation_delta_binding": {
            "relation_mapping_decision_id": _RELATION_DECISION_ID,
        },
    }
    pair_id = semantic_relation_delta_pair_id(row)
    row["relation_pair_id"] = pair_id
    row["relation_before_draft_id"] = "draft:relation:before"
    row["relation_after_draft_id"] = "draft:relation:after"
    scope = causal_scope_fingerprint(row)
    causal["causal_scope_fingerprint"] = scope
    row["causal_scope_fingerprint"] = scope
    row["database_relation_delta_binding"].update(
        {
            "semantic_pair_schema": SEMANTIC_PAIR_SCHEMA,
            "pair_covers_complete_assertion_semantics": True,
            "relation_pair_id": pair_id,
            "relation_before_draft_id": row["relation_before_draft_id"],
            "relation_after_draft_id": row["relation_after_draft_id"],
            "causal_attribution_schema": causal["schema"],
            "causal_scope_fingerprint": scope,
            "causal_mapping_decision_id": causal["mapping_decision_id"],
            "causal_value_source": causal["value_source"],
            "causal_operation_ref": causal["operation_ref"],
            "causal_scope_exact": True,
            "timestamp_window_attribution_used": False,
        }
    )
    return row


def root_phase(value: str, phase: str, draft_id: str) -> dict:
    return {
        "receipt_id": f"root-{phase.lower()}",
        "campaign_id": "campaign-1",
        "execution_id": "execution-1",
        "observer_id": "approved_database_readback",
        "status": "OBSERVED",
        "reason_code": "",
        "evidence": {
            "approved_database_snapshot": {
                "database_table_ref": "table:accounts",
                "database_table_name": "accounts",
                "identity_key": ["id"],
                "identity_parameter_fingerprints": ["identity-a-1"],
                "match_status": "MATCHED_ONE",
                "row_count": 1,
                "rows": [{"id": "a-1", "balance": value}],
                "row_fingerprint": f"root-{phase}-{value}",
                "oracle_verdict_emitted": False,
            }
        },
        "draft_id": draft_id,
        "observer_contract_ref": "observer:accounts",
        "observation_phase": phase,
        "oracle_verdict_emitted": False,
    }


def relation_phase(
    value: str,
    count: int,
    phase: str,
    draft_id: str,
    *,
    spec: dict,
    causal_value: str = "req-1",
) -> dict:
    causal = spec["causal_attribution_contract"]
    return {
        "receipt_id": f"relation-{phase.lower()}",
        "phase_receipt_id": f"relation-phase-{phase.lower()}",
        "campaign_id": "campaign-1",
        "execution_id": "execution-1",
        "observer_id": "approved_database_relation_aggregate",
        "status": "OBSERVED",
        "reason_code": "",
        "evidence": {
            "approved_database_relation_aggregate_snapshot": {
                "relation_observer_ref": "relation-observer:ledger",
                "root_observer_ref": "observer:accounts",
                "database_relationship_id": "fk:ledger:accounts",
                "parent_table_ref": "table:accounts",
                "child_table_ref": "table:ledger_entries",
                "child_table_name": "ledger_entries",
                "relation_key": spec["relation_key"],
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
                "aggregate_values": {
                    "related_value": value,
                    "related_scope_count": count,
                },
                "aggregate_fingerprint": f"aggregate-{phase}-{value}-{count}",
                "causal_attribution_applied": True,
                "causal_attribution_predicate_count": 1,
                "causal_attribution_scope": {
                    "schema": "qualibug.database-relation-causal-attribution-scope.v1",
                    "causal_scope_fingerprint": spec["causal_scope_fingerprint"],
                    "operation_ref": causal["operation_ref"],
                    "value_source": causal["value_source"],
                    "child_database_field_id": causal[
                        "child_database_field_id"
                    ],
                    "child_database_field_name": causal[
                        "child_database_field_name"
                    ],
                    "mapping_decision_id": causal["mapping_decision_id"],
                    "relation_mapping_decision_id": causal[
                        "mapping_decision_id"
                    ],
                    "relation_authority_match": True,
                    "attribution_mode": "EXACT_REQUEST_CORRELATION",
                    "timestamp_window_attribution_used": False,
                    "response_generated_identifier_used": False,
                    "raw_causal_value_retained": False,
                },
                "causal_attribution_parameter_fingerprints": [
                    _fingerprint(causal_value)
                ],
                "client_side_filter_used": False,
                "raw_rows_retained": False,
                "oracle_verdict_emitted": False,
            }
        },
        "draft_id": draft_id,
        "relation_pair_id": spec["relation_pair_id"],
        "relation_observer_contract_ref": "relation-observer:ledger",
        "root_observer_contract_ref": "observer:accounts",
        "observation_phase": phase,
        "oracle_verdict_emitted": False,
    }


def transport_receipt(
    spec: dict,
    causal_value: str = "req-1",
) -> dict:
    causal = spec["causal_attribution_contract"]
    fp = _fingerprint(causal_value)
    return seal_operation_causality_transport_receipt(
        {
            "schema": "qualibug.operation-causality-transport-receipt.v1",
            "assertion_id": spec["assertion_id"],
            "causal_scope_fingerprint": spec["causal_scope_fingerprint"],
            "operation_ref": causal["operation_ref"],
            "treatment_step_id": causal["treatment_step_id"],
            "value_source": causal["value_source"],
            "preflight_value_fingerprint": fp,
            "transport_value_fingerprint": fp,
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
        }
    )


def build_observations(spec: dict | None = None) -> dict:
    row = spec or build_spec()
    return {
        "approved_database_observer_phase_receipts": [
            root_phase("100", "BEFORE", row["root_before_draft_id"]),
            root_phase("85", "AFTER", row["root_after_draft_id"]),
        ],
        "approved_database_relation_phase_receipts": [
            relation_phase(
                "20",
                1,
                "BEFORE",
                row["relation_before_draft_id"],
                spec=row,
            ),
            relation_phase(
                "30",
                2,
                "AFTER",
                row["relation_after_draft_id"],
                spec=row,
            ),
        ],
        "operation_causality_transport_receipts": [transport_receipt(row)],
    }
