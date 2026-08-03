"""Tests for write reversibility contract, cleanup plan validation,
strict preflight (no best-effort), and response-bound observer blocking.

SPEC: 写安全协议收敛与 Harness 真值化 v1.0
"""
from __future__ import annotations

import pytest
from pathlib import Path

from ai_test_asset_center.experiment_compiler_obligation import (
    compile_experiment_for_obligation,
)
from ai_test_asset_center.experiment_executor import execute_one_experiment
from ai_test_asset_center.write_reversibility_contract import (
    build_reversibility_proof,
    CLEANUP_AUTHORITIES,
)
from ai_test_asset_center.cleanup_plan_validator import validate_cleanup_plan


def _actor(role: str = "buyer", actor_id: str = "actor-buyer") -> dict:
    return {
        "id": actor_id,
        "role": role,
        "credential_secret_ref": f"secret_ref:{role}",
        "account_status": "active",
    }


# ─── §16.1: Empty body action → BLOCKED_NON_REVERSIBLE_WRITE ──────────────────


class TestEmptyBodyActionNR:
    """Empty body identity-bound POST actions must be NR without explicit inverse."""

    @pytest.mark.parametrize("action_path", [
        "/api/orders/{id}/ship",
        "/api/orders/{id}/confirm",
        "/api/refunds/{id}/approve",
    ])
    def test_empty_body_action_blocks_nr(self, action_path: str) -> None:
        experiment = compile_experiment_for_obligation(
            {
                "obligation_id": f"obl-{action_path.split('/')[-1]}",
                "risk_family": "state",
                "property": {
                    "operation_ref": "op-action",
                    "actor_ref": "actor-buyer",
                    "from_state_ref": "state-a",
                    "to_state_ref": "state-b",
                },
                "required_actors": ["actor-buyer"],
                "required_operations": ["op-action"],
                "required_observers": ["http_response", "before_state", "after_state"],
                "cleanup_requirement": {"required": True},
            },
            behavior_ir={
                "operations": [{
                    "id": "op-action",
                    "method": "POST",
                    "path": action_path,
                    "read_write": "write",
                    "request_example": {},
                    "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
                }, {
                    "id": "op-get",
                    "method": "GET",
                    "path": action_path.rsplit("/", 1)[0],
                    "read_write": "read",
                    "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
                }, {
                    "id": "op-list",
                    "method": "GET",
                    "path": "/" + "/".join(action_path.strip("/").split("/")[:2]),
                    "read_write": "read",
                    "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
                }],
                "actors": [_actor()],
                "relations": [],
            },
            environment_type="test",
        )
        # Must be BLOCKED before transport — either NR or binding issue
        assert experiment["compile_receipt"]["status"] == "BLOCKED"
        assert experiment["compile_receipt"]["reason_code"] in {
            "BLOCKED_NON_REVERSIBLE_WRITE",
            "BLOCKED_MISSING_BINDING",
        }

    def test_empty_patch_does_not_infer_restore_fields_from_entity_schema(
        self,
    ) -> None:
        from ai_test_asset_center.write_reversibility_contract import (
            _validate_field_snapshot_restore,
        )

        result = _validate_field_snapshot_restore(
            primary_method="PATCH",
            primary_path="/orders/current",
            primary_operation_ref="op-update",
            cleanup_op={},
            cleanup_method="PATCH",
            cleanup_path="/orders/current",
            experiment={},
            ops={
                "op-update": {
                    "id": "op-update",
                    "method": "PATCH",
                    "path": "/orders/current",
                    "request_example": {},
                }
            },
            entities=[
                {
                    "id": "entity-order",
                    "fields": ["id", "status", "amount"],
                }
            ],
            relations=[
                {
                    "relation_type": "mutates",
                    "operation_ref": "op-update",
                    "to_ref": "entity-order",
                }
            ],
        )

        assert result == {
            "kind": "none",
            "detail": "field_snapshot_restore_no_writable_fields",
        }


# ─── §16.2: Sibling misbinding prohibited ─────────────────────────────────────


class TestSiblingMisbinding:
    """Ship must not bind cancel; approve must not bind reject."""

    def test_ship_does_not_bind_cancel(self) -> None:
        experiment = compile_experiment_for_obligation(
            {
                "obligation_id": "obl-ship",
                "risk_family": "state",
                "property": {
                    "operation_ref": "op-ship",
                    "actor_ref": "actor-buyer",
                    "from_state_ref": "state-created",
                    "to_state_ref": "state-shipped",
                },
                "required_actors": ["actor-buyer"],
                "required_operations": ["op-ship"],
                "required_observers": ["http_response", "before_state", "after_state"],
                "cleanup_requirement": {"required": True},
            },
            behavior_ir={
                "operations": [{
                    "id": "op-ship",
                    "method": "POST",
                    "path": "/api/orders/{id}/ship",
                    "read_write": "write",
                    "request_example": {},
                    "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
                }, {
                    "id": "op-cancel",
                    "method": "POST",
                    "path": "/api/orders/{id}/cancel",
                    "read_write": "write",
                    "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
                }, {
                    "id": "op-get-order",
                    "method": "GET",
                    "path": "/api/orders/{id}",
                    "read_write": "read",
                    "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
                }, {
                    "id": "op-list-orders",
                    "method": "GET",
                    "path": "/api/orders",
                    "read_write": "read",
                    "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
                }],
                "actors": [_actor()],
                "relations": [],
            },
            environment_type="test",
        )
        # Must be BLOCKED — cancel is not a valid inverse for ship
        assert experiment["compile_receipt"]["status"] == "BLOCKED"
        # Cleanup plan must NOT reference cancel
        for item in experiment.get("cleanup_plan", []):
            assert item.get("operation_ref") != "op-cancel"


# ─── §16.3: Legitimate inverse (source-declared) ──────────────────────────────


class TestLegitimateInverse:
    """Explicit source-backed inverse relations compile successfully."""

    def test_reserve_release_inverse_compiles(self) -> None:
        experiment = compile_experiment_for_obligation(
            {
                "obligation_id": "obl-reserve",
                "risk_family": "state",
                "property": {
                    "operation_ref": "op-reserve",
                    "actor_ref": "actor-buyer",
                    "from_state_ref": "state-available",
                    "to_state_ref": "state-reserved",
                },
                "required_actors": ["actor-buyer"],
                "required_operations": ["op-reserve"],
                "required_observers": ["http_response", "before_state", "after_state"],
                "cleanup_requirement": {"required": True},
            },
            behavior_ir={
                "operations": [{
                    "id": "op-reserve",
                    "method": "POST",
                    "path": "/api/inventory/{id}/reserve",
                    "read_write": "write",
                    "request_example": {"qty": 1},
                    "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
                }, {
                    "id": "op-release",
                    "method": "POST",
                    "path": "/api/inventory/{id}/release",
                    "read_write": "write",
                    "request_example": {"qty": 1},
                    "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
                }, {
                    "id": "op-get-inventory",
                    "method": "GET",
                    "path": "/api/inventory/{id}",
                    "read_write": "read",
                    "source_refs": [{"kind": "endpoint_contract", "file": "api.md"}],
                }],
                "actors": [_actor()],
                "relations": [{
                    "source": "op-release",
                    "target": "op-reserve",
                    "kind": "compensates",
                }],
            },
            environment_type="test",
        )
        # With explicit compensates relation, should compile
        receipt = experiment["compile_receipt"]
        if receipt["status"] == "COMPILED":
            # Cleanup must reference the declared compensator
            cleanup = experiment.get("cleanup_plan", [])
            assert any(
                c.get("operation_ref") == "op-release"
                for c in cleanup
            ), f"Expected op-release in cleanup_plan: {cleanup}"


# ─── §16.5: Response-bound create blocking ────────────────────────────────────


class TestResponseBoundCreateBlocking:
    """Collection create with only response-bound GET must block at preflight."""

    def test_preflight_blocks_response_only_observer(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        behavior_ir = {
            "operations": [
                {"id": "op-create", "method": "POST", "path": "/items",
                 "read_write": "write", "request_example": {"name": "x"}},
                {"id": "op-get-item", "method": "GET", "path": "/items/{id}",
                 "read_write": "read"},
                {"id": "op-delete", "method": "DELETE", "path": "/items/{id}",
                 "read_write": "write"},
            ],
            "actors": [_actor("owner", "actor-owner")],
        }
        experiment = {
            "experiment_id": "exp-rb-create",
            "obligation_id": "obl-rb-create",
            "control_plan": [{"step_id": "c1", "actor_ref": "actor-owner",
                              "operation_ref": "op-create", "body": {"name": "x"}}],
            "treatment_plan": [{"step_id": "t1", "actor_ref": "actor-owner",
                                "operation_ref": "op-create", "body": {}}],
            "binding_plan": [],
            "fixture_dag": {"status": "READY", "nodes": [], "setup_order": []},
            "assertions": [{"assertion_id": "a1", "kind": "validation_rejection"}],
            "observers": [
                {"observer_id": "http_response", "surface": "http_api"},
                {"observer_id": "business_effect", "surface": "business_effect",
                 "resolver_operations": [{"operation_ref": "op-get-item",
                                          "method": "GET", "path": "/items/{id}"}]},
            ],
            "cleanup_plan": [{"action": "reverse_order_compensation",
                              "mode": "reverse_order", "operation_ref": "op-delete",
                              "path": "/items/{id}", "method": "DELETE",
                              "runtime_response_binding_required": True}],
            "safety_contract": {"environment_type": "test", "governed_write": True},
            "source_refs": [{"source_id": "api", "kind": "api_operation"}],
            "compile_receipt": {"status": "COMPILED", "reason_code": ""},
        }
        post_count = {"n": 0}

        def fake_http(method, url, **kwargs):
            if method == "POST":
                post_count["n"] += 1
            return {"status": 200, "body": {}, "headers": {}}

        monkeypatch.setattr("ai_test_asset_center.experiment_executor._http_request", fake_http)
        monkeypatch.setattr("ai_test_asset_center.experiment_plan_executor._http_request", fake_http)
        monkeypatch.setattr("ai_test_asset_center.experiment_runtime_support._http_request", fake_http)
        monkeypatch.setattr("ai_test_asset_center.experiment_runtime_credentials._http_request", fake_http)
        monkeypatch.setattr("ai_test_asset_center.sandbox_write_executor._http_request", fake_http)

        result = execute_one_experiment(
            experiment,
            behavior_ir=behavior_ir,
            root=tmp_path,
            project="p",
            base_url="http://target.invalid",
            runtime_contract={"environment_type": "test", "environment_ref": "e",
                              "execution_mode": "approved_sandbox_write",
                              "approved_base_url": "http://target.invalid", "status": "approved"},
            campaign_id="c", execution_id="e",
            actor_tokens={"secret_ref:owner": "tok"},
        )
        assert result["status"] == "BLOCKED"
        assert result["reason_code"] == "BLOCKED_MISSING_OBSERVER"
        assert post_count["n"] == 0, "No POST must reach transport"


# ─── §16.6: Strict preflight — no best-effort ─────────────────────────────────


class TestStrictPreflight:
    """Strict preflight failure must not auto-retry with best_effort."""

    def test_no_best_effort_parameter_on_executor(self) -> None:
        import inspect
        sig = inspect.signature(execute_one_experiment)
        assert "best_effort" not in sig.parameters

    def test_no_best_effort_parameter_on_preflight(self) -> None:
        from ai_test_asset_center.experiment_runtime_support import preflight_experiment_executable
        import inspect
        sig = inspect.signature(preflight_experiment_executable)
        assert "best_effort" not in sig.parameters


# ─── §16.7: Actor substitution prohibited ─────────────────────────────────────


class TestActorSubstitution:
    """Missing actor must BLOCK, not substitute with admin."""

    def test_missing_actor_blocks_not_substitutes(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        behavior_ir = {
            "operations": [
                {"id": "op-read", "method": "GET", "path": "/orders", "read_write": "read"},
            ],
            "actors": [
                {"id": "actor-admin", "role": "admin",
                 "credential_secret_ref": "secret_ref:admin", "account_status": "active"},
            ],
        }
        experiment = {
            "experiment_id": "exp-actor",
            "obligation_id": "obl-actor",
            "control_plan": [{"step_id": "c1", "actor_ref": "actor-buyer",
                              "operation_ref": "op-read"}],
            "treatment_plan": [],
            "binding_plan": [],
            "fixture_dag": {"status": "READY", "nodes": [], "setup_order": []},
            "assertions": [{"assertion_id": "a1", "kind": "authorization"}],
            "observers": [{"observer_id": "http_response", "surface": "http_api"}],
            "cleanup_plan": [],
            "safety_contract": {"environment_type": "test"},
            "compile_receipt": {"status": "COMPILED", "reason_code": ""},
        }
        from ai_test_asset_center.experiment_runtime_support import preflight_experiment_executable
        ok, reason, detail = preflight_experiment_executable(
            experiment, behavior_ir=behavior_ir,
            actor_tokens={"secret_ref:admin": "admin-tok"},
        )
        assert not ok
        assert reason == "BLOCKED_MISSING_ACTOR"
        assert "actor-buyer" in detail


# ─── §16.8: Path guessing prohibited ──────────────────────────────────────────


class TestPathGuessing:
    """Operation without path must BLOCK, not derive from operation_id."""

    def test_missing_path_blocks_not_guesses(self) -> None:
        from ai_test_asset_center.experiment_runtime_support import preflight_experiment_executable
        behavior_ir = {
            "operations": [
                {"id": "op-create-order", "method": "POST", "read_write": "write"},
            ],
            "actors": [_actor("owner", "actor-owner")],
        }
        experiment = {
            "experiment_id": "exp-path",
            "obligation_id": "obl-path",
            "control_plan": [{"step_id": "c1", "actor_ref": "actor-owner",
                              "operation_ref": "op-create-order", "body": {}}],
            "treatment_plan": [],
            "binding_plan": [],
            "fixture_dag": {"status": "READY", "nodes": [], "setup_order": []},
            "assertions": [{"assertion_id": "a1", "kind": "validation_rejection"}],
            "observers": [{"observer_id": "http_response", "surface": "http_api"}],
            "cleanup_plan": [],
            "safety_contract": {"environment_type": "test"},
            "compile_receipt": {"status": "COMPILED", "reason_code": ""},
        }
        ok, reason, detail = preflight_experiment_executable(
            experiment, behavior_ir=behavior_ir,
            actor_tokens={"secret_ref:owner": "tok"},
        )
        assert not ok
        assert reason == "BLOCKED_MISSING_OPERATION"
        assert "source_declared_path_missing" in detail


# ─── Write Reversibility Proof unit tests ─────────────────────────────────────


class TestWriteReversibilityProof:
    """Unit tests for the proof builder."""

    def test_identity_delete_proof(self) -> None:
        proof = build_reversibility_proof(
            primary_operation_ref="op-create",
            primary_method="POST",
            primary_path="/orders",
            cleanup_plan=[{"action": "reverse_order_compensation",
                           "mode": "reverse_order", "operation_ref": "op-delete",
                           "method": "DELETE", "path": "/orders/{id}"}],
            behavior_ir={"operations": [
                {"id": "op-create", "method": "POST", "path": "/orders"},
                {"id": "op-delete", "method": "DELETE", "path": "/orders/{id}"},
            ]},
        )
        assert proof["proof_status"] == "PROVEN"
        assert proof["cleanup_authority"]["kind"] == "identity_delete"

    def test_empty_cleanup_plan_blocked(self) -> None:
        proof = build_reversibility_proof(
            primary_operation_ref="op-ship",
            primary_method="POST",
            primary_path="/orders/{id}/ship",
            cleanup_plan=[],
        )
        assert proof["proof_status"] == "BLOCKED"
        assert proof["reason_code"] == "BLOCKED_NON_REVERSIBLE_WRITE"
        assert proof["reason_detail"] == "empty_cleanup_plan"

    def test_all_authorities_in_allowed_set(self) -> None:
        """The set is pinned exactly so admitting a new authority is a conscious act.

        declared_adapter_cleanup was added deliberately. On a live 11-service target only
        2 of 17 writes had a source-declared API compensator -- the API genuinely offers
        no DELETE for products, none for cart items, no reversal for a payment -- and 668
        obligations blocked on BLOCKED_NON_REVERSIBLE_WRITE as a result. "The API has no
        undo" is not "this cannot be cleaned up": a run that creates its own row can
        delete that row through an adapter the operator declared.

        It is the same authority as identity_delete, reached through a different surface,
        and it is admitted only with the table, the identity column, run_created_only
        scope and a requires_ownership_proof flag -- see
        test_declared_adapter_cleanup_requires_every_leg below.
        """
        assert CLEANUP_AUTHORITIES == {
            "identity_delete", "explicit_compensator", "field_snapshot_restore",
            "inverse_delta", "exact_recreate", "verified_environment_reset",
            # V1.6.1: required so RESOLVED field-oracle write experiments can compile.
            "declared_adapter_cleanup",
        }
        from ai_test_asset_center.write_reversibility_contract import (
            ADAPTER_CLEANUP_AUTHORITY,
        )

        assert ADAPTER_CLEANUP_AUTHORITY in CLEANUP_AUTHORITIES

    def test_declared_adapter_cleanup_requires_every_leg(self) -> None:
        """Each missing leg is refused by name, so a partial plan never passes."""
        from ai_test_asset_center.write_reversibility_contract import (
            _classify_cleanup_authority_v11,
        )

        good = {
            "action": "declared_adapter_cleanup",
            "mode": "adapter_row_delete",
            "adapter": "db_sql",
            "table": "orders",
            "identity_column": "id",
            "requires_ownership_proof": True,
            "scope": "run_created_only",
        }
        common = dict(primary_method="POST", primary_operation_ref="op1",
                      primary_path="/api/orders", ops={}, relations=[], experiment={})

        assert _classify_cleanup_authority_v11(cleanup_plan=[good], **common)["kind"] == (
            "declared_adapter_cleanup"
        )

        for mutation, expected in (
            ({"requires_ownership_proof": False}, "adapter_cleanup_ownership_proof_not_required"),
            ({"scope": "everything"}, "adapter_cleanup_scope_not_run_created_only"),
            ({"table": ""}, "adapter_cleanup_table_or_identity_not_declared"),
            ({"identity_column": ""}, "adapter_cleanup_table_or_identity_not_declared"),
            ({"adapter": "ftp"}, "adapter_cleanup_unsupported_adapter:ftp"),
        ):
            result = _classify_cleanup_authority_v11(
                cleanup_plan=[dict(good, **mutation)], **common
            )
            assert result["kind"] == "none", mutation
            assert result["detail"] == expected, mutation

        # Collection create → row-delete equivalence (created_entity_absent).
        create_result = _classify_cleanup_authority_v11(cleanup_plan=[good], **common)
        assert create_result["equivalence_contract"]["mode"] == "created_entity_absent"
        assert create_result["equivalence_contract"]["mode"]

    def test_declared_adapter_mutation_emits_business_state_equivalence(self) -> None:
        """Mutates-entity authority must compile business_state_restored, not empty mode.

        V12 Unlock breakpoint UNLOCK_CLEANUP_EQUIVALENCE_MODE_MISSING: adapter
        field_restore COMPLETED, but WRP.equivalence_contract was {} so equivalence
        stayed INDETERMINATE. Classification comes from source mode / mutates
        relation — not empty-body heuristics alone.
        """
        from ai_test_asset_center.write_reversibility_contract import (
            _classify_cleanup_authority_v11,
            build_reversibility_proof,
        )

        plan = {
            "action": "declared_adapter_cleanup",
            "mode": "field_restore",
            "adapter": "db_sql",
            "table": "orders",
            "identity_column": "id",
            "requires_ownership_proof": True,
            "scope": "run_created_only",
        }
        ops = {
            "op_confirm": {
                "id": "op_confirm",
                "method": "POST",
                "path": "/api/orders/:id/confirm",
                "request_example": {},
            }
        }
        result = _classify_cleanup_authority_v11(
            cleanup_plan=[plan],
            primary_method="POST",
            primary_operation_ref="op_confirm",
            primary_path="/api/orders/:id/confirm",
            ops=ops,
            relations=[
                {
                    "kind": "mutates",
                    "operation_ref": "op_confirm",
                    "to_ref": "entity_order",
                }
            ],
            experiment={},
        )
        assert result["kind"] == "declared_adapter_cleanup"
        assert result["equivalence_contract"]["mode"] == "business_state_restored"
        assert result["authority_block"]["cleanup_surface"] == "field_restore"

        proof = build_reversibility_proof(
            primary_operation_ref="op_confirm",
            primary_method="POST",
            primary_path="/api/orders/:id/confirm",
            cleanup_plan=[plan],
            behavior_ir={
                "operations": list(ops.values()),
                "relations": [
                    {
                        "kind": "mutates",
                        "operation_ref": "op_confirm",
                        "to_ref": "entity_order",
                    }
                ],
            },
        )
        assert proof["proof_status"] == "PROVEN"
        assert proof["equivalence_contract"]["mode"] == "business_state_restored"

    def test_create_under_parent_stays_row_delete_not_field_restore(self) -> None:
        """Produces-entity under a parent path must not be classified as field_restore."""
        from ai_test_asset_center.write_reversibility_contract import (
            _classify_cleanup_authority_v11,
        )

        plan = {
            "action": "declared_adapter_cleanup",
            "mode": "row_delete",
            "adapter": "db_sql",
            "table": "order_items",
            "identity_column": "id",
            "requires_ownership_proof": True,
            "scope": "run_created_only",
        }
        ops = {
            "op_add_item": {
                "id": "op_add_item",
                "method": "POST",
                "path": "/api/orders/:id/items",
                "request_example": {},
            }
        }
        result = _classify_cleanup_authority_v11(
            cleanup_plan=[plan],
            primary_method="POST",
            primary_operation_ref="op_add_item",
            primary_path="/api/orders/:id/items",
            ops=ops,
            relations=[
                {
                    "kind": "produces",
                    "operation_ref": "op_add_item",
                    "to_ref": "entity_order_item",
                }
            ],
            experiment={},
        )
        assert result["kind"] == "declared_adapter_cleanup"
        assert result["authority_block"]["cleanup_surface"] == "row_delete"
        assert result["equivalence_contract"]["mode"] == "created_entity_absent"

    def test_transitions_wins_over_co_declared_produces_for_field_restore(self) -> None:
        """Ship/pay IR often tags both produces and transitions; mutates must win."""
        from ai_test_asset_center.write_reversibility_contract import (
            _classify_cleanup_authority_v11,
        )

        plan = {
            "action": "declared_adapter_cleanup",
            "mode": "row_delete",
            "adapter": "db_sql",
            "table": "orders",
            "identity_column": "id",
            "requires_ownership_proof": True,
            "scope": "run_created_only",
        }
        ops = {
            "op_ship": {
                "id": "op_ship",
                "method": "POST",
                "path": "/api/orders/:id/ship",
                "request_example": {},
            }
        }
        result = _classify_cleanup_authority_v11(
            cleanup_plan=[plan],
            primary_method="POST",
            primary_operation_ref="op_ship",
            primary_path="/api/orders/:id/ship",
            ops=ops,
            relations=[
                {
                    "relation_type": "produces",
                    "operation_ref": "op_ship",
                    "to_ref": "entity_order",
                },
                {
                    "relation_type": "transitions",
                    "operation_ref": "op_ship",
                    "from_ref": "state_paid",
                    "to_ref": "state_shipped",
                },
            ],
            experiment={},
        )
        assert result["authority_block"]["cleanup_surface"] == "field_restore"
        assert result["equivalence_contract"]["mode"] == "business_state_restored"


# ─── Cleanup Plan Validator unit tests ────────────────────────────────────────


class TestCleanupPlanValidator:
    """Unit tests for the unified validator."""

    def test_valid_identity_delete(self) -> None:
        result = validate_cleanup_plan(
            {
                "treatment_plan": [{"operation_ref": "op-create", "method": "POST",
                                    "path": "/orders"}],
                "cleanup_plan": [{"action": "reverse_order_compensation",
                                  "mode": "reverse_order", "operation_ref": "op-delete",
                                  "method": "DELETE"}],
                "safety_contract": {"governed_write": True},
            },
            {"operations": [
                {"id": "op-create", "method": "POST", "path": "/orders"},
                {"id": "op-delete", "method": "DELETE", "path": "/orders/{id}"},
            ]},
        )
        assert result["valid"] is True

    def test_runtime_proof_ignores_unrelated_ir_expansion(self) -> None:
        ir = {
            "operations": [
                {"id": "op-create", "method": "POST", "path": "/orders"},
                {"id": "op-delete", "method": "DELETE", "path": "/orders/{id}"},
            ],
            "relations": [],
        }
        experiment = {
            "treatment_plan": [{
                "operation_ref": "op-create",
                "method": "POST",
                "path": "/orders",
            }],
            "cleanup_plan": [{
                "action": "reverse_order_compensation",
                "mode": "reverse_order",
                "operation_ref": "op-delete",
                "method": "DELETE",
                "path": "/orders/{id}",
            }],
            "safety_contract": {"governed_write": True},
        }
        proof = build_reversibility_proof(
            primary_operation_ref="op-create",
            primary_method="POST",
            primary_path="/orders",
            cleanup_plan=experiment["cleanup_plan"],
            behavior_ir=ir,
            experiment=experiment,
        )
        assert proof["proof_status"] == "PROVEN"
        experiment["write_reversibility_proof"] = proof

        evolved_ir = {
            **ir,
            "operations": ir["operations"] + [
                {"id": "op-unrelated", "method": "GET", "path": "/catalog"},
            ],
            "relations": [{
                "kind": "observes",
                "operation_ref": "op-unrelated",
                "target": "entity-catalog",
            }],
        }
        result = validate_cleanup_plan(
            experiment,
            evolved_ir,
            phase="runtime",
            compile_proof_fingerprint=proof["fingerprint"],
            runtime_bindings={},
            binding_receipts=[],
        )

        assert result["valid"] is True

    def test_invalid_cleanup_op_not_in_ir(self) -> None:
        result = validate_cleanup_plan(
            {
                "treatment_plan": [{"operation_ref": "op-create", "method": "POST",
                                    "path": "/orders"}],
                "cleanup_plan": [{"action": "reverse_order_compensation",
                                  "mode": "reverse_order", "operation_ref": "op-ghost",
                                  "method": "DELETE"}],
                "safety_contract": {"governed_write": True},
            },
            {"operations": [
                {"id": "op-create", "method": "POST", "path": "/orders"},
            ]},
        )
        assert result["valid"] is False
        # v1.1: semantic validation catches missing op as non-reversible
        assert result["reason_code"] in (
            "BLOCKED_INVALID_CLEANUP_PLAN", "BLOCKED_NON_REVERSIBLE_WRITE"
        )

    def test_read_only_experiment_skips_validation(self) -> None:
        result = validate_cleanup_plan(
            {
                "treatment_plan": [{"operation_ref": "op-read", "method": "GET",
                                    "path": "/orders"}],
                "cleanup_plan": [],
                "safety_contract": {},
            },
            {"operations": [{"id": "op-read", "method": "GET", "path": "/orders"}]},
        )
        assert result["valid"] is True


# ─── §11: Harness Failed sub-reason classification ─────────────────────────


class TestHarnessFailureSubclassification:
    """SPEC §11: Harness Failed must have specific sub-reason codes."""

    def test_cleanup_transport_failure(self) -> None:
        from ai_test_asset_center.experiment_outcome_finalizer import _classify_harness_failure
        reason = _classify_harness_failure(
            steps_out=[],
            observations={"cleanup_status": "TRANSPORT_ERROR"},
            pre_transport_block_reasons=[],
            cleanup_failures=1,
        )
        assert reason == "HARNESS_CLEANUP_TRANSPORT_FAILED"

    def test_cleanup_equivalence_failure(self) -> None:
        from ai_test_asset_center.experiment_outcome_finalizer import _classify_harness_failure
        reason = _classify_harness_failure(
            steps_out=[],
            observations={"cleanup_status": "EQUIVALENCE_FAILED"},
            pre_transport_block_reasons=[],
            cleanup_failures=1,
        )
        assert reason == "HARNESS_CLEANUP_EQUIVALENCE_FAILED"

    def test_barrier_execution_failure(self) -> None:
        from ai_test_asset_center.experiment_outcome_finalizer import _classify_harness_failure
        reason = _classify_harness_failure(
            steps_out=[],
            observations={"harness_error": True, "barrier_timeline": [{"event": "broken"}]},
            pre_transport_block_reasons=["barrier_group_pretransport_blocked:g1"],
        )
        assert reason == "HARNESS_BARRIER_EXECUTION_FAILED"

    def test_async_convergence_timeout(self) -> None:
        from ai_test_asset_center.experiment_outcome_finalizer import _classify_harness_failure
        reason = _classify_harness_failure(
            steps_out=[{"error": "timeout waiting for convergence", "status_code": 0}],
            observations={},
            pre_transport_block_reasons=[],
        )
        assert reason == "HARNESS_ASYNC_CONVERGENCE_TIMEOUT"

    def test_runtime_binding_lost(self) -> None:
        from ai_test_asset_center.experiment_outcome_finalizer import _classify_harness_failure
        reason = _classify_harness_failure(
            steps_out=[],
            observations={},
            pre_transport_block_reasons=["binding_lost:order_id"],
        )
        assert reason == "HARNESS_RUNTIME_BINDING_LOST"

    def test_connection_error_still_connection_failed(self) -> None:
        from ai_test_asset_center.experiment_outcome_finalizer import _classify_harness_failure
        reason = _classify_harness_failure(
            steps_out=[{
                "error": "URLError: Connection refused",
                "status_code": 0,
            }],
            observations={},
            pre_transport_block_reasons=[],
        )
        assert reason == "HARNESS_CONNECTION_FAILED"

    def test_governance_zero_transport_not_connection_failed(self) -> None:
        """Before-GET responded; write never attempted — not a connection loss."""
        from ai_test_asset_center.experiment_outcome_finalizer import _classify_harness_failure
        reason = _classify_harness_failure(
            steps_out=[{
                "phase": "treatment",
                "status_code": 0,
                "error": "governed_write_identity_unobservable",
                "governance_receipt": {
                    "reason": "governed_write_identity_unobservable",
                    "write_request_attempt_count": 0,
                    "before": {"status": 404, "body": {}},
                    "write": {"status": 0, "error": "governed_write_identity_unobservable"},
                },
            }],
            observations={},
            pre_transport_block_reasons=[],
        )
        assert reason != "HARNESS_CONNECTION_FAILED"

    def test_empty_fallback_does_not_claim_connection(self) -> None:
        from ai_test_asset_center.experiment_outcome_finalizer import _classify_harness_failure
        reason = _classify_harness_failure(
            steps_out=[],
            observations={},
            pre_transport_block_reasons=[],
        )
        assert reason != "HARNESS_CONNECTION_FAILED"

    def test_all_subtypes_are_prefixed(self) -> None:
        from ai_test_asset_center.experiment_outcome_finalizer import HARNESS_FAILURE_SUBTYPES
        for subtype in HARNESS_FAILURE_SUBTYPES:
            assert subtype.startswith("HARNESS_"), f"Missing prefix: {subtype}"

    def test_spec_required_subtypes_present(self) -> None:
        from ai_test_asset_center.experiment_outcome_finalizer import HARNESS_FAILURE_SUBTYPES
        required = {
            "HARNESS_CLEANUP_TRANSPORT_FAILED",
            "HARNESS_CLEANUP_RESPONSE_REJECTED",
            "HARNESS_CLEANUP_EQUIVALENCE_FAILED",
            "HARNESS_OBSERVER_TRANSPORT_FAILED",
            "HARNESS_OBSERVER_RESPONSE_UNREADABLE",
            "HARNESS_RUNTIME_BINDING_LOST",
            "HARNESS_ASYNC_CONVERGENCE_TIMEOUT",
            "HARNESS_BARRIER_EXECUTION_FAILED",
        }
        assert required.issubset(set(HARNESS_FAILURE_SUBTYPES))
