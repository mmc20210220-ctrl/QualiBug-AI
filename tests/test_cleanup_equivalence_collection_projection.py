from __future__ import annotations

from copy import deepcopy
from typing import Any

from ai_test_asset_center.cleanup_equivalence import evaluate_cleanup_equivalence


TARGET_ID = "order-1"
TENANT_ID = "tenant-a"


def _proof(*, identity_fields: list[str] | None = None) -> dict[str, Any]:
    return {
        "proof_id": "wrp_collection_identity",
        "identity_contract": {
            "identity_fields": identity_fields or ["id"],
        },
        "equivalence_contract": {
            "mode": "created_entity_absent",
        },
    }


def _cleanup_receipt() -> dict[str, Any]:
    return {
        "schema_version": "qualibug.cleanup-execution-receipt.v1",
        "receipt_id": "cleanup_order_1",
        "attempted": True,
        "transport_reached": True,
        "status": "ACCEPTED",
        "status_code": 204,
        "succeeded": True,
        "cleanup_mode": "row_delete",
    }


def _evaluate(
    *,
    before_rows: list[dict[str, Any]],
    after_write_rows: list[dict[str, Any]],
    after_cleanup_rows: list[dict[str, Any]],
    proof: dict[str, Any] | None = None,
    runtime_bindings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return evaluate_cleanup_equivalence(
        proof=proof or _proof(),
        before_observation={
            "status_code": 200,
            "body": {"items": before_rows},
        },
        after_write_observation={
            "status_code": 200,
            "body": {"items": after_write_rows},
        },
        after_cleanup_observation={
            "status_code": 200,
            "body": {"items": after_cleanup_rows},
        },
        runtime_bindings=runtime_bindings or {"id": TARGET_ID},
        cleanup_execution_receipt=_cleanup_receipt(),
    )


def test_empty_collection_after_cleanup_proves_target_absence() -> None:
    receipt = _evaluate(
        before_rows=[],
        after_write_rows=[{"id": TARGET_ID, "sku": "SKU-1"}],
        after_cleanup_rows=[],
    )
    assert receipt["equivalence_status"] == "EQUIVALENT"
    assert receipt["reason_code"] == ""


def test_collection_with_only_unrelated_rows_proves_target_absence() -> None:
    receipt = _evaluate(
        before_rows=[{"id": "order-existing", "sku": "SKU-X"}],
        after_write_rows=[
            {"id": "order-existing", "sku": "SKU-X"},
            {"id": TARGET_ID, "sku": "SKU-1"},
        ],
        after_cleanup_rows=[{"id": "order-existing", "sku": "SKU-X"}],
    )
    assert receipt["equivalence_status"] == "EQUIVALENT"


def test_collection_still_containing_target_is_not_equivalent() -> None:
    receipt = _evaluate(
        before_rows=[],
        after_write_rows=[{"id": TARGET_ID, "sku": "SKU-1"}],
        after_cleanup_rows=[{"id": TARGET_ID, "sku": "SKU-1"}],
    )
    assert receipt["equivalence_status"] == "NOT_EQUIVALENT"
    assert receipt["reason_code"] == "ENTITY_STILL_PRESENT_AFTER_CLEANUP"


def test_non_comparable_collection_remains_fail_closed() -> None:
    receipt = _evaluate(
        before_rows=[],
        after_write_rows=[{"sku": "SKU-1"}],
        after_cleanup_rows=[{"sku": "SKU-1"}],
    )
    assert receipt["equivalence_status"] == "NOT_EQUIVALENT"
    assert receipt["reason_code"] == "ENTITY_STILL_PRESENT_AFTER_CLEANUP"


def test_missing_composite_runtime_binding_remains_fail_closed() -> None:
    receipt = _evaluate(
        proof=_proof(identity_fields=["tenant_id", "id"]),
        runtime_bindings={"id": TARGET_ID},
        before_rows=[],
        after_write_rows=[{"tenant_id": TENANT_ID, "id": TARGET_ID}],
        after_cleanup_rows=[{"tenant_id": TENANT_ID, "id": TARGET_ID}],
    )
    assert receipt["equivalence_status"] == "NOT_EQUIVALENT"
    assert receipt["reason_code"] == "ENTITY_STILL_PRESENT_AFTER_CLEANUP"


def test_row_missing_composite_identity_field_remains_fail_closed() -> None:
    receipt = _evaluate(
        proof=_proof(identity_fields=["tenant_id", "id"]),
        runtime_bindings={"tenant_id": TENANT_ID, "id": TARGET_ID},
        before_rows=[],
        after_write_rows=[{"tenant_id": TENANT_ID, "id": TARGET_ID}],
        after_cleanup_rows=[{"id": TARGET_ID}],
    )
    assert receipt["equivalence_status"] == "NOT_EQUIVALENT"
    assert receipt["reason_code"] == "ENTITY_STILL_PRESENT_AFTER_CLEANUP"


def test_identity_projection_does_not_mutate_observation_inputs() -> None:
    before = {"status_code": 200, "body": {"items": []}}
    after_write = {
        "status_code": 200,
        "body": {"items": [{"id": TARGET_ID, "sku": "SKU-1"}]},
    }
    after_cleanup = {
        "status_code": 200,
        "body": {"items": [{"id": "order-existing", "sku": "SKU-X"}]},
    }
    snapshots = deepcopy((before, after_write, after_cleanup))

    evaluate_cleanup_equivalence(
        proof=_proof(),
        before_observation=before,
        after_write_observation=after_write,
        after_cleanup_observation=after_cleanup,
        runtime_bindings={"id": TARGET_ID},
        cleanup_execution_receipt=_cleanup_receipt(),
    )

    assert (before, after_write, after_cleanup) == snapshots
