"""Tests for fixture_dependency_dag.py — SPEC v1.2 §9."""
import pytest
from ai_test_asset_center.fixture_dependency_dag import (
    build_fixture_node,
    validate_fixture_dag,
)


class TestFixtureNode:
    def test_basic_node(self):
        node = build_fixture_node(
            fixture_id="fix_1",
            operation_ref="op_create_user",
            actor_ref="actor_admin",
        )
        assert node["fixture_id"] == "fix_1"
        assert node["status"] == "PLANNED"
        assert node["fingerprint"] != ""

    def test_node_with_dependencies(self):
        node = build_fixture_node(
            fixture_id="fix_2",
            operation_ref="op_create_order",
            depends_on=["fix_1"],
            produces_bindings=["orderId"],
        )
        assert node["depends_on"] == ["fix_1"]
        assert node["produces_bindings"] == ["orderId"]


class TestFixtureDAG:
    def test_valid_dag(self):
        result = validate_fixture_dag(
            fixtures=[
                {"fixture_id": "fix_1", "operation_ref": "op_1", "cleanup_contract_ref": "cleanup_1"},
                {"fixture_id": "fix_2", "operation_ref": "op_2", "depends_on": ["fix_1"], "cleanup_contract_ref": "cleanup_2"},
            ],
            experiment={"experiment_id": "exp_1", "obligation_id": "obl_1"},
            behavior_ir={},
        )
        assert result["dag_status"] == "VALID"
        assert result["node_count"] == 2
        assert len(result["execution_order"]) == 2

    def test_cycle_detected(self):
        result = validate_fixture_dag(
            fixtures=[
                {"fixture_id": "fix_1", "operation_ref": "op_1", "depends_on": ["fix_2"]},
                {"fixture_id": "fix_2", "operation_ref": "op_2", "depends_on": ["fix_1"]},
            ],
            experiment={},
            behavior_ir={},
        )
        assert result["dag_status"] == "BLOCKED"
        assert any(i["kind"] == "CIRCULAR_DEPENDENCY" for i in result["issues"])

    def test_missing_dependency(self):
        result = validate_fixture_dag(
            fixtures=[
                {"fixture_id": "fix_1", "operation_ref": "op_1", "depends_on": ["fix_nonexist"]},
            ],
            experiment={},
            behavior_ir={},
        )
        assert any(i["kind"] == "MISSING_DEPENDENCY" for i in result["issues"])

    def test_missing_cleanup_warns(self):
        result = validate_fixture_dag(
            fixtures=[
                {"fixture_id": "fix_1", "operation_ref": "op_1"},
            ],
            experiment={},
            behavior_ir={},
        )
        assert any(i["kind"] == "MISSING_CLEANUP_RESPONSIBILITY" for i in result["issues"])

    def test_actor_mismatch(self):
        result = validate_fixture_dag(
            fixtures=[
                {"fixture_id": "fix_1", "operation_ref": "op_1", "actor_ref": "actor_wrong", "cleanup_contract_ref": "c"},
            ],
            experiment={
                "actor_selection_contract": {
                    "control_actor_ref": "actor_admin",
                    "treatment_actor_ref": "actor_buyer",
                },
            },
            behavior_ir={},
        )
        assert any(i["kind"] == "ACTOR_MISMATCH" for i in result["issues"])
