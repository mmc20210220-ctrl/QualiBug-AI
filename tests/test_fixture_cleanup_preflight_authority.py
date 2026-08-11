from __future__ import annotations


def _ops() -> dict[str, dict]:
    return {
        "create-order": {
            "id": "create-order",
            "method": "POST",
            "path": "/api/orders",
        },
        "delete-order": {
            "id": "delete-order",
            "method": "DELETE",
            "path": "/api/orders/{id}",
        },
        "delete-order-alt": {
            "id": "delete-order-alt",
            "method": "DELETE",
            "path": "/api/orders/{orderId}",
        },
    }


def _cleanup(ref: str, path: str) -> dict:
    return {
        "operation_ref": ref,
        "method": "DELETE",
        "path": path,
        "source": "entity_delete_route",
        "compensates_operation_ref": "create-order",
    }


def test_two_compiled_cleanup_routes_are_blocked_pre_transport() -> None:
    from ai_test_asset_center.experiment_fixture_materializer import (
        _cleanup_contract_issues,
    )

    issues = _cleanup_contract_issues(
        {
            "binding_plan": [
                {
                    "fixture_setup": {
                        "operation_ref": "create-order",
                        "cleanup_operations": [
                            _cleanup("delete-order", "/api/orders/{id}"),
                            _cleanup("delete-order-alt", "/api/orders/{orderId}"),
                        ],
                    }
                }
            ]
        },
        _ops(),
    )

    assert issues[0]["kind"] == "FIXTURE_CLEANUP_OPERATION_AMBIGUOUS"
    assert issues[0]["candidate_operation_ids"] == [
        "delete-order",
        "delete-order-alt",
    ]


def test_one_exact_cleanup_operation_has_no_preflight_issue() -> None:
    from ai_test_asset_center.experiment_fixture_materializer import (
        _cleanup_contract_issues,
    )

    assert _cleanup_contract_issues(
        {
            "fixture_setup": {
                "operation_ref": "create-order",
                "cleanup_operations": [
                    _cleanup("delete-order", "/api/orders/{id}")
                ],
            }
        },
        _ops(),
    ) == []


def test_cleanup_method_or_path_drift_is_blocked_before_write() -> None:
    from ai_test_asset_center.experiment_fixture_materializer import (
        _cleanup_contract_issues,
    )

    issues = _cleanup_contract_issues(
        {
            "fixture_setup": {
                "operation_ref": "create-order",
                "cleanup_operations": [
                    {
                        **_cleanup("delete-order", "/api/orders/{wrongId}"),
                        "method": "POST",
                    }
                ],
            }
        },
        _ops(),
    )

    assert any(
        row["kind"] == "FIXTURE_CLEANUP_OPERATION_CONTRACT_DRIFT"
        for row in issues
    )


def test_explicit_accepted_residue_can_have_no_cleanup_operation() -> None:
    from ai_test_asset_center.experiment_fixture_materializer import (
        _cleanup_contract_issues,
    )

    assert _cleanup_contract_issues(
        {
            "fixture_setup": {
                "operation_ref": "create-order",
                "cleanup_operations": [],
                "accepted_residue_allowed": True,
                "cleanup_required": False,
            }
        },
        _ops(),
    ) == []


def test_cleanup_authority_receipt_must_match_selected_row() -> None:
    from ai_test_asset_center.experiment_fixture_materializer import (
        _cleanup_contract_issues,
    )

    selected = _cleanup("delete-order", "/api/orders/{id}")
    issues = _cleanup_contract_issues(
        {
            "fixture_setup": {
                "operation_ref": "create-order",
                "cleanup_operations": [selected],
                "cleanup_operation_authority_receipt": {
                    "status": "RESOLVED",
                    "cleanup_operation": _cleanup(
                        "delete-order-alt", "/api/orders/{orderId}"
                    ),
                },
            }
        },
        _ops(),
    )

    assert any(
        row["kind"] == "FIXTURE_CLEANUP_AUTHORITY_RECEIPT_MISMATCH"
        for row in issues
    )
