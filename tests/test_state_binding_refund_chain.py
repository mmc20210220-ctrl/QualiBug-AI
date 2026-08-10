# -*- coding: utf-8 -*-
"""Attack-B Fix 2: refund-chain state-transition binding.

Regression coverage for the three machine edges that stayed unbound in run16
(``PAID -> REFUND_REQUESTED``, ``REFUND_REQUESTED -> REFUNDED``,
``COMPLETED -> REFUND_REQUESTED``), which left every REFUNDED wrong-source
probe with an empty establishment plan (probes executed from the wrong state
and could never produce a finding):

* request-state channel: ``<ACTION>_REQUESTED`` states are established by the
  create of the request entity whose name matches ACTION;
* denial-verb tie-break: when ``approve`` and ``reject`` both mention the
  refund family, the denial operation (identity ``reject``) is never the
  performer of the transition INTO the positive outcome state.
"""
from __future__ import annotations

import pytest

from ai_test_asset_center.enterprise_knowledge_center.semantic_contract_binding import (
    _bind_request_state_to_request_entity_create,
    _bind_transition_by_verb_action_bridge,
    _interface_write_endpoints,
)


def _interface(iid: str, method: str, path: str, summary: str, action: str = "") -> dict:
    return {
        "id": iid,
        "interface_id": iid,
        "method": method,
        "path": path,
        "summary": summary,
        "action": action,
        "entity": path.split("/")[2] if len(path.split("/")) > 2 else "",
    }


def _refund_interfaces() -> dict:
    return {
        iface["id"]: iface
        for iface in [
            _interface("api:POST:/api/refunds", "POST", "/api/refunds", "请求："),
            _interface(
                "api:POST:/api/refunds/:id/approve",
                "POST",
                "/api/refunds/:id/approve",
                "财务或管理员审批退款。",
            ),
            _interface(
                "api:POST:/api/refunds/:id/reject",
                "POST",
                "/api/refunds/:id/reject",
                "财务或管理员驳回退款。",
            ),
        ]
    }


class TestRequestStateChannel:
    def test_binds_refund_requested_to_refunds_create(self):
        index = _refund_interfaces()
        endpoints = _interface_write_endpoints(index)
        bound = _bind_request_state_to_request_entity_create(
            transition={"from": "PAID", "to": "REFUND_REQUESTED"},
            to_name="REFUND_REQUESTED",
            endpoints=endpoints,
            interface_index=index,
        )
        assert bound is not None
        interface_id, evidence = bound
        assert interface_id == "api:POST:/api/refunds"
        assert evidence["derivation"] == "request_state_entity_create"

    def test_ignores_non_requested_states(self):
        index = _refund_interfaces()
        endpoints = _interface_write_endpoints(index)
        bound = _bind_request_state_to_request_entity_create(
            transition={"from": "CREATED", "to": "PENDING_PAYMENT"},
            to_name="PENDING_PAYMENT",
            endpoints=endpoints,
            interface_index=index,
        )
        assert bound is None

    def test_mismatched_request_entity_never_binds(self):
        # Only an entity whose name matches the state's action stem binds:
        # RETURN_REQUESTED must never route to the refunds create.
        index = _refund_interfaces()
        endpoints = _interface_write_endpoints(index)
        bound = _bind_request_state_to_request_entity_create(
            transition={"from": "PAID", "to": "RETURN_REQUESTED"},
            to_name="RETURN_REQUESTED",
            endpoints=endpoints,
            interface_index=index,
        )
        assert bound is None


class TestDenialVerbTieBreak:
    def test_approve_wins_over_reject_for_refunded(self):
        index = _refund_interfaces()
        endpoints = _interface_write_endpoints(index)
        bound = _bind_transition_by_verb_action_bridge(
            transition={"from": "REFUND_REQUESTED", "to": "REFUNDED"},
            to_name="REFUNDED",
            endpoints=endpoints,
            interface_index=index,
        )
        assert bound is not None
        interface_id, evidence = bound
        assert interface_id == "api:POST:/api/refunds/:id/approve"
        assert evidence["derivation"] == "to_state_verb_action_bridge"

    def test_lone_denial_candidate_still_binds(self):
        # A transition INTO a denied outcome with a single matching denial
        # operation must still bind (the demotion never vetoes a unique match).
        index = _refund_interfaces()
        endpoints = [
            row
            for row in _interface_write_endpoints(index)
            if ":id/approve" not in row["path"]
        ]
        bound = _bind_transition_by_verb_action_bridge(
            transition={"from": "REFUND_REQUESTED", "to": "REJECTED"},
            to_name="REJECTED",
            endpoints=endpoints,
            interface_index=index,
        )
        assert bound is not None
        interface_id, _evidence = bound
        assert interface_id == "api:POST:/api/refunds/:id/reject"

    def test_unique_match_unaffected_by_denial(self):
        index = _refund_interfaces()
        endpoints = _interface_write_endpoints(index)
        bound = _bind_transition_by_verb_action_bridge(
            transition={"from": "PENDING_PAYMENT", "to": "CANCELLED"},
            to_name="CANCELLED",
            endpoints=endpoints,
            interface_index=index,
        )
        # no cancel endpoint in this fixture -> no bind; proves no phantom match
        assert bound is None


class TestRefundChainEndToEnd:
    def test_refunded_chain_planned_with_stamped_steps(self):
        """REFUNDED is now reachable via create -> pay -> refund request -> approve."""
        from pathlib import Path

        import sys

        root = Path(__file__).resolve().parents[1]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        src_dir = root / "platform_inputs" / "evaluation-benchmark-mall-held-in-131"
        prd = (src_dir / "PRD.md").read_text(encoding="utf-8", errors="replace")
        api = (src_dir / "API_SPEC.md").read_text(encoding="utf-8", errors="replace")
        schema = (src_dir / "schema.sql").read_text(encoding="utf-8", errors="replace")
        rules_md = (src_dir / "BUSINESS_RULES.md").read_text(encoding="utf-8", errors="replace")

        from ai_test_asset_center.behavior_ir_core import (
            build_behavior_ir_from_knowledge_asset,
        )
        from ai_test_asset_center.enterprise_knowledge_center import (
            build_runtime_source_knowledge_overlay,
            merge_knowledge_asset_overlay,
        )
        from ai_test_asset_center.enterprise_knowledge_center.semantic_contract_binding import (
            apply_semantic_contract_binding,
        )
        from ai_test_asset_center.state_precondition_planner import (
            STATUS_PLANNED,
            plan_state_precondition,
        )

        overlay = build_runtime_source_knowledge_overlay(
            prd_text=prd + "\n\n" + rules_md,
            api_spec_text=api,
            db_schema_text=schema,
        )
        asset = merge_knowledge_asset_overlay({}, overlay)
        asset = apply_semantic_contract_binding(asset, api_spec_text=api)
        ir = build_behavior_ir_from_knowledge_asset(
            asset, project_id="benchmark-mall-held-in-131"
        )

        plan = plan_state_precondition(
            behavior_ir=ir,
            from_state="REFUNDED",
            actors=["actor_buyer01", "actor_warehouse01", "actor_seller01"],
        )
        assert plan["status"] == STATUS_PLANNED
        steps = plan["steps"]
        assert len(steps) == 4
        tos = [step["to_state"] for step in steps]
        assert tos == ["pending_payment", "paid", "refund_requested", "refunded"]
        # the approve op performs the last step
        ops = {op["id"]: op for op in (ir.get("operations") or [])}
        last_op = ops.get(steps[-1]["operation_ref"], {})
        assert last_op.get("path") == "/api/refunds/:id/approve"
        for step in steps:
            assert step["state_field"] == "status"
            assert step["readback_contract"]["required_fields"] == [{"field": "status"}]
