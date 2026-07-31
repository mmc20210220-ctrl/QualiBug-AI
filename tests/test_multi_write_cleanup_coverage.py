from __future__ import annotations

from ai_test_asset_center.cleanup_plan_validator import (
    _validate_multi_write_cleanup_coverage,
)


OPS = {
    "op_create": {"id": "op_create", "method": "POST", "path": "/orders"},
    "op_pay": {"id": "op_pay", "method": "POST", "path": "/orders/{id}/pay"},
    "op_unpay": {
        "id": "op_unpay",
        "method": "POST",
        "path": "/orders/{id}/unpay",
    },
    "op_delete": {
        "id": "op_delete",
        "method": "DELETE",
        "path": "/orders/{id}",
    },
}


def _writes() -> list[dict[str, str]]:
    return [
        {
            "step_id": "create_order",
            "phase": "treatment",
            "phase_ordinal": "1",
            "operation_ref": "op_create",
            "method": "POST",
            "path": "/orders",
        },
        {
            "step_id": "pay_order",
            "phase": "treatment",
            "phase_ordinal": "2",
            "operation_ref": "op_pay",
            "method": "POST",
            "path": "/orders/{id}/pay",
        },
    ]


def test_multi_write_requires_cleanup_for_every_step() -> None:
    result = _validate_multi_write_cleanup_coverage(
        writes=_writes(),
        cleanup_plan=[
            {
                "source_step_id": "pay_order",
                "source_operation_ref": "op_pay",
                "operation_ref": "op_unpay",
                "mode": "compensating_transition",
            }
        ],
        ops=OPS,
    )

    assert result["valid"] is False
    assert result["reason_code"] == "BLOCKED_NON_REVERSIBLE_WRITE"
    assert "missing_cleanup_for_steps:create_order" in result["detail"]


def test_multi_write_cleanup_must_follow_reverse_dependency_order() -> None:
    result = _validate_multi_write_cleanup_coverage(
        writes=_writes(),
        cleanup_plan=[
            {
                "source_step_id": "create_order",
                "source_operation_ref": "op_create",
                "operation_ref": "op_delete",
                "mode": "delete_created_resource",
            },
            {
                "source_step_id": "pay_order",
                "source_operation_ref": "op_pay",
                "operation_ref": "op_unpay",
                "mode": "compensating_transition",
            },
        ],
        ops=OPS,
    )

    assert result["valid"] is False
    assert "cleanup_order_not_reverse_dependency_order" in result["detail"]


def test_reverse_order_metadata_is_not_semantic_compensation() -> None:
    result = _validate_multi_write_cleanup_coverage(
        writes=_writes(),
        cleanup_plan=[
            {
                "source_step_id": "pay_order",
                "source_operation_ref": "op_pay",
                "operation_ref": "op_pay",
                "mode": "reverse_order",
            },
            {
                "source_step_id": "create_order",
                "source_operation_ref": "op_create",
                "operation_ref": "op_delete",
                "mode": "delete_created_resource",
            },
        ],
        ops=OPS,
    )

    assert result["valid"] is False
    assert "formal_reverse_order_is_not_compensation" in result["detail"]


def test_same_write_operation_requires_explicit_restore_semantics() -> None:
    result = _validate_multi_write_cleanup_coverage(
        writes=_writes(),
        cleanup_plan=[
            {
                "source_step_id": "pay_order",
                "source_operation_ref": "op_pay",
                "operation_ref": "op_pay",
            },
            {
                "source_step_id": "create_order",
                "source_operation_ref": "op_create",
                "operation_ref": "op_delete",
                "mode": "delete_created_resource",
            },
        ],
        ops=OPS,
    )

    assert result["valid"] is False
    assert "source_write_reused_without_semantic_restore" in result["detail"]


def test_explicit_scoped_reverse_compensation_passes_coverage_gate() -> None:
    result = _validate_multi_write_cleanup_coverage(
        writes=_writes(),
        cleanup_plan=[
            {
                "source_step_id": "pay_order",
                "source_operation_ref": "op_pay",
                "operation_ref": "op_unpay",
                "mode": "compensating_transition",
            },
            {
                "source_step_id": "create_order",
                "source_operation_ref": "op_create",
                "operation_ref": "op_delete",
                "mode": "delete_created_resource",
            },
        ],
        ops=OPS,
    )

    assert result["valid"] is True
    assert result["cleanup_source_step_ids"] == [
        "pay_order",
        "create_order",
    ]


def test_multi_write_without_formal_step_ids_fails_closed() -> None:
    writes = _writes()
    writes[1] = {**writes[1], "step_id": ""}

    result = _validate_multi_write_cleanup_coverage(
        writes=writes,
        cleanup_plan=[],
        ops=OPS,
    )

    assert result["valid"] is False
    assert result["detail"] == "multi_write_step_identity_missing"
