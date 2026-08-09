# -*- coding: utf-8 -*-
"""State-machine execution chain — transition binding + wrong-source probes.

Root causes under test: (1) terse machine declarations (``PAID -> SHIPPED``)
and terse operation summaries (``取消订单。``/``发货``/``确认收货``) carried
no TO-state mention with an effect marker, so no transition ever bound to an
operation, the Behavior IR dropped every edge, and the state family compiled
zero obligations; (2) even a bound edge only verified the ALLOWED path — the
machine's wrong-source acceptance (cancel from PAID, ship from an unpaid
order, confirm before shipping) was structurally unreachable.

The tests pin the generic language-resource routing (verb-action bridge,
entry-state collection create) and the wrong-source forbidden probes on a
small industry-neutral synthetic document — no benchmark data.
"""
from __future__ import annotations

from ai_test_asset_center.behavior_ir_core import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.enterprise_knowledge_center._api import (
    build_runtime_source_knowledge_overlay,
)
from ai_test_asset_center.enterprise_knowledge_center.semantic_contract_binding import (
    apply_semantic_contract_binding,
)
from ai_test_asset_center.obligation_compiler import compile_obligations_from_behavior_ir
from ai_test_asset_center.state_precondition_planner import plan_state_precondition

# ── Synthetic industry-neutral lifecycle document (generic 订单/支付 style) ──
API_DOC = """# 示例 API 文档

## 订单

### POST /api/orders

创建订单。

### GET /api/orders

查询订单列表。

### POST /api/orders/:id/cancel

取消订单。

### POST /api/orders/:id/ship

发货。

### POST /api/orders/:id/confirm

确认收货。

## 支付

### POST /api/payments/pay

支付订单。

请求：

```json
{"orderId":"<order_id>","amount":100}
```
"""

RULES_DOC = """# 业务规则

## 订单状态机

```txt
CREATED -> PENDING_PAYMENT -> PAID -> SHIPPED -> COMPLETED
PENDING_PAYMENT -> CANCELLED
```

禁止状态流转：

- CANCELLED -> PAID
- CANCELLED -> SHIPPED
- CANCELLED -> COMPLETED
"""

SCHEMA_DOC = """CREATE TABLE orders (
  id UUID PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN ('CREATED','PENDING_PAYMENT','PAID','SHIPPED','COMPLETED','CANCELLED')),
  total_amount NUMERIC(12,2) NOT NULL
);
"""


def _bound_asset():
    overlay = build_runtime_source_knowledge_overlay(
        prd_text=RULES_DOC,
        api_spec_text=API_DOC,
        db_schema_text=SCHEMA_DOC,
    )
    from ai_test_asset_center.enterprise_knowledge_center import (
        merge_knowledge_asset_overlay,
    )
    asset = merge_knowledge_asset_overlay({}, overlay)
    return apply_semantic_contract_binding(asset, api_spec_text=API_DOC)


def _ir():
    asset = _bound_asset()
    return build_behavior_ir_from_knowledge_asset(
        asset, project_id="unit-test-project"
    )


class TestTransitionBinding:
    def test_all_order_transitions_bind_to_operations(self):
        asset = _bound_asset()
        machines = asset.get("state_machines") or []
        assert machines
        bound = []
        for machine in machines:
            for key in ("transitions", "forbidden_transitions"):
                for transition in machine.get(key) or []:
                    op_ref = transition.get("operation_ref")
                    if op_ref:
                        bound.append((transition.get("from"), transition.get("to"), op_ref))
        bound_edges = {(f, t) for f, t, _ in bound}
        assert ("CREATED", "PENDING_PAYMENT") in bound_edges      # collection create
        assert ("PENDING_PAYMENT", "PAID") in bound_edges         # pay
        assert ("PAID", "SHIPPED") in bound_edges                 # ship
        assert ("SHIPPED", "COMPLETED") in bound_edges            # confirm (verb bridge)
        assert ("PENDING_PAYMENT", "CANCELLED") in bound_edges    # cancel
        # forbidden edges bound to the performing operations
        assert ("CANCELLED", "PAID") in bound_edges
        assert ("CANCELLED", "SHIPPED") in bound_edges
        assert ("CANCELLED", "COMPLETED") in bound_edges

    def test_ir_emits_transition_relations_with_operations(self):
        ir = _ir()
        states_by_id = {s.get("id"): s for s in ir.get("states") or []}
        ops_by_id = {o.get("id"): o for o in ir.get("operations") or []}
        rels = [r for r in ir.get("relations") or [] if r.get("relation_type") == "transitions"]
        order_edges = []
        for r in rels:
            fs = states_by_id.get(r.get("from_ref"), {})
            ts = states_by_id.get(r.get("to_ref"), {})
            op = ops_by_id.get(r.get("operation_ref"), {})
            order_edges.append((fs.get("name"), ts.get("name"), op.get("path")))
        assert ("PENDING_PAYMENT", "PAID", "/api/payments/pay") in order_edges
        assert ("PAID", "SHIPPED", "/api/orders/:id/ship") in order_edges
        assert ("SHIPPED", "COMPLETED", "/api/orders/:id/confirm") in order_edges
        assert ("PENDING_PAYMENT", "CANCELLED", "/api/orders/:id/cancel") in order_edges


class TestStateObligations:
    def test_state_family_compiles_allowed_and_wrong_source_probes(self):
        ir = _ir()
        pack = compile_obligations_from_behavior_ir(ir)
        state_obls = [o for o in pack.get("obligations") or [] if o.get("risk_family") == "state"]
        allowed = [o for o in state_obls
                   if (o.get("property") or {}).get("expression", {}).get("kind") != "forbidden_state_transition"]
        forbidden = [o for o in state_obls
                     if (o.get("property") or {}).get("expression", {}).get("kind") == "forbidden_state_transition"]
        # allowed edges: 5 order transitions
        assert len(allowed) >= 5
        # wrong-source probes: cancel/ship/confirm edges each get adjacent-state probes
        assert len(forbidden) >= 4
        for obl in state_obls:
            prop = obl.get("property") or {}
            assert prop.get("state_field") == "status", "state_field must resolve for the precondition freeze"

    def test_wrong_source_probe_targets_paid_cancel(self):
        """ORDER-008 shape: cancel from PAID must be a forbidden probe."""
        ir = _ir()
        pack = compile_obligations_from_behavior_ir(ir)
        states_by_id = {s.get("id"): s.get("name") for s in ir.get("states") or []}
        forbidden = []
        for o in pack.get("obligations") or []:
            prop = o.get("property") or {}
            if prop.get("risk_family") != "state" and o.get("risk_family") != "state":
                continue
            expr = prop.get("expression") or {}
            if expr.get("kind") != "forbidden_state_transition":
                continue
            if states_by_id.get(prop.get("to_state_ref")) != "CANCELLED":
                continue
            forbidden.append(states_by_id.get(prop.get("from_state_ref")))
        # cancel is declared only from PENDING_PAYMENT; its wrong-source probes
        # cover the adjacent states (CREATED and PAID) — the PAID variant is
        # exactly the "已支付订单仍可取消" violation.
        assert "PAID" in forbidden
        assert "CREATED" in forbidden


class TestPreconditionChain:
    def test_pay_ship_confirm_chain_planning(self):
        """Establishment chains: create->pay->ship->confirm, all planned."""
        ir = _ir()
        plan_paid = plan_state_precondition(
            behavior_ir=ir, from_state="PAID", actors=["actor_buyer01"]
        )
        assert plan_paid.get("status") == "PLANNED"
        steps = plan_paid.get("steps") or []
        assert len(steps) == 2  # create -> pay
        ops_by_id = {o.get("id"): o.get("path") for o in ir.get("operations") or []}
        assert ops_by_id.get(steps[0].get("operation_ref")) == "/api/orders"
        assert ops_by_id.get(steps[1].get("operation_ref")) == "/api/payments/pay"

        plan_shipped = plan_state_precondition(
            behavior_ir=ir, from_state="SHIPPED", actors=["actor_buyer01"]
        )
        assert plan_shipped.get("status") == "PLANNED"
        shipped_ops = [
            ops_by_id.get(s.get("operation_ref")) for s in (plan_shipped.get("steps") or [])
        ]
        assert shipped_ops == ["/api/orders", "/api/payments/pay", "/api/orders/:id/ship"]

        plan_completed = plan_state_precondition(
            behavior_ir=ir, from_state="COMPLETED", actors=["actor_buyer01"]
        )
        assert plan_completed.get("status") == "PLANNED"
        completed_ops = [
            ops_by_id.get(s.get("operation_ref")) for s in (plan_completed.get("steps") or [])
        ]
        assert completed_ops == [
            "/api/orders", "/api/payments/pay", "/api/orders/:id/ship",
            "/api/orders/:id/confirm",
        ]
