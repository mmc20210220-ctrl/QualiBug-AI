"""Tests for v1.2.1 safety non-regression — SPEC v1.2.1 §14."""
import pytest
from ai_test_asset_center.binding_coverage_graph import (
    build_binding_coverage_graph,
    FORBIDDEN_SOURCE_KINDS,
)
from ai_test_asset_center.observer_capability_resolver import resolve_observer_capability
from ai_test_asset_center.oracle_input_contract import build_oracle_input_contract


class TestV121SafetyNonRegression:
    def test_forbidden_sources_still_blocked(self):
        """Forbidden binding sources must still be blocked."""
        exp = {
            "experiment_id": "exp_1",
            "treatment_plan": [{"path": "/orders/{orderId}", "method": "PUT"}],
            "binding_plan": [{"target": "orderId", "source_kind": "RANDOM_PLACEHOLDER"}],
        }
        result = build_binding_coverage_graph(experiment=exp, behavior_ir={})
        assert result["graph_status"] == "BLOCKED"
        assert result["forbidden_source_count"] >= 1

    def test_all_forbidden_kinds_defined(self):
        """All forbidden source kinds are defined."""
        expected = {"RANDOM_PLACEHOLDER", "PATH_GUESS", "LLM_INVENTED_VALUE", "DEFAULT_FAKE_VALUE", "UNVERIFIED_FALLBACK"}
        assert FORBIDDEN_SOURCE_KINDS == expected

    def test_observer_not_invented(self):
        """Observer resolution must not invent operations."""
        ir = {"operations": [{"id": "op_create", "method": "POST", "path": "/orders"}], "relations": [], "actors": []}
        result = resolve_observer_capability(
            observer_requirement="after_state",
            primary_operation=ir["operations"][0],
            behavior_ir=ir,
        )
        # No GET operations → BLOCKED, not invented
        assert result["resolution_status"] == "BLOCKED"
        assert result["operation_ref"] == ""

    def test_oracle_input_not_waived_for_writes(self):
        """Governed writes must have oracle input validation."""
        exp = {
            "experiment_id": "exp_1",
            "safety_contract": {"governed_write": True},
            "assertions": [{"assertion_id": "a1", "kind": "conservation"}],
            "observers": [],
        }
        result = build_oracle_input_contract(experiment=exp, behavior_ir={})
        assert result["overall_status"] == "INCOMPLETE"

    def test_binding_graph_fingerprint_deterministic(self):
        """Same input produces same fingerprint."""
        exp = {
            "experiment_id": "exp_1",
            "treatment_plan": [{"path": "/orders/{orderId}", "method": "PUT"}],
            "binding_plan": [{"target": "orderId", "source_kind": "FIXTURE_RECEIPT"}],
        }
        r1 = build_binding_coverage_graph(experiment=exp, behavior_ir={})
        r2 = build_binding_coverage_graph(experiment=exp, behavior_ir={})
        assert r1["binding_graph_fingerprint"] == r2["binding_graph_fingerprint"]

    def test_stage_order_invariant(self):
        """Stage order must be fixed and monotonic."""
        from ai_test_asset_center.binding_coverage_graph import STAGE_ORDER, _stage_index
        assert STAGE_ORDER[0] == "COMPILE_STATIC"
        assert STAGE_ORDER[-1] == "AFTER_CLEANUP_OBSERVATION"
        for i in range(len(STAGE_ORDER) - 1):
            assert _stage_index(STAGE_ORDER[i]) < _stage_index(STAGE_ORDER[i + 1])
