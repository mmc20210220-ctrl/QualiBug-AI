from __future__ import annotations


def _requirement(step_id: str, targets: list[str]) -> dict:
    return {
        "requirement_id": "flow-1",
        "requirement_fingerprint": "fp-1",
        "binding_targets": [],
        "step_requirements": [
            {"step_id": step_id, "required_binding_targets": targets}
        ],
    }


def test_duplicate_step_ids_are_execution_identity_ambiguity() -> None:
    from ai_test_asset_center.flow_data_execution_contract import _ambiguity_issues

    issues = _ambiguity_issues(
        {
            "control_plan": [{"step_id": "same"}],
            "treatment_plan": [{"step_id": "same"}],
        },
        _requirement("same", []),
    )

    assert any(row["kind"] == "STEP_IDENTITY_AMBIGUOUS" for row in issues)


def test_same_output_name_with_two_response_paths_is_ambiguous() -> None:
    from ai_test_asset_center.flow_data_execution_contract import _ambiguity_issues

    graph = {"nodes": [{"node_id": "producer"}], "edges": []}
    issues = _ambiguity_issues(
        {
            "treatment_plan": [
                {
                    "step_id": "producer",
                    "_execution_graph": graph,
                    "output_binding_specs": [
                        {"canonical_field_id": "order_id", "source_path": "$.id"},
                        {"canonical_field_id": "order_id", "source_path": "$.order.id"},
                    ],
                }
            ]
        },
        _requirement("producer", []),
    )

    row = next(
        item for item in issues
        if item["kind"] == "OUTPUT_BINDING_SOURCE_AMBIGUOUS"
    )
    assert row["canonical_field_id"] == "order_id"
    assert row["source_paths"] == ["$.id", "$.order.id"]


def test_one_consumer_target_cannot_have_two_producer_identities() -> None:
    from ai_test_asset_center.flow_data_execution_contract import _ambiguity_issues

    graph = {"nodes": [{"node_id": "consumer"}], "edges": []}
    issues = _ambiguity_issues(
        {
            "treatment_plan": [
                {
                    "step_id": "consumer",
                    "_execution_graph": graph,
                    "input_binding_refs": [
                        {
                            "producer_node_id": "a",
                            "producer_output_field": "id",
                            "consumer_target": "order_id",
                        },
                        {
                            "producer_node_id": "b",
                            "producer_output_field": "id",
                            "consumer_target": "order_id",
                        },
                    ],
                }
            ]
        },
        _requirement("consumer", ["order_id"]),
    )

    row = next(
        item for item in issues
        if item["kind"] == "INPUT_BINDING_PRODUCER_AMBIGUOUS"
    )
    assert row["consumer_target"] == "order_id"
    assert [item["producer_node_id"] for item in row["producers"]] == ["a", "b"]


def test_invalid_precondition_identity_output_never_becomes_available() -> None:
    from ai_test_asset_center.flow_data_execution_contract import _ambiguity_issues

    issues = _ambiguity_issues(
        {
            "precondition_plan": [
                {
                    "step_id": "create-order",
                    "identity_output_binding": {
                        "schema_version": "qualibug.identity-output-binding.v1",
                        "status": "FROZEN",
                        "source_identity_field": "id",
                        "source_path": "",
                        "source_authority": "",
                        "consumer_targets": ["order_id"],
                    },
                }
            ]
        },
        _requirement("create-order", []),
    )

    assert any(
        row["kind"] == "IDENTITY_OUTPUT_CONTRACT_INVALID" for row in issues
    )


def test_two_prior_identity_producers_need_explicit_consumer_selection() -> None:
    from ai_test_asset_center.flow_data_execution_contract import _ambiguity_issues

    identity = {
        "schema_version": "qualibug.identity-output-binding.v1",
        "status": "FROZEN",
        "source_identity_field": "id",
        "source_path": "id",
        "source_authority": "behavior_ir.entities.identity_fields",
        "consumer_targets": ["order_id"],
        "alias_targets": ["order_id"],
    }
    experiment = {
        "precondition_plan": [
            {"step_id": "a", "identity_output_binding": dict(identity)},
            {"step_id": "b", "identity_output_binding": dict(identity)},
        ],
        "treatment_plan": [
            {"step_id": "use", "body": {"orderId": "{order_id}"}}
        ],
    }
    requirement = {
        "requirement_id": "flow-1",
        "requirement_fingerprint": "fp-1",
        "binding_targets": [],
        "step_requirements": [
            {"step_id": "a", "required_binding_targets": []},
            {"step_id": "b", "required_binding_targets": []},
            {"step_id": "use", "required_binding_targets": ["order_id"]},
        ],
    }

    issues = _ambiguity_issues(experiment, requirement)

    row = next(
        item for item in issues
        if item["kind"] == "SEQUENTIAL_IDENTITY_PRODUCER_AMBIGUOUS"
    )
    assert row["consumer_target"] == "order_id"
    assert row["producer_step_ids"] == ["a", "b"]
