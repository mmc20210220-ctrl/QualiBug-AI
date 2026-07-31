"""Final runtime binding identities are sealed without exposing bound values."""
from __future__ import annotations

from copy import deepcopy

import pytest

from ai_test_asset_center.binding_materialization_identity_receipt import (
    BindingMaterializationIdentityError,
    binding_identity_proofs_for_targets,
    build_binding_materialization_identity_receipt,
    seal_binding_materialization_receipts,
    validate_binding_materialization_identity_receipt,
)


def _binding(
    target: str = "order_id",
    *,
    value_fingerprint: str = "order-42-fingerprint",
) -> dict:
    return {
        "target": target,
        "status": "BOUND",
        "value_fingerprint": value_fingerprint,
        "resolver_path": "/orders",
        "resolver_operation_ref": "op:list-orders",
        "materialized_value": "order-42",
    }


def test_identity_receipt_is_deterministic_and_redacted() -> None:
    binding = _binding()

    first = build_binding_materialization_identity_receipt(binding)
    second = build_binding_materialization_identity_receipt(binding)

    assert first == second
    assert first["receipt_id"].startswith("binding_materialization_")
    assert first == {
        "receipt_id": first["receipt_id"],
        "target": "order_id",
        "status": "BOUND",
        "value_fingerprint": "order-42-fingerprint",
    }
    assert "order-42" not in repr(first)
    assert validate_binding_materialization_identity_receipt(first) == first


def test_identity_receipt_tamper_is_rejected() -> None:
    receipt = build_binding_materialization_identity_receipt(_binding())
    receipt["value_fingerprint"] = "other-order-fingerprint"

    with pytest.raises(
        BindingMaterializationIdentityError,
        match="binding_materialization_identity_fingerprint_invalid",
    ):
        validate_binding_materialization_identity_receipt(receipt)


def test_sealing_preserves_input_and_attaches_final_identity() -> None:
    result = {"binding_materialization_receipts": [_binding()]}
    snapshot = deepcopy(result)

    sealed = seal_binding_materialization_receipts(result)

    assert result == snapshot
    row = sealed["binding_materialization_receipts"][0]
    proof = row["materialization_identity_receipt"]
    assert row["materialization_receipt_id"] == proof["receipt_id"]
    assert validate_binding_materialization_identity_receipt(proof) == proof


def test_duplicate_bound_target_is_rejected() -> None:
    result = {
        "binding_materialization_receipts": [
            _binding(value_fingerprint="order-42-fingerprint"),
            _binding(value_fingerprint="order-99-fingerprint"),
        ]
    }

    with pytest.raises(
        BindingMaterializationIdentityError,
        match="binding_materialization_target_ambiguous:order_id",
    ):
        seal_binding_materialization_receipts(result)


def test_target_projection_requires_every_declared_identity() -> None:
    sealed = seal_binding_materialization_receipts(
        {
            "binding_materialization_receipts": [
                _binding("order_id"),
                _binding(
                    "customer_id",
                    value_fingerprint="customer-7-fingerprint",
                ),
            ]
        }
    )

    proofs = binding_identity_proofs_for_targets(
        sealed["binding_materialization_receipts"],
        ["order_id"],
    )

    assert len(proofs) == 1
    assert proofs[0]["target"] == "order_id"
    with pytest.raises(
        BindingMaterializationIdentityError,
        match="binding_materialization_identity_missing:warehouse_id",
    ):
        binding_identity_proofs_for_targets(
            sealed["binding_materialization_receipts"],
            ["order_id", "warehouse_id"],
        )
