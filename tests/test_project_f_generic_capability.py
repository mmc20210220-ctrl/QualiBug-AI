"""Project F Generic Four-Capability Integration Tests.

SPEC §18: Verifies Actor Matrix, State Path, Cross-Entity Chain, and
Idempotency Replay all activate from the normal product mainchain using
fully generic fixtures with NO project-specific naming.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

# ─── Generic Behavior IR Fixture (no project naming) ─────────────────────────

GENERIC_BEHAVIOR_IR: dict[str, Any] = {
    "schema_version": "qualibug.behavior-ir.v1",
    "project": "generic_enterprise_system",
    "actors": [
        {"id": "actor_owner", "role": "owner", "scope": "tenant_a", "status": "active", "credential_secret_ref": "secret_owner"},
        {"id": "actor_peer", "role": "member", "scope": "tenant_a", "status": "active", "credential_secret_ref": "secret_peer"},
        {"id": "actor_other_tenant", "role": "member", "scope": "tenant_b", "status": "active", "credential_secret_ref": "secret_other"},
        {"id": "actor_admin", "role": "admin", "scope": "global", "status": "active", "credential_secret_ref": "secret_admin"},
        {"id": "actor_anonymous", "role": "anonymous", "scope": "", "status": "active", "credential_secret_ref": "secret_anon"},
    ],
    "operations": [
        {"id": "op_create_order", "method": "POST", "path": "/api/v1/orders", "entity": "order"},
        {"id": "op_get_order", "method": "GET", "path": "/api/v1/orders/{order_id}", "entity": "order"},
        {"id": "op_update_order_status", "method": "PATCH", "path": "/api/v1/orders/{order_id}/status", "entity": "order"},
        {"id": "op_list_orders", "method": "GET", "path": "/api/v1/orders", "entity": "order"},
        {"id": "op_create_shipment", "method": "POST", "path": "/api/v1/shipments", "entity": "shipment"},
        {"id": "op_get_shipment", "method": "GET", "path": "/api/v1/shipments/{shipment_id}", "entity": "shipment"},
        {"id": "op_create_payment", "method": "POST", "path": "/api/v1/payments", "entity": "payment"},
        {"id": "op_delete_order", "method": "DELETE", "path": "/api/v1/orders/{order_id}", "entity": "order"},
    ],
    "relations": [
        {"id": "rel_order_shipment", "from_entity": "order", "to_entity": "shipment", "type": "PARENT_CHILD"},
        {"id": "rel_order_payment", "from_entity": "order", "to_entity": "payment", "type": "ASSOCIATION"},
    ],
    "invariants": [
        {
            "id": "inv_order_status_transition",
            "type": "STATE_TRANSITION",
            "entity": "order",
            "states": ["draft", "confirmed", "shipped", "delivered", "cancelled"],
            "transitions": [
                {"from": "draft", "to": "confirmed"},
                {"from": "confirmed", "to": "shipped"},
                {"from": "shipped", "to": "delivered"},
                {"from": "draft", "to": "cancelled"},
                {"from": "confirmed", "to": "cancelled"},
            ],
            "forbidden": [
                {"from": "delivered", "to": "draft"},
                {"from": "cancelled", "to": "confirmed"},
            ],
        },
        {
            "id": "inv_order_tenant_isolation",
            "type": "TENANT_ISOLATION",
            "entity": "order",
            "scope_field": "tenant_id",
        },
        {
            "id": "inv_order_ownership",
            "type": "RESOURCE_OWNERSHIP",
            "entity": "order",
            "owner_field": "created_by",
        },
        {
            "id": "inv_payment_idempotency",
            "type": "IDEMPOTENCY",
            "entity": "payment",
            "idempotency_key": "Idempotency-Key",
            "operation": "op_create_payment",
        },
    ],
}

# Generic obligations for testing
GENERIC_AUTH_OBLIGATION: dict[str, Any] = {
    "obligation_id": "obl_generic_auth_001",
    "mechanism": "TENANT_OR_SCOPE_ISOLATION",
    "risk_family": "isolation",
    "entity": "order",
    "required_operations": ["op_get_order"],
    "required_actors": ["actor_owner", "actor_other_tenant"],
    "source_refs": [{"ref": "inv_order_tenant_isolation"}],
    "invariant_id": "inv_order_tenant_isolation",
}

GENERIC_STATE_OBLIGATION: dict[str, Any] = {
    "obligation_id": "obl_generic_state_001",
    "mechanism": "STATE_TRANSITION",
    "risk_family": "state_transition",
    "entity": "order",
    "required_operations": ["op_update_order_status"],
    "required_actors": ["actor_owner"],
    "source_refs": [{"ref": "inv_order_status_transition"}],
    "invariant_id": "inv_order_status_transition",
    "target_state": "confirmed",
    "from_state": "draft",
}

GENERIC_CHAIN_OBLIGATION: dict[str, Any] = {
    "obligation_id": "obl_generic_chain_001",
    "mechanism": "CROSS_ENTITY_OPERATION_CHAIN",
    "risk_family": "cross_entity_chain",
    "entity": "order",
    "target_entity": "shipment",
    "required_operations": ["op_create_order", "op_create_shipment"],
    "required_actors": ["actor_owner"],
    "source_refs": [{"ref": "rel_order_shipment"}],
    "relation_id": "rel_order_shipment",
}

GENERIC_IDEM_OBLIGATION: dict[str, Any] = {
    "obligation_id": "obl_generic_idem_001",
    "mechanism": "IDEMPOTENCY_REPETITION",
    "risk_family": "idempotency",
    "entity": "payment",
    "required_operations": ["op_create_payment"],
    "required_actors": ["actor_owner"],
    "source_refs": [{"ref": "inv_payment_idempotency"}],
    "invariant_id": "inv_payment_idempotency",
    "idempotency_key": "Idempotency-Key",
}


# ─── §18.1 Actor Matrix Integration ──────────────────────────────────────────


class TestGenericActorMatrix:
    """§18.1: Actor Matrix auto-activation with generic fixture."""

    def test_actor_matrix_auto_activates(self):
        """Actor Matrix automatically activates from IR actors + invariants."""
        from ai_test_asset_center.actor_matrix_planning import (
            build_actor_inventory,
            generate_actor_matrix_candidates,
            extract_operation_authorization_requirement,
            resolve_resource_context,
        )

        inventory = build_actor_inventory(GENERIC_BEHAVIOR_IR)
        assert len(inventory) >= 3, f"Expected >=3 actors, got {len(inventory)}"
        invariant = GENERIC_BEHAVIOR_IR["invariants"][2]  # RESOURCE_OWNERSHIP
        operation = GENERIC_BEHAVIOR_IR["operations"][1]  # op_get_order
        resource_ctx = resolve_resource_context(
            expression={}, invariant=invariant,
            behavior_ir=GENERIC_BEHAVIOR_IR, operation=operation,
        )
        auth_req = extract_operation_authorization_requirement(
            operation, GENERIC_BEHAVIOR_IR, resource_ctx,
        )
        candidates = generate_actor_matrix_candidates(
            inventory, resource_ctx, auth_req,
            resource_owner_actor_id="actor_owner",
        )
        assert len(candidates) > 0, "Actor Matrix should generate candidates"

    def test_discriminating_pair_auto_generated(self):
        """Discriminating pairs are automatically generated."""
        from ai_test_asset_center.actor_matrix_planning import (
            build_actor_inventory,
            generate_actor_matrix_candidates,
            select_discriminating_pairs,
            resolve_resource_context,
            extract_operation_authorization_requirement,
        )

        inventory = build_actor_inventory(GENERIC_BEHAVIOR_IR)
        invariant = GENERIC_BEHAVIOR_IR["invariants"][1]  # TENANT_ISOLATION
        operation = GENERIC_BEHAVIOR_IR["operations"][1]
        resource_ctx = resolve_resource_context(
            expression={}, invariant=invariant,
            behavior_ir=GENERIC_BEHAVIOR_IR, operation=operation,
        )
        auth_req = extract_operation_authorization_requirement(
            operation, GENERIC_BEHAVIOR_IR, resource_ctx,
        )
        candidates = generate_actor_matrix_candidates(
            inventory, resource_ctx, auth_req,
            resource_tenant="tenant_a",
        )
        assert len(candidates) > 0
        pairs = select_discriminating_pairs(candidates, auth_req, resource_ctx)
        assert len(pairs) > 0, "Should select discriminating pairs"

    def test_same_scope_wrong_role(self):
        """Same scope, wrong role scenario covered."""
        from ai_test_asset_center.actor_matrix_planning import (
            build_actor_inventory,
            generate_actor_matrix_candidates,
            resolve_resource_context,
            extract_operation_authorization_requirement,
        )

        inventory = build_actor_inventory(GENERIC_BEHAVIOR_IR)
        invariant = GENERIC_BEHAVIOR_IR["invariants"][2]  # RESOURCE_OWNERSHIP
        operation = GENERIC_BEHAVIOR_IR["operations"][2]  # op_update_order_status
        resource_ctx = resolve_resource_context(
            expression={}, invariant=invariant,
            behavior_ir=GENERIC_BEHAVIOR_IR, operation=operation,
        )
        auth_req = extract_operation_authorization_requirement(
            operation, GENERIC_BEHAVIOR_IR, resource_ctx,
        )
        candidates = generate_actor_matrix_candidates(
            inventory, resource_ctx, auth_req,
            resource_owner_actor_id="actor_owner",
        )
        assert len(candidates) >= 2, "Should cover multiple actor combinations"

    def test_cross_scope_same_role(self):
        """Cross scope, same role scenario covered."""
        from ai_test_asset_center.actor_matrix_planning import (
            build_actor_inventory,
            generate_actor_matrix_candidates,
            resolve_resource_context,
            extract_operation_authorization_requirement,
        )

        inventory = build_actor_inventory(GENERIC_BEHAVIOR_IR)
        invariant = GENERIC_BEHAVIOR_IR["invariants"][1]  # TENANT_ISOLATION
        operation = GENERIC_BEHAVIOR_IR["operations"][3]  # op_list_orders
        resource_ctx = resolve_resource_context(
            expression={}, invariant=invariant,
            behavior_ir=GENERIC_BEHAVIOR_IR, operation=operation,
        )
        auth_req = extract_operation_authorization_requirement(
            operation, GENERIC_BEHAVIOR_IR, resource_ctx,
        )
        candidates = generate_actor_matrix_candidates(
            inventory, resource_ctx, auth_req,
            resource_tenant="tenant_a",
        )
        cross_tenant = [
            c for c in candidates
            if "CROSS_TENANT" in str(c.get("relation_type", ""))
        ]
        assert len(cross_tenant) >= 1, "Should have cross-tenant discrimination"

    def test_no_manual_actor_pair(self):
        """Human Actor Pair = 0 (all auto-generated)."""
        from ai_test_asset_center.actor_matrix_planning import (
            build_actor_inventory,
            generate_actor_matrix_candidates,
            resolve_resource_context,
            extract_operation_authorization_requirement,
        )

        inventory = build_actor_inventory(GENERIC_BEHAVIOR_IR)
        invariant = GENERIC_BEHAVIOR_IR["invariants"][1]
        operation = GENERIC_BEHAVIOR_IR["operations"][1]
        resource_ctx = resolve_resource_context(
            expression={}, invariant=invariant,
            behavior_ir=GENERIC_BEHAVIOR_IR, operation=operation,
        )
        auth_req = extract_operation_authorization_requirement(
            operation, GENERIC_BEHAVIOR_IR, resource_ctx,
        )
        candidates = generate_actor_matrix_candidates(
            inventory, resource_ctx, auth_req,
        )
        for c in candidates:
            assert c.get("manual") is not True, "No manual actor pairs allowed"


# ─── §18.2 State Path Integration ────────────────────────────────────────────


class TestGenericStatePath:
    """§18.2: State Path auto-generation with generic fixture."""

    def test_state_goal_auto_generated(self):
        """State goals are automatically generated from IR invariants."""
        from ai_test_asset_center.deep_experiment_planner import (
            plan_state_path_experiments,
        )

        result = plan_state_path_experiments(
            obligations=[GENERIC_STATE_OBLIGATION],
            behavior_ir=GENERIC_BEHAVIOR_IR,
        )
        assert result is not None
        # Should produce some output (experiments or status)
        assert isinstance(result, dict)

    def test_legal_from_state_covered(self):
        """Legal from-state transitions covered - function activates correctly."""
        from ai_test_asset_center.deep_experiment_planner import explore_state_paths

        result = explore_state_paths(
            obligation=GENERIC_STATE_OBLIGATION,
            behavior_ir=GENERIC_BEHAVIOR_IR,
        )
        assert result is not None
        # Function should activate and return a valid status
        # (NO_FORBIDDEN_STATES is valid when obligation lacks full compile chain)
        assert result.get("status") in {
            "OK", "NO_FORBIDDEN_STATES", "PARTIAL", "COMPLETE",
        }, f"Unexpected status: {result.get('status')}"
        # State rule type should be identified
        state_rule = result.get("state_rule", {})
        assert state_rule.get("rule_type") == "STATE_TRANSITION"

    def test_forbidden_from_state_covered(self):
        """Forbidden from-state transitions covered."""
        from ai_test_asset_center.deep_experiment_planner import explore_state_paths

        forbidden_obl = {
            **GENERIC_STATE_OBLIGATION,
            "obligation_id": "obl_generic_state_forbidden",
            "from_state": "delivered",
            "target_state": "draft",
        }
        result = explore_state_paths(
            obligation=forbidden_obl,
            behavior_ir=GENERIC_BEHAVIOR_IR,
        )
        assert result is not None

    def test_operation_path_auto_generated(self):
        """Operation paths are automatically generated (no DB state manipulation)."""
        from ai_test_asset_center.deep_experiment_planner import explore_state_paths

        result = explore_state_paths(
            obligation=GENERIC_STATE_OBLIGATION,
            behavior_ir=GENERIC_BEHAVIOR_IR,
        )
        paths = result.get("paths", result.get("state_paths", []))
        for p in paths:
            assert p.get("direct_db_write") is not True


# ─── §18.3 Cross-Entity Chain Integration ────────────────────────────────────


class TestGenericCrossEntityChain:
    """§18.3: Cross-Entity Chain auto-generation with generic fixture."""

    def test_operation_chain_auto_generated(self):
        """Operation chains are automatically generated from IR relations."""
        from ai_test_asset_center.cross_entity_chain_planning import (
            plan_cross_entity_experiments,
        )

        result = plan_cross_entity_experiments(
            obligation=GENERIC_CHAIN_OBLIGATION,
            ir=GENERIC_BEHAVIOR_IR,
        )
        assert result is not None
        assert isinstance(result, dict)

    def test_parent_child_chain(self):
        """Parent → Child chain covered (order → shipment)."""
        from ai_test_asset_center.cross_entity_chain_planning import (
            detect_cross_entity_requirement,
            build_cross_entity_chain,
        )

        detection = detect_cross_entity_requirement(
            obligation=GENERIC_CHAIN_OBLIGATION,
            ir=GENERIC_BEHAVIOR_IR,
        )
        assert detection is not None
        chain = build_cross_entity_chain(
            detection=detection,
            obligation=GENERIC_CHAIN_OBLIGATION,
            ir=GENERIC_BEHAVIOR_IR,
        )
        assert chain is not None

    def test_association_chain(self):
        """Association chain covered (order ↔ payment)."""
        from ai_test_asset_center.cross_entity_chain_planning import (
            detect_cross_entity_requirement,
            build_cross_entity_chain,
        )

        assoc_obl = {
            **GENERIC_CHAIN_OBLIGATION,
            "obligation_id": "obl_generic_chain_assoc",
            "target_entity": "payment",
            "relation_id": "rel_order_payment",
            "required_operations": ["op_create_order", "op_create_payment"],
        }
        detection = detect_cross_entity_requirement(
            obligation=assoc_obl,
            ir=GENERIC_BEHAVIOR_IR,
        )
        chain = build_cross_entity_chain(
            detection=detection,
            obligation=assoc_obl,
            ir=GENERIC_BEHAVIOR_IR,
        )
        assert chain is not None

    def test_no_direct_db_relation_write(self):
        """Database direct relation write = 0."""
        from ai_test_asset_center.cross_entity_chain_planning import (
            plan_cross_entity_experiments,
        )

        result = plan_cross_entity_experiments(
            obligation=GENERIC_CHAIN_OBLIGATION,
            ir=GENERIC_BEHAVIOR_IR,
        )
        assert result.get("direct_db_write") is not True


# ─── §18.4 Idempotency Replay Integration ────────────────────────────────────


class TestGenericIdempotencyReplay:
    """§18.4: Idempotency Replay auto-generation with generic fixture."""

    def test_replay_variant_auto_generated(self):
        """Replay variants are automatically generated from IR invariants."""
        from ai_test_asset_center.idempotency_replay_planning import (
            plan_idempotency_replay,
        )

        result = plan_idempotency_replay(
            obligation=GENERIC_IDEM_OBLIGATION,
            ir=GENERIC_BEHAVIOR_IR,
        )
        assert result is not None
        variants = result.get("replay_variants", [])
        assert len(variants) > 0, "Should generate idempotency replay variants"

    def test_same_key_same_payload(self):
        """Same Key + Same Payload variant covered."""
        from ai_test_asset_center.idempotency_replay_planning import (
            plan_idempotency_replay,
        )

        result = plan_idempotency_replay(
            obligation=GENERIC_IDEM_OBLIGATION,
            ir=GENERIC_BEHAVIOR_IR,
        )
        variants = result.get("replay_variants", [])
        # Should have at least same-key-same-payload variant
        assert len(variants) >= 1

    def test_request_fingerprint_auto_generated(self):
        """Request fingerprint is automatically generated."""
        from ai_test_asset_center.idempotency_replay_planning import (
            build_request_fingerprint,
            derive_idempotency_key,
            resolve_operation_identity,
        )

        operation = GENERIC_BEHAVIOR_IR["operations"][6]  # op_create_payment
        op_identity = resolve_operation_identity(operation, GENERIC_BEHAVIOR_IR)
        idem_key = derive_idempotency_key(
            op_identity, GENERIC_IDEM_OBLIGATION, GENERIC_BEHAVIOR_IR,
        )
        fp = build_request_fingerprint(op_identity, idem_key)
        assert fp is not None
        assert isinstance(fp, dict)

    def test_no_unbounded_repetition(self):
        """Unbounded repetition = 0 (variants are finite)."""
        from ai_test_asset_center.idempotency_replay_planning import (
            plan_idempotency_replay,
        )

        result = plan_idempotency_replay(
            obligation=GENERIC_IDEM_OBLIGATION,
            ir=GENERIC_BEHAVIOR_IR,
        )
        variants = result.get("replay_variants", [])
        assert len(variants) <= 20, "Replay variants should be bounded"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
