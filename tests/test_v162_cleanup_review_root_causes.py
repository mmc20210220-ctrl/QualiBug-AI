"""V1.6.2 code-review Critical/High root-cause pins (cleanup / identity / CER)."""
from __future__ import annotations

import inspect

from ai_test_asset_center.cleanup_adapter_ladder import (
    REASON_MUTATION_NOT_ATTESTED,
    execute_declared_adapter_field_restore,
    identity_value_from_body,
    mutation_attested_for_identity,
)
from ai_test_asset_center.cleanup_equivalence import evaluate_cleanup_equivalence
from ai_test_asset_center import experiment_cleanup_executor as cleanup_mod
from ai_test_asset_center.sandbox_write_executor_base import (
    _identity_scoped_entity_observation,
)
from ai_test_asset_center.write_reversibility_contract import (
    _adapter_cleanup_is_field_restore,
    _classify_cleanup_authority_v11,
)


def test_mutation_attestation_requires_same_identity_and_accepted_write() -> None:
    ok, basis = mutation_attested_for_identity(
        identity_value="ord-1",
        identity_column="id",
        mutation_attestation={
            "identity_value": "ord-1",
            "accepted_write": True,
            "write_receipt_ref": "audit-write-1",
            "before_body": {"id": "ord-1", "status": "A"},
            "after_body": {"id": "ord-1", "status": "B"},
            "restore_fields": {"status": "A"},
        },
    )
    assert ok is True
    assert basis == "governed_mutation_attested"

    bad, reason = mutation_attested_for_identity(
        identity_value="ord-1",
        identity_column="id",
        mutation_attestation={
            "identity_value": "ord-other",
            "accepted_write": True,
            "before_body": {"id": "ord-other", "status": "A"},
            "after_body": {"id": "ord-other", "status": "B"},
            "restore_fields": {"status": "A"},
        },
    )
    assert bad is False
    assert reason == "attestation_identity_mismatch"


def test_mutation_attestation_requires_exact_observed_restore_value() -> None:
    ok, reason = mutation_attested_for_identity(
        identity_value="ord-1",
        identity_column="id",
        mutation_attestation={
            "identity_value": "ord-1",
            "accepted_write": True,
            "write_receipt_ref": "audit-write-1",
            "before_body": {"id": "ord-1", "status": "SHIPPED"},
            "after_body": {"id": "ord-1", "status": "COMPLETED"},
            "restore_fields": {"status": "INVENTED"},
        },
    )

    assert ok is False
    assert reason == "attestation_restore_value_mismatch"
    assert identity_value_from_body({"id": {"value": "ord-1"}}, "id") == ""


def test_generic_identity_aliases_are_case_insensitive_but_conflicts_refuse() -> None:
    assert identity_value_from_body({"ID": "ord-1"}, "id") == "ord-1"
    assert identity_value_from_body({"id": "ord-1", "ID": "ord-2"}, "id") == ""


def test_identity_value_accepts_single_response_envelope() -> None:
    assert identity_value_from_body({"data": {"id": "pay-1"}}, "id") == "pay-1"
    assert identity_value_from_body({"result": {"uuid": "pay-2"}}, "id") == "pay-2"
    # Conflicting nested envelopes stay unbound.
    assert (
        identity_value_from_body(
            {"data": {"id": "pay-a"}, "result": {"id": "pay-b"}},
            "id",
        )
        == ""
    )


def test_mutation_attestation_requires_governed_write_receipt_reference() -> None:
    ok, reason = mutation_attested_for_identity(
        identity_value="ord-1",
        identity_column="id",
        mutation_attestation={
            "identity_value": "ord-1",
            "accepted_write": True,
            "before_body": {"id": "ord-1", "status": "SHIPPED"},
            "after_body": {"id": "ord-1", "status": "COMPLETED"},
            "restore_fields": {"status": "SHIPPED"},
        },
    )

    assert ok is False
    assert reason == "attestation_write_receipt_missing"


def test_field_restore_refuses_unattested_update() -> None:
    receipt = execute_declared_adapter_field_restore(
        {"adapter": "db_sql", "table": "orders", "identity_column": "id"},
        identity_value="ord-1",
        restore_fields={"status": "SHIPPED"},
        connect=lambda: (_ for _ in ()).throw(AssertionError("must not connect")),
        policy_decision={"write_allowed": True},
    )
    assert receipt["status"] == "REFUSED"
    assert receipt["reason_code"] == REASON_MUTATION_NOT_ATTESTED


def test_restore_fields_require_positive_primary_identity_match() -> None:
    fields = cleanup_mod._mutation_restore_fields_from_steps(
        [
            {
                "phase": "treatment",
                "governance_receipt": {
                    "accepted": True,
                    "before": {"body": {"status": "SHIPPED"}},  # missing id
                    "after": {"body": {"status": "COMPLETED"}},
                },
            }
        ],
        identity_value="ord-1",
        identity_column="id",
    )
    assert fields == {}


def test_adapter_completed_evidence_emits_restoration_verified() -> None:
    """A CLEANED db_sql step must emit restoration_verified/state_unchanged derived from
    the real adapter result (``bool(_adapter_cleaned)``), never a hardcoded ``True``.

    The earlier ``_scoped_n == 0`` NOT_REQUIRED guard legitimately emits a hardcoded
    ``restoration_verified: True`` (nothing was written, state unchanged); the contract
    here pins the adapter-run completion emission instead.
    """
    source = inspect.getsource(cleanup_mod._core.execute_experiment_cleanup_compensation)
    assert '"restoration_verified": bool(_adapter_cleaned)' in source
    assert '"state_unchanged": bool(_adapter_cleaned)' in source
    assert "audit_receipt_ids" in source
    assert "False if _adapter_cleaned" not in source


def test_short_id_blocks_unobservable_identity_path() -> None:
    assert _identity_scoped_entity_observation(
        "/api/resources/ord-1/ship",
        "/api/resources/ord-1",
    )


def test_cer_false_not_required_does_not_waive_equivalence() -> None:
    receipt = evaluate_cleanup_equivalence(
        proof={
            "proof_id": "wrp_1",
            "equivalence_contract": {"mode": "business_state_restored"},
            "identity_contract": {"identity_fields": ["id"]},
        },
        before_observation={"status_code": 200, "body": {"id": "x", "qty": 1}},
        after_write_observation={"status_code": 200, "body": {"id": "x", "qty": 2}},
        after_cleanup_observation={},
        runtime_bindings={"id": "x"},
        cleanup_execution_receipt={
            "schema_version": "qualibug.cleanup-execution-receipt.v1",
            "status": "NOT_REQUIRED",
            "reason_code": "CLEANUP_NOT_REQUIRED",
        },
    )
    assert receipt["equivalence_status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "CLEANUP_NOT_REQUIRED_UNPROVEN"


def test_empty_body_alone_does_not_force_field_restore() -> None:
    assert (
        _adapter_cleanup_is_field_restore(
            {"mode": "row_delete"},
            primary_method="POST",
            primary_path="/api/orders/:id/confirm",
            primary_op={"id": "op_confirm", "request_example": {}},
            primary_operation_ref="op_confirm",
            relations=[],
        )
        is False
    )


def test_produces_entity_create_under_parent_is_row_delete() -> None:
    result = _classify_cleanup_authority_v11(
        cleanup_plan=[
            {
                "action": "declared_adapter_cleanup",
                "mode": "row_delete",
                "adapter": "db_sql",
                "table": "items",
                "identity_column": "id",
                "requires_ownership_proof": True,
                "scope": "run_created_only",
            }
        ],
        primary_method="POST",
        primary_operation_ref="op_create_child",
        primary_path="/api/parents/:id/items",
        ops={
            "op_create_child": {
                "id": "op_create_child",
                "method": "POST",
                "path": "/api/parents/:id/items",
                "request_example": {},
            }
        },
        relations=[
            {
                "kind": "produces",
                "operation_ref": "op_create_child",
                "to_ref": "entity_item",
            }
        ],
        experiment={},
    )
    assert result["authority_block"]["cleanup_surface"] == "row_delete"


def test_cer_propagates_field_restore_mode_for_equivalence() -> None:
    from ai_test_asset_center.cleanup_execution_receipt import (
        build_cleanup_execution_receipt,
    )

    receipt = build_cleanup_execution_receipt(
        experiment_id="exp_1",
        proof_id="wrp_1",
        cleanup_plan=[
            {
                "adapter": "db_sql",
                "table": "orders",
                "identity_column": "id",
                "mode": "row_delete",
            }
        ],
        steps_out=[
            {
                "phase": "cleanup",
                "method": "ADAPTER_DB_SQL",
                "path": "/api/orders/o-1",
                "status_code": 200,
                "adapter_cleanup_receipt": {
                    "receipt_id": "cleanup_adapter_1",
                    "status": "CLEANED",
                    "mode": "field_restore",
                    "table": "orders",
                    "ownership_basis": "governed_mutation_attested",
                    "rows_updated": 1,
                },
                "governance_receipt": {
                    "accepted": True,
                    "write": {
                        "status": 200,
                        "body": {"mode": "field_restore", "rows_updated": 1},
                    },
                    "after": {
                        "status": 200,
                        "body": {"id": "o-1", "status": "PENDING"},
                    },
                },
            }
        ],
        cleanup_failures=0,
        cleanup_status="cleaned",
        proof={"cleanup_authority": {"mode": "declared_adapter_cleanup"}},
        adapter_cleanup_receipts=[
            {
                "receipt_id": "cleanup_adapter_1",
                "status": "CLEANED",
                "mode": "field_restore",
                "table": "orders",
                "identity_column": "id",
                "identity_value": "o-1",
                "ownership_basis": "governed_mutation_attested",
                "rows_updated": 1,
            }
        ],
    )
    assert receipt["cleanup_mode"] == "field_restore"
    assert receipt["mode"] == "field_restore"


def test_cer_does_not_take_cleanup_authority_from_target_body() -> None:
    from ai_test_asset_center.cleanup_execution_receipt import (
        build_cleanup_execution_receipt,
    )

    receipt = build_cleanup_execution_receipt(
        experiment_id="exp_1",
        proof_id="wrp_1",
        cleanup_plan=[
            {
                "operation_ref": "delete-order",
                "method": "DELETE",
                "path": "/api/orders/{id}",
                "mode": "row_delete",
            }
        ],
        steps_out=[
            {
                "phase": "cleanup",
                "operation_ref": "delete-order",
                "method": "DELETE",
                "path": "/api/orders/o-1",
                "status_code": 200,
                "body": {"mode": "field_restore"},
                "governance_receipt": {
                    "accepted": True,
                    "write": {"status": 200, "body": {"mode": "field_restore"}},
                },
            }
        ],
        cleanup_failures=0,
        cleanup_status="cleaned",
        proof={"cleanup_authority": {"mode": "identity_delete"}},
        adapter_cleanup_receipts=[],
    )

    assert receipt["cleanup_mode"] == "row_delete"


def test_cer_rejects_2xx_cleanup_without_governance_acceptance() -> None:
    from ai_test_asset_center.cleanup_execution_receipt import (
        build_cleanup_execution_receipt,
    )

    receipt = build_cleanup_execution_receipt(
        experiment_id="exp_1",
        proof_id="wrp_1",
        cleanup_plan=[
            {
                "operation_ref": "delete-order",
                "method": "DELETE",
                "path": "/api/orders/{id}",
                "mode": "row_delete",
            }
        ],
        steps_out=[
            {
                "phase": "cleanup",
                "operation_ref": "delete-order",
                "method": "DELETE",
                "path": "/api/orders/o-1",
                "status_code": 204,
                "governance_receipt": {"accepted": False},
            }
        ],
        cleanup_failures=0,
        cleanup_status="cleaned",
        proof={"cleanup_authority": {"mode": "identity_delete"}},
        adapter_cleanup_receipts=[],
    )

    assert receipt["succeeded"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["reason_code"] == "CLEANUP_GOVERNANCE_ACCEPTANCE_MISSING"


def test_cer_rejects_accepted_cleanup_without_governance_audit_receipt() -> None:
    from ai_test_asset_center.cleanup_execution_receipt import (
        build_cleanup_execution_receipt,
    )

    receipt = build_cleanup_execution_receipt(
        experiment_id="exp_1",
        proof_id="wrp_1",
        cleanup_plan=[
            {
                "operation_ref": "delete-order",
                "method": "DELETE",
                "path": "/api/orders/{id}",
                "mode": "row_delete",
            }
        ],
        steps_out=[
            {
                "phase": "cleanup",
                "operation_ref": "delete-order",
                "method": "DELETE",
                "path": "/api/orders/o-1",
                "status_code": 204,
                "governance_receipt": {"accepted": True},
            }
        ],
        cleanup_failures=0,
        cleanup_status="cleaned",
        proof={"cleanup_authority": {"mode": "identity_delete"}},
        adapter_cleanup_receipts=[],
    )

    assert receipt["succeeded"] is False
    assert receipt["status"] == "BLOCKED"
    assert receipt["reason_code"] == "CLEANUP_GOVERNANCE_AUDIT_RECEIPT_MISSING"
    assert receipt["source_receipt_ids"] == []


def test_cer_binds_content_addressed_governance_audit_receipt_id() -> None:
    from ai_test_asset_center.cleanup_execution_receipt import (
        build_cleanup_execution_receipt,
    )
    from ai_test_asset_center.experiment_runtime_support import (
        _governance_audit_receipt_id,
    )

    governance = {
        "accepted": True,
        "audit_path": "platform_workspace/p/defect_discovery/sandbox_write_audit.jsonl",
        "audit_record": {"campaign_id": "c-1", "operation_phase": "cleanup"},
        "before_ref": "before-1",
        "after_ref": "after-1",
    }
    receipt = build_cleanup_execution_receipt(
        experiment_id="exp_1",
        proof_id="wrp_1",
        cleanup_plan=[
            {
                "operation_ref": "delete-order",
                "method": "DELETE",
                "path": "/api/orders/{id}",
                "mode": "row_delete",
            }
        ],
        steps_out=[
            {
                "phase": "cleanup",
                "operation_ref": "delete-order",
                "method": "DELETE",
                "path": "/api/orders/o-1",
                "status_code": 204,
                "governance_receipt": governance,
            }
        ],
        cleanup_failures=0,
        cleanup_status="cleaned",
        proof={"cleanup_authority": {"mode": "identity_delete"}},
        adapter_cleanup_receipts=[],
    )

    assert receipt["succeeded"] is True
    assert receipt["source_receipt_ids"] == [
        _governance_audit_receipt_id(governance)
    ]
