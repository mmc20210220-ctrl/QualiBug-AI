from ai_test_asset_center.enterprise_implementation_authority_projection import (
    _resolve_api_binding,
    _runtime_operation_index,
    project_enterprise_implementation_authority,
)


def _runtime_model():
    return {
        "operations": [
            {
                "id": "bir_op_pay",
                "operation_id": "payOrder",
                "method": "POST",
                "path": "/orders/{id}/pay",
                "source_operation_refs": [
                    "api:POST:/orders/{id}/pay",
                    "payOrder",
                ],
            },
            {
                "id": "bir_op_cancel",
                "operation_id": "cancelOrder",
                "method": "POST",
                "path": "/orders/{id}/cancel",
                "source_operation_refs": [
                    "api:POST:/orders/{id}/cancel",
                    "cancelOrder",
                ],
            },
        ],
        "invariants": [
            {"id": "inv1", "fact_refs": ["fact:1"], "operation_refs": []}
        ],
    }


def _asset(api_bindings, *, binding_status="PARTIAL"):
    return {
        "enterprise_understanding_model": {
            "business_behaviors": [
                {"behavior_id": "b1", "source_refs": ["fact:1"]}
            ],
            "behavior_implementation_bindings": [
                {
                    "behavior_ref": "b1",
                    "status": binding_status,
                    "api_operation_bindings": api_bindings,
                }
            ],
        }
    }


def test_projects_unique_authoritative_interface_id():
    model = _runtime_model()
    project_enterprise_implementation_authority(
        model,
        _asset(
            [
                {
                    "interface_id": "api:POST:/orders/{id}/pay",
                    "status": "BOUND",
                    "authoritative": True,
                }
            ]
        ),
    )
    assert model["invariants"][0]["operation_refs"] == ["bir_op_pay"]


def test_projects_unique_authoritative_transport_identity():
    model = _runtime_model()
    project_enterprise_implementation_authority(
        model,
        _asset(
            [
                {
                    "method": "POST",
                    "path": "/orders/{id}/pay",
                    "status": "BOUND",
                    "authoritative": True,
                }
            ]
        ),
    )
    assert model["invariants"][0]["operation_refs"] == ["bir_op_pay"]


def test_candidate_only_never_promoted():
    model = _runtime_model()
    project_enterprise_implementation_authority(
        model,
        _asset(
            [
                {
                    "interface_id": "api:POST:/orders/{id}/pay",
                    "status": "CANDIDATE_ONLY",
                    "authoritative": False,
                    "derivation": "token_overlap_diagnostic",
                }
            ]
        ),
    )
    assert model["invariants"][0]["operation_refs"] == []


def test_ambiguous_authoritative_bindings_fail_closed():
    model = _runtime_model()
    project_enterprise_implementation_authority(
        model,
        _asset(
            [
                {
                    "interface_id": "api:POST:/orders/{id}/pay",
                    "status": "BOUND",
                    "authoritative": True,
                },
                {
                    "interface_id": "api:POST:/orders/{id}/cancel",
                    "status": "BOUND",
                    "authoritative": True,
                },
            ]
        ),
    )
    assert model["invariants"][0]["operation_refs"] == []
    assert (
        model["enterprise_implementation_authority_receipt"][
            "ambiguous_invariant_count"
        ]
        == 1
    )


def test_existing_conflicting_explicit_ref_is_not_overwritten():
    model = _runtime_model()
    model["invariants"][0]["operation_refs"] = ["bir_op_cancel"]
    project_enterprise_implementation_authority(
        model,
        _asset(
            [
                {
                    "interface_id": "api:POST:/orders/{id}/pay",
                    "status": "BOUND",
                    "authoritative": True,
                }
            ]
        ),
    )
    assert model["invariants"][0]["operation_refs"] == ["bir_op_cancel"]
    assert (
        model["enterprise_implementation_authority_receipt"][
            "conflicting_invariant_count"
        ]
        == 1
    )


def test_no_shared_fact_identity_never_projects():
    model = _runtime_model()
    asset = _asset(
        [
            {
                "interface_id": "api:POST:/orders/{id}/pay",
                "status": "BOUND",
                "authoritative": True,
            }
        ]
    )
    asset["enterprise_understanding_model"]["business_behaviors"][0][
        "source_refs"
    ] = ["fact:other"]
    project_enterprise_implementation_authority(model, asset)
    assert model["invariants"][0]["operation_refs"] == []


def test_exact_transport_narrows_reused_operation_alias():
    model = _runtime_model()
    model["operations"][0]["operation_id"] = "mutateOrder"
    model["operations"][1]["operation_id"] = "mutateOrder"
    aliases, transports = _runtime_operation_index(model)

    resolved = _resolve_api_binding(
        {
            "operation_id": "mutateOrder",
            "method": "POST",
            "path": "/orders/{id}/pay",
            "status": "BOUND",
            "authoritative": True,
            "derivation": "exact_source_identity",
        },
        aliases=aliases,
        transports=transports,
    )

    assert resolved == {"bir_op_pay"}


def test_conflicting_exact_identity_channels_fail_closed():
    model = _runtime_model()
    aliases, transports = _runtime_operation_index(model)

    resolved = _resolve_api_binding(
        {
            "operation_id": "cancelOrder",
            "method": "POST",
            "path": "/orders/{id}/pay",
            "status": "BOUND",
            "authoritative": True,
            "derivation": "exact_source_identity",
        },
        aliases=aliases,
        transports=transports,
    )

    assert resolved == set()
