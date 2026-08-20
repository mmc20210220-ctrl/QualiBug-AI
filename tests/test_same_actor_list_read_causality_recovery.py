from __future__ import annotations

from ai_test_asset_center.authorization_oracle_causality import (
    _binding_parent_provenance,
    _recover_same_actor_list_read_materialization,
)


def _experiment() -> dict:
    return {
        "binding_plan": [
            {
                "target": "order_id",
                "status": "bound",
                "source_priority": "same_actor_list_read",
                "materialized_value": "order-42",
            }
        ]
    }


def _result(*, status_code: int = 200, path: str = "/api/orders/order-42") -> dict:
    return {
        "binding_materialization_receipts": [],
        "fixture_receipts": [
            {
                "node_id": "bind:order_id",
                "kind": "runtime_read_binding",
                "status": "resolved",
                "target": "order_id",
                "value": "order-42",
                "value_fingerprint": "order-42-fingerprint",
                "source": "pre_resolved_binding",
                "proof_source": "/api/orders/order-42",
            }
        ],
        "steps": [
            {
                "phase": "binding_identity_proof",
                "step_id": "bind-proof:order_id",
                "actor_ref": "actor_owner",
                "operation_ref": "op_get_order",
                "method": "GET",
                "path": path,
                "status_code": status_code,
            }
        ],
    }


def test_recovers_sealed_materialization_from_real_identity_proof() -> None:
    result = _result()

    _recover_same_actor_list_read_materialization(result, _experiment())

    rows = result["binding_materialization_receipts"]
    assert len(rows) == 1
    row = rows[0]
    assert row["target"] == "order_id"
    assert row["status"] == "bound"
    assert row["source_priority"] == "same_actor_list_read"
    assert row["value_fingerprint"] == "order-42-fingerprint"
    assert row["resolver_path"] == "/api/orders/order-42"
    assert row["resolver_operation_ref"] == "op_get_order"
    assert row["status_code"] == 200
    assert row["resolver_actor_ref"] == "actor_owner"
    assert row["materialization_receipt_id"]
    assert row["materialization_identity_receipt"]

    provenance, problem = _binding_parent_provenance(row)
    assert problem == ""
    assert provenance["target"] == "order_id"
    assert provenance["source_priority"] == "same_actor_list_read"
    assert provenance["resolver_operation_ref"] == "op_get_order"
    assert provenance["status_code"] == 200


def test_does_not_recover_without_successful_identity_transport() -> None:
    result = _result(status_code=404)

    _recover_same_actor_list_read_materialization(result, _experiment())

    assert result["binding_materialization_receipts"] == []


def test_does_not_recover_when_proof_path_disagrees_with_fixture_receipt() -> None:
    result = _result(path="/api/orders/foreign-order")

    _recover_same_actor_list_read_materialization(result, _experiment())

    assert result["binding_materialization_receipts"] == []


def test_existing_bound_target_is_never_recovered_or_overwritten() -> None:
    existing = {
        "target": "order_id",
        "status": "BOUND",
        "source_priority": "observed_reuse_priority",
        "value_fingerprint": "existing-fingerprint",
        "resolver_path": "/api/orders",
    }
    result = _result()
    result["binding_materialization_receipts"] = [existing.copy()]

    _recover_same_actor_list_read_materialization(result, _experiment())

    assert result["binding_materialization_receipts"] == [existing]


def test_ambiguous_successful_proof_steps_fail_closed() -> None:
    result = _result()
    result["steps"].append(
        {
            **result["steps"][0],
            "step_id": "bind-proof:order_id:list1",
        }
    )

    _recover_same_actor_list_read_materialization(result, _experiment())

    assert result["binding_materialization_receipts"] == []
