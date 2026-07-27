"""V1.5.0 Multi-Step Protocol and integration unit tests.

Covers: multi-step protocol compilation, state chain derivation, sequence
verification protocol, registration into existing registry, and integration
with compiler/executor/finalizer paths.
Industry-neutral: all test data uses generic entity/operation names.
"""
from __future__ import annotations

import pytest

from ai_test_asset_center.multi_step_protocol import (
    TEMPLATE_MULTI_STEP_PROCESS,
    TEMPLATE_STATE_CHAIN_PROCESS,
    TEMPLATE_SEQUENCE_VERIFICATION,
    compile_multi_step_process_protocol,
    compile_state_chain_protocol,
    compile_sequence_verification_protocol,
    register_v150_multi_step_protocols,
)
from ai_test_asset_center.experiment_protocol_registry import (
    resolve_family_protocol,
    registered_family_protocols,
)
from ai_test_asset_center.process_step_observer import install_process_step_surface


# ─── Fixtures ─────────────────────────────────────────────────────────────────


def _envelope_with_steps():
    """Envelope with source-declared process steps."""
    return {
        "risk_family": "process",
        "operation": {"id": "op_submit", "method": "POST", "path": "/api/records"},
        "operation_ref": "op_submit",
        "control_actor_ref": "",
        "treatment_actor_ref": "actor_admin",
        "property_spec": {
            "process_steps": [
                {"step_id": "step_create", "operation_ref": "op_create", "actor_ref": "actor_admin"},
                {"step_id": "step_validate", "operation_ref": "op_validate", "actor_ref": "actor_admin"},
                {"step_id": "step_approve", "operation_ref": "op_approve", "actor_ref": "actor_admin"},
            ],
            "expected_order": ["step_create", "step_validate", "step_approve"],
            "source_refs": [{"source_id": "src_proc_1", "doc_ref": "Business Process §4"}],
        },
        "behavior_ir": {"operations": [], "relations": [], "entities": []},
    }


def _envelope_with_transitions():
    """Envelope relying on state transition derivation."""
    return {
        "risk_family": "state",
        "operation": {"id": "op_create", "method": "POST", "path": "/api/records"},
        "operation_ref": "op_create",
        "control_actor_ref": "",
        "treatment_actor_ref": "actor_admin",
        "property_spec": {},
        "behavior_ir": {
            "operations": [
                {"id": "op_create", "method": "POST", "path": "/api/records"},
                {"id": "op_submit", "method": "POST", "path": "/api/records/{id}/submit"},
                {"id": "op_approve", "method": "POST", "path": "/api/records/{id}/approve"},
            ],
            "relations": [
                {"relation_type": "transitions", "operation_ref": "op_create", "from_ref": "draft", "to_ref": "created"},
                {"relation_type": "transitions", "operation_ref": "op_submit", "from_ref": "created", "to_ref": "submitted"},
                {"relation_type": "transitions", "operation_ref": "op_approve", "from_ref": "submitted", "to_ref": "approved"},
            ],
            "entities": [],
        },
    }


# ─── §19: Multi-Step Protocol Compilation ─────────────────────────────────────


class TestMultiStepProcessProtocol:
    def test_compiles_with_declared_steps(self):
        result = compile_multi_step_process_protocol(_envelope_with_steps())
        assert result["status"] == "COMPILED"
        assert len(result["treatment_plan"]) == 3
        assert result["per_step_evidence"] is True

    def test_step_ids_unique(self):
        result = compile_multi_step_process_protocol(_envelope_with_steps())
        step_ids = [s["step_id"] for s in result["treatment_plan"]]
        assert len(step_ids) == len(set(step_ids))

    def test_step_ids_match_source(self):
        result = compile_multi_step_process_protocol(_envelope_with_steps())
        step_ids = [s["step_id"] for s in result["treatment_plan"]]
        assert step_ids == ["step_create", "step_validate", "step_approve"]

    def test_blocked_without_actor(self):
        env = _envelope_with_steps()
        env["treatment_actor_ref"] = ""
        env["control_actor_ref"] = ""
        result = compile_multi_step_process_protocol(env)
        assert result["status"] == "BLOCKED"
        assert "ACTOR" in result["reason_code"]

    def test_blocked_without_steps(self):
        env = _envelope_with_steps()
        env["property_spec"]["process_steps"] = []
        env["behavior_ir"] = {"operations": [], "relations": [], "entities": []}
        result = compile_multi_step_process_protocol(env)
        assert result["status"] == "BLOCKED"

    def test_blocked_duplicate_step_id(self):
        env = _envelope_with_steps()
        env["property_spec"]["process_steps"][1]["step_id"] = "step_create"  # duplicate
        result = compile_multi_step_process_protocol(env)
        assert result["status"] == "BLOCKED"
        assert "duplicate" in result["detail"]

    def test_cleanup_plan_reverse(self):
        result = compile_multi_step_process_protocol(_envelope_with_steps())
        cleanup = result["cleanup_plan"]
        assert cleanup[0]["step_id"] == "cleanup_step_approve"
        assert cleanup[-1]["step_id"] == "cleanup_step_create"

    def test_expected_order_from_source(self):
        result = compile_multi_step_process_protocol(_envelope_with_steps())
        assert result["expected_order"] == ["step_create", "step_validate", "step_approve"]
        assert result["source_refs"][0]["source_id"] == "src_proc_1"


class TestStateChainProtocol:
    def test_derives_from_transitions(self):
        result = compile_state_chain_protocol(_envelope_with_transitions())
        assert result["status"] == "COMPILED"
        assert len(result["treatment_plan"]) >= 2

    def test_assertion_kind_sequence(self):
        result = compile_state_chain_protocol(_envelope_with_transitions())
        assert result["assertion"]["kind"] == "step_sequence_order"

    def test_follows_chain(self):
        result = compile_state_chain_protocol(_envelope_with_transitions())
        ops = [s["operation_ref"] for s in result["treatment_plan"]]
        assert "op_create" in ops
        assert "op_submit" in ops


class TestSequenceVerificationProtocol:
    def test_compiles(self):
        result = compile_sequence_verification_protocol(_envelope_with_steps())
        assert result["status"] == "COMPILED"
        assert result["assertion"]["kind"] == "step_sequence_order"


# ─── Registration into existing Protocol Registry ─────────────────────────────


class TestProtocolRegistration:
    def test_register_all(self):
        registered = register_v150_multi_step_protocols()
        assert len(registered) >= 2  # at least process + state

    def test_resolvable_after_registration(self):
        register_v150_multi_step_protocols()
        reg = resolve_family_protocol("process", TEMPLATE_MULTI_STEP_PROCESS)
        assert reg is not None
        assert reg["per_step_evidence"] is True

    def test_state_chain_resolvable(self):
        register_v150_multi_step_protocols()
        reg = resolve_family_protocol("state", TEMPLATE_STATE_CHAIN_PROCESS)
        assert reg is not None

    def test_idempotent_registration(self):
        r1 = register_v150_multi_step_protocols()
        r2 = register_v150_multi_step_protocols()
        # Second call should not fail (registry may reject duplicates silently)
        assert isinstance(r2, list)

    def test_registered_in_global_list(self):
        register_v150_multi_step_protocols()
        all_ids = registered_family_protocols()
        assert f"process:{TEMPLATE_MULTI_STEP_PROCESS}" in all_ids


# ─── Integration: Observer + Protocol ─────────────────────────────────────────


class TestObserverProtocolIntegration:
    def test_observer_installed_before_protocol(self):
        """register_v150_multi_step_protocols installs observer first."""
        install_process_step_surface()
        registered = register_v150_multi_step_protocols()
        assert len(registered) >= 2

    def test_sequence_assertion_kind_available(self):
        """After registration, step_sequence_order kind is usable."""
        from ai_test_asset_center.assertion_dsl_base import registered_assertion_kinds
        install_process_step_surface()
        kinds = registered_assertion_kinds()
        assert "step_sequence_order" in kinds
