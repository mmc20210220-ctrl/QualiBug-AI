from __future__ import annotations


def _sealed_row(*, actor: str = "actor-owner") -> dict:
    from ai_test_asset_center.binding_materialization_identity_receipt import (
        build_binding_materialization_identity_receipt,
    )

    row = {
        "target": "order_id",
        "status": "BOUND",
        "value_fingerprint": "abc123",
        "source_priority": "same_actor_list_read",
        "resolver_operation_ref": "op-list-orders",
        "resolver_path": "/api/orders",
        "status_code": 200,
        "resolver_actor_ref": actor,
    }
    proof = build_binding_materialization_identity_receipt(row)
    return {
        **row,
        "materialization_receipt_id": proof["receipt_id"],
        "materialization_identity_receipt": proof,
    }


def test_authorization_binding_proof_requires_embedded_materialization_receipt() -> None:
    from ai_test_asset_center.authorization_oracle_causality import _binding_proof

    fingerprint, receipt_ids, reasons = _binding_proof(
        {"resource_identity_binding_targets": ["order_id"]},
        [
            {
                "target": "order_id",
                "status": "BOUND",
                "value_fingerprint": "abc123",
                "source_priority": "same_actor_list_read",
                "resolver_operation_ref": "op-list-orders",
                "resolver_path": "/api/orders",
                "status_code": 200,
            }
        ],
    )

    assert fingerprint == ""
    assert receipt_ids == []
    assert any("PROVENANCE_INVALID:order_id" in reason for reason in reasons)


def test_authorization_binding_proof_accepts_sealed_observed_resolver() -> None:
    from ai_test_asset_center.authorization_oracle_causality import _binding_proof

    fingerprint, receipt_ids, reasons = _binding_proof(
        {"resource_identity_binding_targets": ["order_id"]},
        [_sealed_row()],
    )

    assert len(fingerprint) == 64
    assert len(receipt_ids) == 1
    assert receipt_ids[0].startswith("binding_materialization_")
    assert reasons == []


def test_authorization_binding_proof_rejects_unobserved_resolver_status() -> None:
    from ai_test_asset_center.authorization_oracle_causality import _binding_proof

    row = _sealed_row()
    row["status_code"] = 0
    fingerprint, _, reasons = _binding_proof(
        {"resource_identity_binding_targets": ["order_id"]},
        [row],
    )

    assert fingerprint == ""
    assert any("resolver_not_observed_2xx" in reason for reason in reasons)


def test_causal_resource_fingerprint_commits_to_resolver_actor_provenance() -> None:
    from ai_test_asset_center.authorization_oracle_causality import _binding_proof

    contract = {"resource_identity_binding_targets": ["order_id"]}
    first, _, first_reasons = _binding_proof(contract, [_sealed_row(actor="actor-a")])
    second, _, second_reasons = _binding_proof(contract, [_sealed_row(actor="actor-b")])

    assert first_reasons == []
    assert second_reasons == []
    assert first != second
