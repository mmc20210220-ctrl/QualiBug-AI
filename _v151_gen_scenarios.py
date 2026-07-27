"""V1.5.1 Phase 3: Generate live scenario manifest from source materials."""
import json
import datetime
import hashlib

now = datetime.datetime.now().isoformat()
out_dir = "artifacts/spec_v1_5_1"

# All scenarios derived ONLY from source-declared operations and state machine
scenarios = [
    # ── Type A: Disposable Fixture Creation Chains (multi-entity) ──
    {
        "scenario_id": "SCN-FIX-001",
        "scenario_type": "disposable_fixture_creation",
        "source_refs": ["API_SPEC.md:POST /api/products/admin", "API_SPEC.md:POST /api/orders"],
        "obligation_id": "obl_fixture_product_order",
        "risk_family": "process",
        "protocol_template": "multi_step_business_process",
        "fixture_entities": ["products", "orders", "order_items"],
        "create_operations": [
            {"step_id": "fix_create_product", "method": "POST", "path": "/api/products/admin", "actor_ref": "seller01"},
            {"step_id": "fix_create_order", "method": "POST", "path": "/api/orders", "actor_ref": "buyer01"}
        ],
        "business_operations": [
            {"step_id": "biz_get_order", "method": "GET", "path": "/api/orders/:id", "actor_ref": "buyer01"}
        ],
        "expected_step_order": ["fix_create_product", "fix_create_order", "biz_get_order"],
        "state_precondition": None,
        "observers": ["process_step_timeline"],
        "cleanup_contracts": [
            {"step_id": "fix_create_order", "method": "DELETE", "path": "/api/orders/:id", "actor_ref": "admin"},
            {"step_id": "fix_create_product", "method": "DELETE", "path": "/api/products/admin/:sku", "actor_ref": "admin"}
        ],
        "multi_table": True
    },
    {
        "scenario_id": "SCN-FIX-002",
        "scenario_type": "disposable_fixture_creation",
        "source_refs": ["API_SPEC.md:POST /api/cart/items", "API_SPEC.md:POST /api/orders"],
        "obligation_id": "obl_fixture_cart_order",
        "risk_family": "process",
        "protocol_template": "multi_step_business_process",
        "fixture_entities": ["cart_items", "orders", "order_items"],
        "create_operations": [
            {"step_id": "fix_add_cart", "method": "POST", "path": "/api/cart/items", "actor_ref": "buyer01"},
            {"step_id": "fix_create_order", "method": "POST", "path": "/api/orders", "actor_ref": "buyer01"}
        ],
        "business_operations": [
            {"step_id": "biz_verify_order", "method": "GET", "path": "/api/orders/:id", "actor_ref": "buyer01"}
        ],
        "expected_step_order": ["fix_add_cart", "fix_create_order", "biz_verify_order"],
        "state_precondition": None,
        "observers": ["process_step_timeline"],
        "cleanup_contracts": [
            {"step_id": "fix_create_order", "method": "DELETE", "path": "/api/orders/:id", "actor_ref": "admin"},
            {"step_id": "fix_add_cart", "method": "DELETE", "path": "/api/cart/items/:id", "actor_ref": "buyer01"}
        ],
        "multi_table": True
    },
    {
        "scenario_id": "SCN-FIX-003",
        "scenario_type": "disposable_fixture_creation",
        "source_refs": ["API_SPEC.md:POST /api/orders", "API_SPEC.md:POST /api/payments/pay"],
        "obligation_id": "obl_fixture_order_payment",
        "risk_family": "process",
        "protocol_template": "multi_step_business_process",
        "fixture_entities": ["orders", "order_items", "payments"],
        "create_operations": [
            {"step_id": "fix_create_order", "method": "POST", "path": "/api/orders", "actor_ref": "buyer01"},
            {"step_id": "fix_pay_order", "method": "POST", "path": "/api/payments/pay", "actor_ref": "buyer01"}
        ],
        "business_operations": [
            {"step_id": "biz_verify_paid", "method": "GET", "path": "/api/orders/:id", "actor_ref": "buyer01"}
        ],
        "expected_step_order": ["fix_create_order", "fix_pay_order", "biz_verify_paid"],
        "state_precondition": None,
        "observers": ["process_step_timeline"],
        "cleanup_contracts": [
            {"step_id": "fix_pay_order", "method": "POST", "path": "/api/refunds", "actor_ref": "buyer01"},
            {"step_id": "fix_create_order", "method": "POST", "path": "/api/orders/:id/cancel", "actor_ref": "buyer01"}
        ],
        "multi_table": True
    },
    # ── Type B: State Precondition Establishment Chains ──
    {
        "scenario_id": "SCN-STATE-001",
        "scenario_type": "state_precondition",
        "source_refs": ["BUSINESS_RULES.md:order_state_machine", "API_SPEC.md:POST /api/payments/pay"],
        "obligation_id": "obl_state_to_paid",
        "risk_family": "state",
        "protocol_template": "state_chain_process",
        "fixture_entities": ["orders", "order_items"],
        "create_operations": [
            {"step_id": "fix_create_order", "method": "POST", "path": "/api/orders", "actor_ref": "buyer01"}
        ],
        "business_operations": [
            {"step_id": "biz_ship_order", "method": "POST", "path": "/api/orders/:id/ship", "actor_ref": "warehouse01"}
        ],
        "expected_step_order": ["fix_create_order", "sp_pay_order", "biz_ship_order"],
        "state_precondition": {
            "entity": "order",
            "from_state": "CREATED",
            "target_state": "PAID",
            "declared_path": [
                {"from": "CREATED", "to": "PENDING_PAYMENT", "operation": "POST /api/orders"},
                {"from": "PENDING_PAYMENT", "to": "PAID", "operation": "POST /api/payments/pay"}
            ],
            "steps": [
                {"step_id": "sp_pay_order", "method": "POST", "path": "/api/payments/pay", "actor_ref": "buyer01", "expected_to_state": "PAID"}
            ]
        },
        "observers": ["process_step_timeline"],
        "cleanup_contracts": [
            {"step_id": "fix_create_order", "method": "POST", "path": "/api/orders/:id/cancel", "actor_ref": "admin"}
        ],
        "multi_table": False
    },
    {
        "scenario_id": "SCN-STATE-002",
        "scenario_type": "state_precondition",
        "source_refs": ["BUSINESS_RULES.md:order_state_machine", "API_SPEC.md:POST /api/orders/:id/ship"],
        "obligation_id": "obl_state_to_shipped",
        "risk_family": "state",
        "protocol_template": "state_chain_process",
        "fixture_entities": ["orders", "order_items"],
        "create_operations": [
            {"step_id": "fix_create_order", "method": "POST", "path": "/api/orders", "actor_ref": "buyer01"}
        ],
        "business_operations": [
            {"step_id": "biz_confirm_order", "method": "POST", "path": "/api/orders/:id/confirm", "actor_ref": "buyer01"}
        ],
        "expected_step_order": ["fix_create_order", "sp_pay_order", "sp_ship_order", "biz_confirm_order"],
        "state_precondition": {
            "entity": "order",
            "from_state": "CREATED",
            "target_state": "SHIPPED",
            "declared_path": [
                {"from": "CREATED", "to": "PENDING_PAYMENT", "operation": "POST /api/orders"},
                {"from": "PENDING_PAYMENT", "to": "PAID", "operation": "POST /api/payments/pay"},
                {"from": "PAID", "to": "SHIPPED", "operation": "POST /api/orders/:id/ship"}
            ],
            "steps": [
                {"step_id": "sp_pay_order", "method": "POST", "path": "/api/payments/pay", "actor_ref": "buyer01", "expected_to_state": "PAID"},
                {"step_id": "sp_ship_order", "method": "POST", "path": "/api/orders/:id/ship", "actor_ref": "warehouse01", "expected_to_state": "SHIPPED"}
            ]
        },
        "observers": ["process_step_timeline"],
        "cleanup_contracts": [
            {"step_id": "fix_create_order", "method": "POST", "path": "/api/orders/:id/cancel", "actor_ref": "admin"}
        ],
        "multi_table": False
    },
    {
        "scenario_id": "SCN-STATE-003",
        "scenario_type": "state_precondition",
        "source_refs": ["BUSINESS_RULES.md:order_state_machine", "API_SPEC.md:POST /api/refunds"],
        "obligation_id": "obl_state_to_refund_requested",
        "risk_family": "state",
        "protocol_template": "state_chain_process",
        "fixture_entities": ["orders", "order_items", "payments"],
        "create_operations": [
            {"step_id": "fix_create_order", "method": "POST", "path": "/api/orders", "actor_ref": "buyer02"}
        ],
        "business_operations": [
            {"step_id": "biz_approve_refund", "method": "POST", "path": "/api/refunds/:id/approve", "actor_ref": "finance01"}
        ],
        "expected_step_order": ["fix_create_order", "sp_pay_order", "sp_create_refund", "biz_approve_refund"],
        "state_precondition": {
            "entity": "order",
            "from_state": "CREATED",
            "target_state": "REFUND_REQUESTED",
            "declared_path": [
                {"from": "CREATED", "to": "PENDING_PAYMENT", "operation": "POST /api/orders"},
                {"from": "PENDING_PAYMENT", "to": "PAID", "operation": "POST /api/payments/pay"},
                {"from": "PAID", "to": "REFUND_REQUESTED", "operation": "POST /api/refunds"}
            ],
            "steps": [
                {"step_id": "sp_pay_order", "method": "POST", "path": "/api/payments/pay", "actor_ref": "buyer02", "expected_to_state": "PAID"},
                {"step_id": "sp_create_refund", "method": "POST", "path": "/api/refunds", "actor_ref": "buyer02", "expected_to_state": "REFUND_REQUESTED"}
            ]
        },
        "observers": ["process_step_timeline"],
        "cleanup_contracts": [
            {"step_id": "sp_create_refund", "method": "POST", "path": "/api/refunds/:id/reject", "actor_ref": "finance01"},
            {"step_id": "fix_create_order", "method": "POST", "path": "/api/orders/:id/cancel", "actor_ref": "admin"}
        ],
        "multi_table": True
    },
    # ── Type C: Source-Declared Order Chains ──
    {
        "scenario_id": "SCN-SEQ-001",
        "scenario_type": "source_declared_order",
        "source_refs": ["BUSINESS_RULES.md:order_lifecycle", "API_SPEC.md:Order"],
        "obligation_id": "obl_seq_full_order_lifecycle",
        "risk_family": "process",
        "protocol_template": "sequence_verification",
        "fixture_entities": ["orders", "order_items", "payments"],
        "create_operations": [
            {"step_id": "fix_create_order", "method": "POST", "path": "/api/orders", "actor_ref": "buyer01"}
        ],
        "business_operations": [
            {"step_id": "biz_pay", "method": "POST", "path": "/api/payments/pay", "actor_ref": "buyer01"},
            {"step_id": "biz_ship", "method": "POST", "path": "/api/orders/:id/ship", "actor_ref": "warehouse01"},
            {"step_id": "biz_confirm", "method": "POST", "path": "/api/orders/:id/confirm", "actor_ref": "buyer01"}
        ],
        "expected_step_order": ["fix_create_order", "biz_pay", "biz_ship", "biz_confirm"],
        "state_precondition": None,
        "observers": ["process_step_timeline"],
        "cleanup_contracts": [
            {"step_id": "fix_create_order", "method": "POST", "path": "/api/orders/:id/cancel", "actor_ref": "admin"}
        ],
        "multi_table": True
    },
    {
        "scenario_id": "SCN-SEQ-002",
        "scenario_type": "source_declared_order",
        "source_refs": ["BUSINESS_RULES.md:refund_flow", "API_SPEC.md:Refund"],
        "obligation_id": "obl_seq_refund_flow",
        "risk_family": "process",
        "protocol_template": "sequence_verification",
        "fixture_entities": ["orders", "payments", "refunds"],
        "create_operations": [
            {"step_id": "fix_create_order", "method": "POST", "path": "/api/orders", "actor_ref": "buyer02"},
            {"step_id": "fix_pay", "method": "POST", "path": "/api/payments/pay", "actor_ref": "buyer02"}
        ],
        "business_operations": [
            {"step_id": "biz_create_refund", "method": "POST", "path": "/api/refunds", "actor_ref": "buyer02"},
            {"step_id": "biz_approve_refund", "method": "POST", "path": "/api/refunds/:id/approve", "actor_ref": "finance01"}
        ],
        "expected_step_order": ["fix_create_order", "fix_pay", "biz_create_refund", "biz_approve_refund"],
        "state_precondition": None,
        "observers": ["process_step_timeline"],
        "cleanup_contracts": [
            {"step_id": "fix_create_order", "method": "POST", "path": "/api/orders/:id/cancel", "actor_ref": "admin"}
        ],
        "multi_table": True
    },
    {
        "scenario_id": "SCN-SEQ-003",
        "scenario_type": "source_declared_order",
        "source_refs": ["BUSINESS_RULES.md:order_cancel_flow", "API_SPEC.md:POST /api/orders/:id/cancel"],
        "obligation_id": "obl_seq_cancel_flow",
        "risk_family": "process",
        "protocol_template": "sequence_verification",
        "fixture_entities": ["orders", "order_items"],
        "create_operations": [
            {"step_id": "fix_create_order", "method": "POST", "path": "/api/orders", "actor_ref": "buyer01"}
        ],
        "business_operations": [
            {"step_id": "biz_verify_pending", "method": "GET", "path": "/api/orders/:id", "actor_ref": "buyer01"},
            {"step_id": "biz_cancel", "method": "POST", "path": "/api/orders/:id/cancel", "actor_ref": "buyer01"}
        ],
        "expected_step_order": ["fix_create_order", "biz_verify_pending", "biz_cancel"],
        "state_precondition": None,
        "observers": ["process_step_timeline"],
        "cleanup_contracts": [],
        "multi_table": False
    },
    # ── Type D: Partial Failure Reverse Cleanup ──
    {
        "scenario_id": "SCN-CLEAN-001",
        "scenario_type": "partial_failure_reverse_cleanup",
        "source_refs": ["API_SPEC.md:POST /api/orders", "API_SPEC.md:POST /api/payments/pay"],
        "obligation_id": "obl_cleanup_order_pay_fail",
        "risk_family": "process",
        "protocol_template": "multi_step_business_process",
        "fixture_entities": ["orders", "order_items", "payments"],
        "create_operations": [
            {"step_id": "fix_create_order", "method": "POST", "path": "/api/orders", "actor_ref": "buyer01"},
            {"step_id": "fix_pay_invalid", "method": "POST", "path": "/api/payments/pay", "actor_ref": "buyer01", "inject_failure": True}
        ],
        "business_operations": [],
        "expected_step_order": ["fix_create_order", "fix_pay_invalid"],
        "state_precondition": None,
        "observers": ["process_step_timeline"],
        "cleanup_contracts": [
            {"step_id": "fix_create_order", "method": "POST", "path": "/api/orders/:id/cancel", "actor_ref": "buyer01"}
        ],
        "multi_table": True,
        "failure_injection": {"step_id": "fix_pay_invalid", "method": "invalid_amount"}
    },
    {
        "scenario_id": "SCN-CLEAN-002",
        "scenario_type": "partial_failure_reverse_cleanup",
        "source_refs": ["API_SPEC.md:POST /api/orders/:id/ship", "BUSINESS_RULES.md:order_state_machine"],
        "obligation_id": "obl_cleanup_paid_ship_fail",
        "risk_family": "process",
        "protocol_template": "multi_step_business_process",
        "fixture_entities": ["orders", "order_items", "payments"],
        "create_operations": [
            {"step_id": "fix_create_order", "method": "POST", "path": "/api/orders", "actor_ref": "buyer02"},
            {"step_id": "fix_pay_order", "method": "POST", "path": "/api/payments/pay", "actor_ref": "buyer02"},
            {"step_id": "fix_ship_wrong_actor", "method": "POST", "path": "/api/orders/:id/ship", "actor_ref": "buyer02", "inject_failure": True}
        ],
        "business_operations": [],
        "expected_step_order": ["fix_create_order", "fix_pay_order", "fix_ship_wrong_actor"],
        "state_precondition": None,
        "observers": ["process_step_timeline"],
        "cleanup_contracts": [
            {"step_id": "fix_pay_order", "method": "POST", "path": "/api/refunds", "actor_ref": "buyer02"},
            {"step_id": "fix_create_order", "method": "POST", "path": "/api/orders/:id/cancel", "actor_ref": "admin"}
        ],
        "multi_table": True,
        "failure_injection": {"step_id": "fix_ship_wrong_actor", "method": "wrong_actor_permission"}
    },
    {
        "scenario_id": "SCN-CLEAN-003",
        "scenario_type": "partial_failure_reverse_cleanup",
        "source_refs": ["API_SPEC.md:POST /api/refunds", "API_SPEC.md:POST /api/refunds/:id/approve"],
        "obligation_id": "obl_cleanup_refund_approve_fail",
        "risk_family": "process",
        "protocol_template": "multi_step_business_process",
        "fixture_entities": ["orders", "payments", "refunds"],
        "create_operations": [
            {"step_id": "fix_create_order", "method": "POST", "path": "/api/orders", "actor_ref": "buyer01"},
            {"step_id": "fix_pay_order", "method": "POST", "path": "/api/payments/pay", "actor_ref": "buyer01"},
            {"step_id": "fix_create_refund", "method": "POST", "path": "/api/refunds", "actor_ref": "buyer01"},
            {"step_id": "fix_approve_wrong_actor", "method": "POST", "path": "/api/refunds/:id/approve", "actor_ref": "buyer01", "inject_failure": True}
        ],
        "business_operations": [],
        "expected_step_order": ["fix_create_order", "fix_pay_order", "fix_create_refund", "fix_approve_wrong_actor"],
        "state_precondition": None,
        "observers": ["process_step_timeline"],
        "cleanup_contracts": [
            {"step_id": "fix_create_refund", "method": "POST", "path": "/api/refunds/:id/reject", "actor_ref": "finance01"},
            {"step_id": "fix_create_order", "method": "POST", "path": "/api/orders/:id/cancel", "actor_ref": "admin"}
        ],
        "multi_table": True,
        "failure_injection": {"step_id": "fix_approve_wrong_actor", "method": "wrong_actor_permission"}
    },
]

# Validate counts
type_counts = {}
for s in scenarios:
    t = s["scenario_type"]
    type_counts[t] = type_counts.get(t, 0) + 1

multi_table_count = sum(1 for s in scenarios if s.get("multi_table"))

manifest = {
    "spec_version": "V1.5.1",
    "generated_at": now,
    "total_scenarios": len(scenarios),
    "type_distribution": type_counts,
    "multi_table_fixture_count": multi_table_count,
    "source_materials": [
        "platform_workspace/benchmark_mall_131/input/API_SPEC.md",
        "platform_workspace/benchmark_mall_131/input/BUSINESS_RULES.md",
        "platform_workspace/benchmark_mall_131/input/DB_SCHEMA.md",
        "platform_workspace/benchmark_mall_131/defect_discovery/enterprise_business_knowledge_asset.json"
    ],
    "scenarios": scenarios,
    "manifest_hash": hashlib.sha256(
        json.dumps([s["scenario_id"] for s in scenarios]).encode()
    ).hexdigest()[:16],
}

with open(f"{out_dir}/v151_live_scenario_manifest.json", "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2, ensure_ascii=False)

print(f"Total scenarios: {len(scenarios)}")
print(f"Type distribution: {type_counts}")
print(f"Multi-table fixtures: {multi_table_count}")
print("Scenario manifest written")
