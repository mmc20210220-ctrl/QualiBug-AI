"""Tests for fixture DAG materializer integration — SPEC v1.2.1 §8."""
import pytest
from ai_test_asset_center.fixture_dependency_dag import validate_fixture_dag


class TestFixtureDagMaterializerIntegration:
    def test_empty_fixtures_valid(self):
        result = validate_fixture_dag(fixtures=[], experiment={}, behavior_ir={})
        assert result["dag_status"] in ("VALID", "NOT_REQUIRED", "COMPLETE", "EMPTY")

    def test_single_fixture_valid(self):
        fixtures = [{"fixture_id": "fx_1", "operation_ref": "op_create", "depends_on": []}]
        result = validate_fixture_dag(fixtures=fixtures, experiment={}, behavior_ir={})
        assert result["dag_status"] in ("VALID", "COMPLETE")

    def test_topological_order_present(self):
        fixtures = [
            {"fixture_id": "fx_1", "operation_ref": "op_create", "depends_on": []},
            {"fixture_id": "fx_2", "operation_ref": "op_create2", "depends_on": ["fx_1"]},
        ]
        result = validate_fixture_dag(fixtures=fixtures, experiment={}, behavior_ir={})
        assert "execution_order" in result

    def test_cycle_detection(self):
        fixtures = [
            {"fixture_id": "fx_1", "operation_ref": "op_1", "depends_on": ["fx_2"]},
            {"fixture_id": "fx_2", "operation_ref": "op_2", "depends_on": ["fx_1"]},
        ]
        result = validate_fixture_dag(fixtures=fixtures, experiment={}, behavior_ir={})
        # Cycle should be detected via issues or dag_status
        has_cycle_issue = any("cycle" in str(i).lower() for i in result.get("issues", []))
        assert has_cycle_issue or result["dag_status"] == "BLOCKED" or result.get("cycle_detected")
