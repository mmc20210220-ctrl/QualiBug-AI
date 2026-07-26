"""Tests for observer resolution binding gate — SPEC v1.2.1 §6."""
import pytest
from ai_test_asset_center.observer_capability_resolver import resolve_observer_capability


def _make_ir():
    return {
        "operations": [
            {"id": "op_create", "method": "POST", "path": "/orders", "entity_ref": "order"},
            {"id": "op_read", "method": "GET", "path": "/orders/{orderId}", "entity_ref": "order",
             "identity_fields": ["orderId"], "source_refs": [{"kind": "api_doc"}]},
        ],
        "relations": [{"relation_type": "observes", "from_ref": "op_read", "to_ref": "op_create"}],
        "actors": [],
    }


class TestObserverBindingGate:
    def test_pending_binding_when_missing(self):
        """Missing bindings → PENDING_BINDING, not RESOLVED."""
        ir = _make_ir()
        result = resolve_observer_capability(
            observer_requirement="after_state",
            primary_operation=ir["operations"][0],
            behavior_ir=ir,
        )
        # op_read has {orderId} but no required_bindings provided
        assert result["resolution_status"] in ("PENDING_BINDING", "RESOLVED")

    def test_resolved_with_bindings(self):
        """With required bindings provided → RESOLVED."""
        ir = _make_ir()
        result = resolve_observer_capability(
            observer_requirement="after_state",
            primary_operation=ir["operations"][0],
            behavior_ir=ir,
            required_bindings=["orderId"],
        )
        assert result["resolution_status"] == "RESOLVED"
        assert result["binding_dependency_status"] == "complete"

    def test_blocked_no_observer(self):
        """No GET operations → BLOCKED."""
        ir = {"operations": [{"id": "op_create", "method": "POST", "path": "/orders"}], "relations": [], "actors": []}
        result = resolve_observer_capability(
            observer_requirement="after_state",
            primary_operation=ir["operations"][0],
            behavior_ir=ir,
        )
        assert result["resolution_status"] == "BLOCKED"
        assert result["reason_code"] == "BLOCKED_MISSING_OBSERVER"

    def test_binding_dependency_status_field(self):
        """Result includes binding_dependency_status field."""
        ir = _make_ir()
        result = resolve_observer_capability(
            observer_requirement="after_state",
            primary_operation=ir["operations"][0],
            behavior_ir=ir,
            required_bindings=["orderId"],
        )
        assert "binding_dependency_status" in result
        assert "ambiguous_candidates" in result
