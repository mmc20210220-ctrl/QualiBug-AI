"""Tests for binding graph all-issue gate — SPEC v1.2.2 §8."""
import pytest
from ai_test_asset_center.binding_coverage_graph import build_binding_coverage_graph, FORBIDDEN_SOURCE_KINDS


class TestBindingGraphAllIssueGate:
    def test_forbidden_source_blocked(self):
        """FORBIDDEN_SOURCE → graph BLOCKED."""
        exp = {"experiment_id": "e1", "treatment_plan": [{"path": "/orders/{id}", "method": "PUT"}],
               "binding_plan": [{"target": "id", "source_kind": "RANDOM_PLACEHOLDER"}]}
        r = build_binding_coverage_graph(experiment=exp, behavior_ir={})
        assert r["graph_status"] == "BLOCKED"
        assert r["forbidden_source_count"] >= 1

    def test_valid_binding_graph(self):
        """Valid binding → graph VALID."""
        exp = {"experiment_id": "e1", "treatment_plan": [{"path": "/orders/{id}", "method": "PUT"}],
               "binding_plan": [{"target": "id", "source_kind": "FIXTURE_RECEIPT"}]}
        r = build_binding_coverage_graph(experiment=exp, behavior_ir={})
        assert r["graph_status"] == "VALID"

    def test_all_forbidden_kinds(self):
        """All forbidden source kinds are defined."""
        assert "RANDOM_PLACEHOLDER" in FORBIDDEN_SOURCE_KINDS
        assert "LLM_INVENTED_VALUE" in FORBIDDEN_SOURCE_KINDS
        assert "PATH_GUESS" in FORBIDDEN_SOURCE_KINDS

    def test_fingerprint_deterministic(self):
        """Same input → same fingerprint."""
        exp = {"experiment_id": "e1", "treatment_plan": [{"path": "/orders/{id}", "method": "PUT"}],
               "binding_plan": [{"target": "id", "source_kind": "FIXTURE_RECEIPT"}]}
        r1 = build_binding_coverage_graph(experiment=exp, behavior_ir={})
        r2 = build_binding_coverage_graph(experiment=exp, behavior_ir={})
        assert r1["binding_graph_fingerprint"] == r2["binding_graph_fingerprint"]
