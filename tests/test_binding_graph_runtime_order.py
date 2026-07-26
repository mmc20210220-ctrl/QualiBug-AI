"""Tests for binding graph runtime order — SPEC v1.2.1 §7."""
import pytest
from ai_test_asset_center.binding_coverage_graph import (
    build_binding_coverage_graph,
    STAGE_ORDER,
    _stage_index,
)


def _make_experiment():
    return {
        "experiment_id": "exp_1",
        "obligation_id": "obl_1",
        "treatment_plan": [{"path": "/orders/{orderId}", "method": "PUT"}],
        "control_plan": [],
        "cleanup_plan": [{"path": "/orders/{orderId}", "method": "DELETE"}],
        "observers": [{"path": "/orders/{orderId}", "method": "GET"}],
        "binding_plan": [{"target": "orderId", "source_kind": "FIXTURE_RECEIPT"}],
    }


class TestBindingGraphRuntimeOrder:
    def test_stage_order_defined(self):
        assert len(STAGE_ORDER) == 8
        assert STAGE_ORDER[0] == "COMPILE_STATIC"
        assert STAGE_ORDER[-1] == "AFTER_CLEANUP_OBSERVATION"

    def test_stage_index_monotonic(self):
        for i in range(len(STAGE_ORDER) - 1):
            assert _stage_index(STAGE_ORDER[i]) < _stage_index(STAGE_ORDER[i + 1])

    def test_edges_present(self):
        result = build_binding_coverage_graph(experiment=_make_experiment(), behavior_ir={})
        assert "edges" in result
        assert "topological_order" in result
        assert "cycle_detected" in result

    def test_no_cycle_in_simple_graph(self):
        result = build_binding_coverage_graph(experiment=_make_experiment(), behavior_ir={})
        assert result["cycle_detected"] is False

    def test_binding_graph_fingerprint(self):
        result = build_binding_coverage_graph(experiment=_make_experiment(), behavior_ir={})
        assert "binding_graph_fingerprint" in result
        assert len(result["binding_graph_fingerprint"]) == 32

    def test_no_default_primary_response_for_pre_primary(self):
        """Bindings consumed before primary must not default to PRIMARY_RESPONSE."""
        exp = {
            "experiment_id": "exp_x",
            "treatment_plan": [{"path": "/orders/{orderId}", "method": "PUT"}],
            "control_plan": [{"path": "/orders/{orderId}", "method": "GET"}],
            "binding_plan": [],
        }
        result = build_binding_coverage_graph(experiment=exp, behavior_ir={})
        # The binding should be FIXTURE_RECEIPT, not PRIMARY_RESPONSE
        node = next((n for n in result["nodes"] if n["semantic_name"] == "orderId"), None)
        assert node is not None
        assert node["source_kind"] == "FIXTURE_RECEIPT"

    def test_stage_violations_are_warnings(self):
        """Stage violations should be WARN severity, not BLOCK."""
        exp = {
            "experiment_id": "exp_x",
            "treatment_plan": [{"path": "/orders/{orderId}", "method": "PUT"}],
            "control_plan": [{"path": "/orders/{orderId}", "method": "GET"}],
            "binding_plan": [{"target": "orderId", "source_kind": "PRIMARY_RESPONSE"}],
        }
        result = build_binding_coverage_graph(experiment=exp, behavior_ir={})
        # Stage violations are warnings
        for issue in result["issues"]:
            if issue["kind"] == "STAGE_ORDER_VIOLATION":
                assert issue["severity"] == "WARN"
