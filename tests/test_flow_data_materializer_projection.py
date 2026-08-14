from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.flow_data_materializer_projection import (
    project_flow_data_materializer_dag,
)


def _experiment() -> dict:
    return {
        "flow_data_requirement": {
            "status": "FROZEN",
            "requirement_id": "flow_data_1",
            "requirement_fingerprint": "fp_1",
            "materialized_before_measurement_targets": ["tenant_id", "id"],
        },
        "binding_plan": [
            {"target": "id"},
            {"target": "tenant_id"},
        ],
        # Historical DAG node with no kind is intentionally not executable by core.
        "fixture_dag": {
            "nodes": [
                {
                    "node_id": "node_legacy_fixture",
                    "fixture_id": "legacy_fixture",
                    "create_operation_ref": "op_create",
                },
                {
                    "node_id": "actor_1",
                    "kind": "actor_context",
                    "actor_ref": "actor_1",
                },
            ],
            "creation_order": ["node_legacy_fixture", "actor_1"],
        },
        # V12 nodes also use fixture_id rather than node_id/kind.
        "fixture_dependency_dag": {
            "nodes": [
                {
                    "fixture_id": "v12_fixture",
                    "operation_ref": "op_create",
                    "produces_bindings": ["id"],
                }
            ],
            "execution_order": ["v12_fixture"],
            "fingerprint": "v12_fp",
        },
    }


def test_projection_builds_core_supported_binding_nodes_in_binding_order() -> None:
    source = _experiment()
    projected, receipt = project_flow_data_materializer_dag(source)

    assert receipt["status"] == "PROJECTED"
    assert receipt["required_targets"] == ["id", "tenant_id"]
    assert projected["fixture_dag"]["setup_order"] == [
        "actor_1",
        "flow_binding:id",
        "flow_binding:tenant_id",
    ]
    nodes = {
        row["node_id"]: row
        for row in projected["fixture_dag"]["nodes"]
    }
    assert nodes["actor_1"]["kind"] == "actor_context"
    assert nodes["flow_binding:id"] == {
        "node_id": "flow_binding:id",
        "kind": "runtime_read_binding",
        "target": "id",
        "authority": "flow_data_requirement",
        "flow_data_requirement_id": "flow_data_1",
    }
    assert nodes["flow_binding:tenant_id"]["target"] == "tenant_id"
    assert "node_legacy_fixture" not in nodes
    assert projected["fixture_dependency_dag"]["execution_order"] == (
        projected["fixture_dag"]["setup_order"]
    )
    assert projected["fixture_dependency_dag"]["nodes"] == (
        projected["fixture_dag"]["nodes"]
    )


def test_projection_never_mutates_compiled_experiment() -> None:
    source = _experiment()
    before = deepcopy(source)

    projected, receipt = project_flow_data_materializer_dag(source)

    assert source == before
    assert projected is not source
    assert receipt["compiled_experiment_mutated"] is False
    assert source["fixture_dependency_dag"]["execution_order"] == [
        "v12_fixture"
    ]


def test_existing_supported_binding_node_is_reused() -> None:
    source = _experiment()
    source["fixture_dag"]["nodes"].append(
        {
            "node_id": "existing_id_binding",
            "kind": "runtime_read_binding",
            "target": "id",
        }
    )
    source["fixture_dag"]["setup_order"] = [
        "actor_1",
        "existing_id_binding",
    ]

    projected, receipt = project_flow_data_materializer_dag(source)

    binding_nodes = [
        row
        for row in projected["fixture_dag"]["nodes"]
        if row.get("kind") == "runtime_read_binding"
        and row.get("target") == "id"
    ]
    assert binding_nodes == [
        {
            "node_id": "existing_id_binding",
            "kind": "runtime_read_binding",
            "target": "id",
        }
    ]
    assert "flow_binding:id" not in receipt["generated_node_ids"]


def test_projection_is_deterministic() -> None:
    once, once_receipt = project_flow_data_materializer_dag(_experiment())
    twice, twice_receipt = project_flow_data_materializer_dag(_experiment())

    assert once == twice
    assert once_receipt == twice_receipt


def test_legacy_experiment_without_requirement_is_not_rewritten() -> None:
    source = {"fixture_dag": {"nodes": [{"node_id": "legacy"}]}}

    projected, receipt = project_flow_data_materializer_dag(source)

    assert projected == source
    assert receipt["status"] == "NOT_APPLICABLE"
    assert receipt["projected_node_count"] == 0


def test_projection_preserves_every_compiler_node_kind() -> None:
    """All compiler-emitted kinds must survive projection.

    Dropping any kind (e.g. disposable_fixture / dependency_create /
    bound_value) made the materializer reconciliation re-synthesize the missing
    node as a stale requirement and the truthfulness gate then blocked the
    experiment as BLOCKED_FIXTURE_DAG_DRIFT. Projection is kind-agnostic: only
    kind-less legacy nodes are dropped.
    """
    compiler_kinds = [
        "actor_context",
        "runtime_read_binding",
        "ownership_fixture_proof",
        "disposable_fixture",
        "dependency_create",
        "bound_value",
        "setup_step",
    ]
    source = {
        "flow_data_requirement": {
            "status": "FROZEN",
            "requirement_id": "flow_data_1",
            "requirement_fingerprint": "fp_1",
            "materialized_before_measurement_targets": ["id"],
        },
        "binding_plan": [{"target": "id"}],
        "fixture_dag": {
            "nodes": [
                {"node_id": f"node_{kind}", "kind": kind}
                for kind in compiler_kinds
            ]
            + [
                # kind-less legacy node must still be dropped
                {"node_id": "node_legacy", "fixture_id": "legacy_fixture"},
            ],
            "setup_order": [
                f"node_{kind}" for kind in compiler_kinds
            ] + ["node_legacy"],
        },
    }

    projected, receipt = project_flow_data_materializer_dag(source)

    projected_nodes = {
        row["node_id"]: row for row in projected["fixture_dag"]["nodes"]
    }
    for kind in compiler_kinds:
        assert f"node_{kind}" in projected_nodes, (
            f"compiler kind {kind!r} was dropped by projection"
        )
    assert "node_legacy" not in projected_nodes
    assert "flow_binding:id" in projected_nodes
    # setup_order must reference every preserved node so activation requirements
    # (computed on the same identities) never see a missing node.
    assert set(projected["fixture_dag"]["setup_order"]) == set(
        projected_nodes
    )
