"""Unit-test the generic state->action transition operation binding."""
from ai_test_asset_center.behavior_ir import (
    _state_action_stems,
    _infer_transition_operation,
)

# Representative documented write operations (method + path only matters).
OPS = [
    {"id": "op_orders_create", "method": "POST", "path": "/api/orders"},
    {"id": "op_orders_cancel", "method": "POST", "path": "/api/orders/:id/cancel"},
    {"id": "op_orders_ship", "method": "POST", "path": "/api/orders/:id/ship"},
    {"id": "op_orders_confirm", "method": "POST", "path": "/api/orders/:id/confirm"},
    {"id": "op_payments_pay", "method": "POST", "path": "/api/payments/pay"},
    {"id": "op_refunds_create", "method": "POST", "path": "/api/refunds"},
    {"id": "op_refunds_approve", "method": "POST", "path": "/api/refunds/:id/approve"},
    {"id": "op_refunds_reject", "method": "POST", "path": "/api/refunds/:id/reject"},
    {"id": "op_cart_items", "method": "POST", "path": "/api/cart/items"},
    {"id": "op_products_create", "method": "POST", "path": "/api/products/admin"},
]

print("stems:")
for s in ["PAID", "SHIPPED", "COMPLETED", "CANCELLED", "REFUNDED", "PENDING_PAYMENT", "CREATED"]:
    print(f"  {s:16s} -> {_state_action_stems(s)}")

print("\nbinding (entity=order):")
for to_state in ["PAID", "SHIPPED", "COMPLETED", "CANCELLED", "REFUNDED", "CREATED"]:
    op = _infer_transition_operation(OPS, "order", to_state)
    print(f"  -> {to_state:12s} : {op['id'] if op else 'NONE (fail-closed)'}  ({op['path'] if op else ''})")
