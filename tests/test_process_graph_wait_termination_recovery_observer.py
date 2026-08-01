from __future__ import annotations

from ai_test_asset_center.process_graph_async_transition_observer import (
    EVIDENCE_KEY,
    observe_async_transitions,
)
from ai_test_asset_center.process_graph_event_transition import (
    RECEIPT_SCHEMA_VERSION,
)


def test_observer_projects_persisted_termination_recovery_authority() -> None:
    source_step = "submit_order"
    target_step = "consume_notification"
    contract_fingerprint = "event-contract-fingerprint"
    receipt_id = "cleanup-receipt-id"
    envelope = {
        "experiment": {
            "execution_graph": {
                "wait_contracts": [
                    {
                        "wait_id": "wait_order_created",
                        "transition_kind": "event_delivery",
                        "source_node_id": source_step,
                        "target_node_id": target_step,
                        "event_transition_contract": {
                            "wait_id": "wait_order_created",
                            "source_node_id": source_step,
                            "target_node_id": target_step,
                            "contract_fingerprint": contract_fingerprint,
                        },
                    }
                ]
            }
        },
        "observations": {
            "process_graph_async_transition_receipts": [
                {
                    "schema_version": RECEIPT_SCHEMA_VERSION,
                    "status": "BLOCKED",
                    "semantic_status": "INDETERMINATE",
                    "reason_code": (
                        "PROCESS_GRAPH_WAIT_TERMINATION_EPOCH_ACTIVE"
                    ),
                    "semantic_reason_codes": [
                        "PROCESS_GRAPH_WAIT_TERMINATION_EPOCH_ACTIVE"
                    ],
                    "step_id": target_step,
                    "source_node_id": source_step,
                    "target_node_id": target_step,
                    "contract_fingerprint": contract_fingerprint,
                    "receipt_id": "event-wait-terminated",
                    "coverage_complete": False,
                    "observation_window_completed": False,
                    "termination_epoch_authority": (
                        "process_step_ledger_cleanup_receipts"
                    ),
                    "termination_epoch_contract_fingerprint": (
                        "termination-epoch-fingerprint"
                    ),
                    "termination_cleanup_receipt_ids": [receipt_id],
                    "termination_recovery_schema_version": (
                        "qualibug.process-graph-wait-termination-recovery.v1"
                    ),
                    "termination_recovery_ledger_id": "psl-recovered",
                    "termination_recovery_ledger_hash": "ledger-hash",
                    "termination_recovery_source_step_fact_hash": (
                        "source-step-fact-hash"
                    ),
                }
            ]
        },
    }

    observed = observe_async_transitions(envelope)
    transitions = observed["evidence"][EVIDENCE_KEY]["transitions"]

    assert len(transitions) == 1
    transition = transitions[0]
    assert transition["termination_epoch_authority"] == (
        "process_step_ledger_cleanup_receipts"
    )
    assert transition["termination_cleanup_receipt_ids"] == [receipt_id]
    assert transition["termination_recovery_schema_version"] == (
        "qualibug.process-graph-wait-termination-recovery.v1"
    )
    assert transition["termination_recovery_ledger_id"] == "psl-recovered"
    assert transition["termination_recovery_ledger_hash"] == "ledger-hash"
    assert transition["termination_recovery_source_step_fact_hash"] == (
        "source-step-fact-hash"
    )
