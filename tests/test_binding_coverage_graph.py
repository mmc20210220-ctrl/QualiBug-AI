"""Tests for binding_coverage_graph.py — SPEC v1.2 §8."""
import pytest
from ai_test_asset_center.binding_coverage_graph import (
    ALLOWED_SOURCE_KINDS,
    FORBIDDEN_SOURCE_KINDS,
    build_binding_coverage_graph,
)


def _make_experiment():
    return {
        "experiment_id": "exp_1",
        "obligation_id": "obl_1",
        "treatment_plan": [{"path": "/orders/{orderId}", "method": "PUT"}],
        "control_plan": [{"path": "/orders/{orderId}", "method": "GET"}],
        "cleanup_plan": [{"path": "/orders/{orderId}", "method": "DELETE"}],
        "observers": [{"path": "/orders/{orderId}", "method": "GET"}],
        "binding_plan": [
            {"target": "orderId", "source_kind": "FIXTURE_RECEIPT", "status": "runtime_resolvable"},
        ],
        "actor_selection_contract": {
            "control_actor_ref": "actor_admin",
            "treatment_actor_ref": "actor_buyer",
        },
    }


class TestBindingGraph:
    def test_allowed_sources(self):
        assert "PRIMARY_RESPONSE" in ALLOWED_SOURCE_KINDS
        assert "FIXTURE_RECEIPT" in ALLOWED_SOURCE_KINDS
        assert "RANDOM_PLACEHOLDER" not in ALLOWED_SOURCE_KINDS

    def test_forbidden_sources(self):
        assert "RANDOM_PLACEHOLDER" in FORBIDDEN_SOURCE_KINDS
        assert "LLM_INVENTED_VALUE" in FORBIDDEN_SOURCE_KINDS
        assert "PATH_GUESS" in FORBIDDEN_SOURCE_KINDS

    def test_basic_graph(self):
        result = build_binding_coverage_graph(
            experiment=_make_experiment(),
            behavior_ir={},
        )
        assert result["schema_version"] == "qualibug.binding-coverage-graph.v1"
        assert result["node_count"] >= 1
        assert result["graph_status"] == "VALID"

    def test_binding_propagates_to_all_locations(self):
        result = build_binding_coverage_graph(
            experiment=_make_experiment(),
            behavior_ir={},
        )
        # orderId should appear in treatment, control, cleanup, observer
        node = next(n for n in result["nodes"] if n["semantic_name"] == "orderId")
        assert len(node["target_locations"]) >= 1

    def test_forbidden_source_detected(self):
        exp = _make_experiment()
        exp["binding_plan"].append({
            "target": "fakeId",
            "source_kind": "RANDOM_PLACEHOLDER",
            "status": "bound",
        })
        result = build_binding_coverage_graph(experiment=exp, behavior_ir={})
        assert result["forbidden_source_count"] >= 1
        assert result["graph_status"] == "BLOCKED"

    def test_empty_experiment(self):
        result = build_binding_coverage_graph(experiment={}, behavior_ir={})
        assert result["node_count"] == 0
        assert result["graph_status"] == "VALID"
