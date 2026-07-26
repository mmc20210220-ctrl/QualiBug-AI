"""Tests for observer_capability_resolver.py — SPEC v1.2 §7."""
import pytest
from ai_test_asset_center.observer_capability_resolver import (
    resolve_observer_capability,
)


def _make_ir_with_get():
    return {
        "operations": [
            {"id": "op_create", "method": "POST", "path": "/orders", "entity_ref": "order"},
            {"id": "op_read", "method": "GET", "path": "/orders/{orderId}", "entity_ref": "order",
             "identity_fields": ["orderId"], "source_refs": [{"kind": "api_doc"}]},
            {"id": "op_list", "method": "GET", "path": "/orders", "entity_ref": "order"},
        ],
        "relations": [
            {"relation_type": "observes", "from_ref": "op_read", "to_ref": "op_create"},
        ],
        "actors": [],
    }


def _make_ir_no_get():
    return {
        "operations": [
            {"id": "op_create", "method": "POST", "path": "/orders", "entity_ref": "order"},
        ],
        "relations": [],
        "actors": [],
    }


class TestObserverResolution:
    def test_real_get_resolved(self):
        ir = _make_ir_with_get()
        result = resolve_observer_capability(
            observer_requirement="before_state",
            primary_operation=ir["operations"][0],
            behavior_ir=ir,
        )
        assert result["resolution_status"] == "RESOLVED"
        assert result["operation_ref"] in ("op_read", "op_list")
        assert result["method"] == "GET"
        assert result["independent_from_primary_response"] is True

    def test_no_get_blocked(self):
        ir = _make_ir_no_get()
        result = resolve_observer_capability(
            observer_requirement="before_state",
            primary_operation=ir["operations"][0],
            behavior_ir=ir,
        )
        assert result["resolution_status"] == "BLOCKED"
        assert result["reason_code"] == "BLOCKED_MISSING_OBSERVER"

    def test_guessed_path_forbidden(self):
        """POST /orders must NOT auto-generate GET /orders/{id}."""
        ir = {
            "operations": [
                {"id": "op_create", "method": "POST", "path": "/orders", "entity_ref": "order"},
            ],
            "relations": [],
            "actors": [],
        }
        result = resolve_observer_capability(
            observer_requirement="after_state",
            primary_operation=ir["operations"][0],
            behavior_ir=ir,
        )
        # Must be BLOCKED, not invent a GET
        assert result["resolution_status"] == "BLOCKED"

    def test_identity_read_preferred(self):
        ir = _make_ir_with_get()
        result = resolve_observer_capability(
            observer_requirement="before_state",
            primary_operation=ir["operations"][0],
            behavior_ir=ir,
            required_bindings=["orderId"],
        )
        # Identity read (op_read) should score higher than collection read (op_list)
        assert result["operation_ref"] == "op_read"
        assert result["identity_strategy"] == "path_identity"

    def test_schema_version(self):
        ir = _make_ir_with_get()
        result = resolve_observer_capability(
            observer_requirement="entity_state",
            primary_operation=ir["operations"][0],
            behavior_ir=ir,
        )
        assert result["schema_version"] == "qualibug.observer-resolution-plan.v1"

    def test_fingerprint_present(self):
        ir = _make_ir_with_get()
        result = resolve_observer_capability(
            observer_requirement="before_state",
            primary_operation=ir["operations"][0],
            behavior_ir=ir,
        )
        assert result["fingerprint"] != ""
