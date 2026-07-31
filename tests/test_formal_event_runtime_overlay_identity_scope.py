from __future__ import annotations

from ai_test_asset_center.formal_event_binding_identity_bridge import (
    project_formal_event_binding_identities,
)
from ai_test_asset_center.source_event_obligation_binding import (
    compile_obligations_with_source_event,
)


def _behavior_ir() -> dict:
    return {
        "operations": [
            {
                "id": "op:create",
                "method": "POST",
                "path": "/orders",
                "confidence": 1.0,
                "source_refs": [{"source_id": "api", "locator": "POST /orders"}],
            }
        ],
        "actors": [
            {
                "id": "actor:admin",
                "confidence": 1.0,
                "source_refs": [{"source_id": "roles", "locator": "admin"}],
            }
        ],
        "invariants": [
            {
                "id": "inv:runtime-event",
                "expression": {"kind": "event_delivery_contract"},
                "event_contract_id": "runtime-overlay:event",
                "event_contract": {
                    "contract_id": "runtime-overlay:event",
                    "expected_event_type": "OrderCreated",
                },
                "event_actor_ref": "actor:admin",
                "operation_refs": ["op:create"],
                "status": "accepted",
                "confidence": 1.0,
                "source_refs": [
                    {"source_id": "runtime-scan", "locator": "event contract"}
                ],
            }
        ],
        "relations": [
            {
                "id": "rel:produces",
                "relation_type": "produces",
                "operation_ref": "op:create",
                "from_ref": "op:create",
                "to_ref": "inv:runtime-event",
                "actor_ref": "actor:admin",
                "source_refs": [
                    {"source_id": "runtime-scan", "locator": "event contract"}
                ],
            }
        ],
    }


def test_unmanaged_runtime_overlay_event_is_not_blocked_by_unrelated_identity_graph() -> None:
    asset = {
        "binding_identity_graph": {
            "action_surface_bindings": [],
            "observer_bindings": [
                {
                    "observer_binding_id": "observer:other",
                    "binding_kind": "SOURCE_EVENT_DELIVERY_OBSERVER",
                    "event_contract_ref": "enterprise:event:other",
                    "implementation_binding_ref": "implementation:other",
                    "interface_id": "api:other",
                    "status": "BOUND",
                    "authoritative": True,
                }
            ],
        }
    }

    model, receipt = project_formal_event_binding_identities(_behavior_ir(), asset)

    invariant = model["invariants"][0]
    assert receipt["status"] == "RUNTIME_OVERLAY_ONLY"
    assert receipt["identity_required"] is False
    assert receipt["runtime_overlay_event_invariant_count"] == 1
    assert invariant["event_binding_identity_required"] is False
    assert invariant["event_binding_identity_status"] == "RUNTIME_OVERLAY_ONLY"

    compiled = compile_obligations_with_source_event(
        model,
        base_compile=lambda _model: {
            "obligations": [],
            "coverage_gaps": [],
            "by_family": {},
        },
    )

    assert compiled["source_event_obligation_receipt"]["status"] == "COMPILED"
    assert len(compiled["obligations"]) == 1
    assert compiled["obligations"][0]["property"][
        "formal_event_binding_identity"
    ] == {}
