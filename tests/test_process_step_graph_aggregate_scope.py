from __future__ import annotations

from ai_test_asset_center.process_step_execution import ProcessStepLedger
from ai_test_asset_center.process_step_receipt_scope import (
    synchronize_scoped_receipts_from_observations,
)


def _ledger() -> ProcessStepLedger:
    ledger = ProcessStepLedger(
        experiment_id="experiment-1",
        campaign_id="campaign-1",
        run_id="run-1",
        obligation_id="obligation-1",
        protocol_id="protocol-1",
        required_step_ids=["write-a", "write-b"],
    )
    for step_id in ("write-a", "write-b"):
        ledger.record_step_execution(
            step_id=step_id,
            phase="treatment",
            operation_ref=f"operation-{step_id}",
            actor_ref="actor-1",
            request_receipt_id=f"request-{step_id}",
            response_receipt_id=f"response-{step_id}",
            transport_receipt_id=f"transport-{step_id}",
            status_code=200,
            final_status="EXECUTED",
            operation_accepted=True,
        )
    return ledger


def _execution_receipt(step_id: str) -> dict:
    return {
        "schema_version": "qualibug.cleanup-execution-receipt.v1",
        "receipt_id": f"cleanup-execution-{step_id}",
        "source_step_id": step_id,
        "status": "ACCEPTED",
    }


def _verification_receipt(step_id: str) -> dict:
    return {
        "schema_version": "qualibug.cleanup-equivalence-receipt.v1",
        "receipt_id": f"cleanup-verification-{step_id}",
        "source_step_id": step_id,
        "equivalence_status": "EQUIVALENT",
    }


def _graph_cleanup_receipt(step_id: str) -> dict:
    return {
        "schema_version": "qualibug.contract-evidence-receipt.v1",
        "receipt_id": f"graph-cleanup-{step_id}",
        "status": "COMPLETED",
        "evidence": {
            "source_step_id": step_id,
            "request_reached_transport": True,
        },
    }


def _execution_set() -> dict:
    return {
        "schema_version": (
            "qualibug.process-graph-cleanup-execution-set.v1"
        ),
        "receipt_id": "graph-cleanup-execution-set",
        "write_step_ids": ["write-a", "write-b"],
        "step_cleanup_execution_receipts_by_id": {
            "write-a": _execution_receipt("write-a"),
            "write-b": _execution_receipt("write-b"),
        },
        "step_cleanup_verification_receipts_by_id": {
            "write-a": _verification_receipt("write-a"),
            "write-b": _verification_receipt("write-b"),
        },
    }


def _equivalence_set() -> dict:
    return {
        "schema_version": (
            "qualibug.process-graph-cleanup-equivalence-receipt.v1"
        ),
        "receipt_id": "graph-cleanup-equivalence-set",
        "write_step_ids": ["write-a", "write-b"],
        "step_equivalence_receipts_by_id": {
            "write-a": _verification_receipt("write-a"),
            "write-b": _verification_receipt("write-b"),
        },
    }


def test_graph_aggregates_stay_bundle_scoped_while_nested_receipts_bind() -> None:
    ledger = _ledger()
    execution_set = _execution_set()
    equivalence_set = _equivalence_set()
    observations = {
        "cleanup_execution_receipt": execution_set,
        "cleanup_execution_receipts": [execution_set],
        "cleanup_equivalence_receipt": equivalence_set,
        "process_graph_cleanup_receipts": [
            _graph_cleanup_receipt("write-a"),
            _graph_cleanup_receipt("write-b"),
        ],
    }

    audit = synchronize_scoped_receipts_from_observations(
        ledger,
        observations,
    )

    assert audit["complete"] is True
    assert audit["unbound_receipts"] == []
    assert audit["aggregate_cleanup_receipts_excluded_from_step_scope"] is True
    assert audit["aggregate_cleanup_receipt_ids"] == [
        "graph-cleanup-execution-set",
        "graph-cleanup-equivalence-set",
    ]
    assert observations["cleanup_execution_receipt"] is execution_set
    assert observations["cleanup_execution_receipts"] == [execution_set]
    assert observations["cleanup_equivalence_receipt"] is equivalence_set

    assert audit["step_cleanup_execution_receipt_ids"] == [
        "graph-cleanup-write-a",
        "graph-cleanup-write-b",
        "cleanup-execution-write-a",
        "cleanup-execution-write-b",
    ]
    assert audit["step_cleanup_verification_receipt_ids"] == [
        "cleanup-verification-write-a",
        "cleanup-verification-write-b",
    ]
    assert [
        row["receipt_id"]
        for row in observations[
            "process_step_cleanup_execution_receipts"
        ]
    ] == audit["step_cleanup_execution_receipt_ids"]
    assert [
        row["receipt_id"]
        for row in observations[
            "process_step_cleanup_verification_receipts"
        ]
    ] == audit["step_cleanup_verification_receipt_ids"]

    assert ledger.get_step_row("write-a")[
        "scoped_cleanup_receipt_ids"
    ] == [
        "graph-cleanup-write-a",
        "cleanup-execution-write-a",
        "cleanup-verification-write-a",
    ]
    assert ledger.get_step_row("write-b")[
        "scoped_cleanup_receipt_ids"
    ] == [
        "graph-cleanup-write-b",
        "cleanup-execution-write-b",
        "cleanup-verification-write-b",
    ]


def test_ordinary_cleanup_receipt_without_scope_remains_unbound() -> None:
    ledger = _ledger()
    receipt = {
        "schema_version": "qualibug.cleanup-execution-receipt.v1",
        "receipt_id": "ordinary-missing-scope",
    }
    observations = {"cleanup_execution_receipt": receipt}

    audit = synchronize_scoped_receipts_from_observations(
        ledger,
        observations,
    )

    assert audit["complete"] is False
    assert audit["aggregate_cleanup_receipt_ids"] == []
    assert audit["unbound_receipts"] == [
        {
            "receipt_id": "ordinary-missing-scope",
            "status": "STEP_SCOPE_MISSING",
            "step_id": "",
            "declared_step_ids": [],
            "explicit_scalar_step_ids": [],
            "explicit_list_step_ids": [],
            "evidence_kind": "cleanup",
        }
    ]
    assert ledger.get_step_row("write-a")[
        "scoped_cleanup_receipt_ids"
    ] == []
    assert observations["cleanup_execution_receipts"] == [receipt]


def test_nested_receipt_without_exact_scope_is_not_exempted() -> None:
    ledger = _ledger()
    execution_set = _execution_set()
    execution_set["step_cleanup_execution_receipts_by_id"] = {
        "write-a": {
            "schema_version": "qualibug.cleanup-execution-receipt.v1",
            "receipt_id": "nested-missing-scope",
        }
    }
    execution_set["step_cleanup_verification_receipts_by_id"] = {}
    observations = {
        "cleanup_execution_receipt": execution_set,
        "cleanup_execution_receipts": [execution_set],
    }

    audit = synchronize_scoped_receipts_from_observations(
        ledger,
        observations,
    )

    assert audit["complete"] is False
    assert audit["aggregate_cleanup_receipt_ids"] == [
        "graph-cleanup-execution-set"
    ]
    assert audit["unbound_receipts"][0]["receipt_id"] == (
        "nested-missing-scope"
    )
    assert audit["unbound_receipts"][0]["status"] == (
        "STEP_SCOPE_MISSING"
    )
    assert ledger.get_step_row("write-a")[
        "scoped_cleanup_receipt_ids"
    ] == []


def test_reused_nested_receipt_id_is_rejected_before_any_step_binding() -> None:
    ledger = _ledger()
    execution_set = _execution_set()
    execution_set["step_cleanup_execution_receipts_by_id"] = {
        "write-a": {
            "schema_version": "qualibug.cleanup-execution-receipt.v1",
            "receipt_id": "reused-cleanup-receipt",
            "source_step_id": "write-a",
        },
        "write-b": {
            "schema_version": "qualibug.cleanup-execution-receipt.v1",
            "receipt_id": "reused-cleanup-receipt",
            "source_step_id": "write-b",
        },
    }
    execution_set["step_cleanup_verification_receipts_by_id"] = {}
    observations = {
        "cleanup_execution_receipt": execution_set,
        "cleanup_execution_receipts": [execution_set],
    }

    audit = synchronize_scoped_receipts_from_observations(
        ledger,
        observations,
    )

    assert audit["complete"] is False
    assert audit["conflicting_cleanup_receipt_ids"] == [
        "reused-cleanup-receipt"
    ]
    assert audit["cleanup"]["duplicate_receipt_ids"] == [
        "reused-cleanup-receipt"
    ]
    assert audit["unbound_receipts"] == [
        {
            "receipt_id": "reused-cleanup-receipt",
            "status": "RECEIPT_REUSED_ACROSS_STEPS",
            "step_id": "",
            "declared_step_ids": ["write-a", "write-b"],
            "explicit_scalar_step_ids": ["write-a", "write-b"],
            "explicit_list_step_ids": [],
            "evidence_kind": "cleanup",
        }
    ]
    assert observations["process_step_cleanup_execution_receipts"] == []
    assert ledger.get_step_row("write-a")[
        "scoped_cleanup_receipt_ids"
    ] == []
    assert ledger.get_step_row("write-b")[
        "scoped_cleanup_receipt_ids"
    ] == []
