from __future__ import annotations


def test_raw_batch_prebindings_are_removed_from_formal_experiment() -> None:
    from ai_test_asset_center.experiment_executor_governance import (
        _formal_experiment_without_raw_prebindings,
    )

    governed, diagnostic = _formal_experiment_without_raw_prebindings(
        {
            "experiment_id": "exp-1",
            "binding_plan": [
                {
                    "target": "order_id",
                    "status": "runtime_resolvable",
                    "resolver_operations": [
                        {
                            "operation_ref": "list-orders",
                            "method": "GET",
                            "path": "/api/orders",
                        }
                    ],
                }
            ],
            "_pre_resolved_bindings": {
                "order_id": "UNRECEIPTED-ORDER-ID"
            },
        }
    )

    assert "_pre_resolved_bindings" not in governed
    assert governed["binding_plan"][0]["status"] == "runtime_resolvable"
    assert "materialized_value" not in governed["binding_plan"][0]
    assert diagnostic == {
        "schema_version": "qualibug.pre-resolved-binding-diagnostic.v1",
        "present": True,
        "target_count": 1,
        "targets": ["order_id"],
        "formal_binding_authority": False,
        "values_forwarded_to_transport": False,
        "reason": "raw_batch_prebinding_has_no_materialization_receipt",
    }


def test_prebinding_diagnostic_does_not_retain_raw_values() -> None:
    from ai_test_asset_center.experiment_executor_governance import (
        _formal_experiment_without_raw_prebindings,
    )

    _, diagnostic = _formal_experiment_without_raw_prebindings(
        {
            "_pre_resolved_bindings": {
                "order_id": "sensitive-business-id-123"
            }
        }
    )

    assert "sensitive-business-id-123" not in repr(diagnostic)
    assert diagnostic["targets"] == ["order_id"]


def test_no_prebinding_keeps_diagnostic_explicitly_non_authoritative() -> None:
    from ai_test_asset_center.experiment_executor_governance import (
        _formal_experiment_without_raw_prebindings,
    )

    governed, diagnostic = _formal_experiment_without_raw_prebindings(
        {"experiment_id": "exp-2"}
    )

    assert governed == {"experiment_id": "exp-2"}
    assert diagnostic["present"] is False
    assert diagnostic["formal_binding_authority"] is False
