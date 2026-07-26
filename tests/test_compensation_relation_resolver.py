"""Tests for compensation_relation_resolver.py — SPEC v1.2 §10."""
import pytest
from ai_test_asset_center.compensation_relation_resolver import (
    resolve_compensation_relation,
    resolve_all_compensation_relations,
)


def _make_ir_with_explicit_relation():
    return {
        "operations": [
            {"id": "op_create", "method": "POST", "path": "/orders", "entity_ref": "order",
             "entity": "order", "identity_fields": ["orderId"]},
            {"id": "op_delete", "method": "DELETE", "path": "/orders/{orderId}", "entity_ref": "order",
             "entity": "order", "identity_fields": ["orderId"]},
        ],
        "relations": [
            {"relation_type": "compensates", "from_ref": "op_delete", "to_ref": "op_create",
             "source_refs": [{"kind": "api_doc", "locator": "DELETE /orders/{orderId}"}]},
        ],
        "actors": [],
    }


def _make_ir_name_only():
    """IR with operations named create/delete but NO explicit relation."""
    return {
        "operations": [
            {"id": "op_create", "method": "POST", "path": "/reservations", "entity_ref": "reservation",
             "entity": "reservation"},
            {"id": "op_cancel", "method": "POST", "path": "/reservations/{id}/cancel", "entity_ref": "reservation",
             "entity": "reservation"},
        ],
        "relations": [],
        "actors": [],
    }


class TestCompensationRelation:
    def test_source_explicit_accepted(self):
        ir = _make_ir_with_explicit_relation()
        result = resolve_compensation_relation(
            primary_operation=ir["operations"][0],
            candidate_operation=ir["operations"][1],
            behavior_ir=ir,
        )
        assert result["accepted"] is True
        assert result["evidence_level"] == "SOURCE_EXPLICIT"
        assert result["entity_match"] is True

    def test_name_antonym_rejected(self):
        """Only name-based create/cancel must NOT be accepted."""
        ir = _make_ir_name_only()
        result = resolve_compensation_relation(
            primary_operation=ir["operations"][0],
            candidate_operation=ir["operations"][1],
            behavior_ir=ir,
        )
        assert result["accepted"] is False
        assert "insufficient_evidence" in result["rejection_reason"]

    def test_entity_mismatch_rejected(self):
        ir = {
            "operations": [
                {"id": "op_create_order", "method": "POST", "path": "/orders", "entity": "order"},
                {"id": "op_delete_user", "method": "DELETE", "path": "/users/{id}", "entity": "user"},
            ],
            "relations": [
                {"relation_type": "compensates", "from_ref": "op_delete_user", "to_ref": "op_create_order"},
            ],
            "actors": [],
        }
        result = resolve_compensation_relation(
            primary_operation=ir["operations"][0],
            candidate_operation=ir["operations"][1],
            behavior_ir=ir,
        )
        assert result["accepted"] is False
        assert result["entity_match"] is False

    def test_schema_version(self):
        ir = _make_ir_with_explicit_relation()
        result = resolve_compensation_relation(
            primary_operation=ir["operations"][0],
            candidate_operation=ir["operations"][1],
            behavior_ir=ir,
        )
        assert result["schema_version"] == "qualibug.relation-evidence.v1"


class TestBatchResolution:
    def test_finds_explicit_relations(self):
        ir = _make_ir_with_explicit_relation()
        result = resolve_all_compensation_relations(behavior_ir=ir)
        assert result["accepted_count"] == 1
        assert result["accepted"][0]["evidence_level"] == "SOURCE_EXPLICIT"

    def test_rejects_name_only(self):
        ir = _make_ir_name_only()
        result = resolve_all_compensation_relations(behavior_ir=ir)
        assert result["accepted_count"] == 0
