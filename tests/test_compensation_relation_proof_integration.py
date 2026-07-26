"""Tests for compensation relation proof integration — SPEC v1.2.1 §9."""
import pytest
from ai_test_asset_center.compensation_relation_resolver import resolve_compensation_relation


def _make_ir():
    return {
        "operations": [
            {"id": "op_create", "method": "POST", "path": "/orders", "entity_ref": "order"},
            {"id": "op_cancel", "method": "POST", "path": "/orders/{id}/cancel", "entity_ref": "order"},
            {"id": "op_delete", "method": "DELETE", "path": "/orders/{id}", "entity_ref": "order"},
        ],
        "relations": [
            {"kind": "compensates", "source": "op_create", "target": "op_cancel"},
        ],
        "actors": [],
    }


class TestCompensationRelationProofIntegration:
    def test_resolved_with_compensation(self):
        ir = _make_ir()
        result = resolve_compensation_relation(
            primary_operation=ir["operations"][0],
            candidate_operation=ir["operations"][1],
            behavior_ir=ir,
        )
        assert "schema_version" in result
        assert result.get("relation_kind") == "compensates" or result.get("source_operation_ref")

    def test_schema_version(self):
        ir = _make_ir()
        result = resolve_compensation_relation(
            primary_operation=ir["operations"][0],
            candidate_operation=ir["operations"][1],
            behavior_ir=ir,
        )
        assert "schema_version" in result

    def test_no_candidate_returns_result(self):
        ir = _make_ir()
        result = resolve_compensation_relation(
            primary_operation=ir["operations"][0],
            candidate_operation={},
            behavior_ir=ir,
        )
        # Should still return a result structure
        assert "schema_version" in result
