"""Multi-step protocol graph-authority regression tests.

The protocol registry remains the only registry.  These tests pin the root-cause
rules: source graph edges define order, nonlinear/cross-system graphs fail
visibly until the existing executor can schedule them, and cleanup is emitted
only from explicit compensation declarations.
"""
from __future__ import annotations

from ai_test_asset_center.multi_step_protocol import (
    MULTI_STEP_GRAPH_RUNTIME_NOT_AVAILABLE,
    MULTI_STEP_PROCESS_GRAPH_AMBIGUOUS,
    TEMPLATE_MULTI_STEP_PROCESS,
    TEMPLATE_SEQUENCE_VERIFICATION,
    TEMPLATE_STATE_CHAIN_PROCESS,
    compile_multi_step_process_protocol,
    compile_sequence_verification_protocol,
    compile_state_chain_protocol,
    register_v150_multi_step_protocols,
)
from ai_test_asset_center.experiment_protocol_registry import (
    registered_family_protocols,
    resolve_family_protocol,
)
from ai_test_asset_center.process_step_observer import install_process_step_surface


def _envelope_with_steps():
    return {
        "risk_family": "process",
        "operation": {"id": "op_submit", "method": "POST", "path": "/api/records"},
        "operation_ref": "op_submit",
        "control_actor_ref": "",
        "treatment_actor_ref": "actor_admin",
        "property_spec": {
            "process_steps": [
                {
                    "step_id": "step_create",
                    "operation_ref": "op_create",
                    "cleanup_operation_ref": "op_delete_created",
                    "actor_ref": "actor_admin",
                },
                {
                    "step_id": "step_validate",
                    "operation_ref": "op_validate",
                    "cleanup_operation_ref": "op_revert_validation",
                    "actor_ref": "actor_admin",
                },
                {
                    "step_id": "step_approve",
                    "operation_ref": "op_approve",
                    "cleanup_operation_ref": "op_revoke_approval",
                    "actor_ref": "actor_admin",
                },
            ],
            "expected_order": ["step_create", "step_validate", "step_approve"],
            "source_refs": [{"source_id": "src_proc_1", "doc_ref": "Business Process §4"}],
        },
        "behavior_ir": {"operations": [], "relations": [], "entities": []},
    }


def _envelope_with_transitions():
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
                {
                    "relation_id": "rel_create",
                    "relation_type": "transitions",
                    "operation_ref": "op_create",
                    "from_ref": "draft",
                    "to_ref": "created",
                },
                {
                    "relation_id": "rel_submit",
                    "relation_type": "transitions",
                    "operation_ref": "op_submit",
                    "from_ref": "created",
                    "to_ref": "submitted",
                },
                {
                    "relation_id": "rel_approve",
                    "relation_type": "transitions",
                    "operation_ref": "op_approve",
                    "from_ref": "submitted",
                    "to_ref": "approved",
                },
            ],
            "entities": [],
        },
    }


class TestMultiStepProcessProtocol:
    def test_compiles_with_declared_steps(self):
        result = compile_multi_step_process_protocol(_envelope_with_steps())
        assert result["status"] == "COMPILED"
        assert len(result["treatment_plan"]) == 3
        assert result["per_step_evidence"] is True
        assert result["execution_graph"]["topological_order"] == [
            "step_create",
            "step_validate",
            "step_approve",
        ]

    def test_step_ids_unique(self):
        result = compile_multi_step_process_protocol(_envelope_with_steps())
        step_ids = [step["step_id"] for step in result["treatment_plan"]]
        assert len(step_ids) == len(set(step_ids))

    def test_step_ids_match_source(self):
        result = compile_multi_step_process_protocol(_envelope_with_steps())
        step_ids = [step["step_id"] for step in result["treatment_plan"]]
        assert step_ids == ["step_create", "step_validate", "step_approve"]

    def test_blocked_without_actor(self):
        envelope = _envelope_with_steps()
        envelope["treatment_actor_ref"] = ""
        envelope["control_actor_ref"] = ""
        result = compile_multi_step_process_protocol(envelope)
        assert result["status"] == "BLOCKED"
        assert "ACTOR" in result["reason_code"]

    def test_blocked_without_steps(self):
        envelope = _envelope_with_steps()
        envelope["property_spec"]["process_steps"] = []
        envelope["behavior_ir"] = {"operations": [], "relations": [], "entities": []}
        result = compile_multi_step_process_protocol(envelope)
        assert result["status"] == "BLOCKED"

    def test_blocked_duplicate_step_id(self):
        envelope = _envelope_with_steps()
        envelope["property_spec"]["process_steps"][1]["step_id"] = "step_create"
        result = compile_multi_step_process_protocol(envelope)
        assert result["status"] == "BLOCKED"
        assert "duplicate" in result["detail"]

    def test_cleanup_uses_explicit_compensation_in_reverse_graph_order(self):
        result = compile_multi_step_process_protocol(_envelope_with_steps())
        cleanup = result["cleanup_plan"]
        assert [row["source_step_id"] for row in cleanup] == [
            "step_approve",
            "step_validate",
            "step_create",
        ]
        assert [row["operation_ref"] for row in cleanup] == [
            "op_revoke_approval",
            "op_revert_validation",
            "op_delete_created",
        ]
        assert all(row["source_declared"] is True for row in cleanup)

    def test_missing_compensation_does_not_reuse_original_write(self):
        envelope = _envelope_with_steps()
        for step in envelope["property_spec"]["process_steps"]:
            step.pop("cleanup_operation_ref", None)
        result = compile_multi_step_process_protocol(envelope)
        assert result["status"] == "COMPILED"
        assert result["cleanup_plan"] == []

    def test_expected_order_from_source(self):
        result = compile_multi_step_process_protocol(_envelope_with_steps())
        assert result["expected_order"] == ["step_create", "step_validate", "step_approve"]
        assert result["source_refs"][0]["source_id"] == "src_proc_1"

    def test_cross_system_graph_is_preserved_and_blocked_not_flattened(self):
        envelope = _envelope_with_steps()
        envelope["property_spec"] = {
            "process_graph": {
                "process_id": "order_to_payment",
                "nodes": [
                    {
                        "node_id": "create_order",
                        "operation_ref": "op_create_order",
                        "actor_ref": "actor_admin",
                        "system_ref": "erp",
                    },
                    {
                        "node_id": "charge_payment",
                        "operation_ref": "op_charge",
                        "actor_ref": "actor_admin",
                        "system_ref": "payment",
                    },
                ],
                "edges": [
                    {
                        "source_node_id": "create_order",
                        "target_node_id": "charge_payment",
                        "relation_type": "TRIGGERS",
                    }
                ],
            }
        }
        result = compile_multi_step_process_protocol(envelope)
        assert result["status"] == "BLOCKED"
        assert result["reason_code"] == MULTI_STEP_GRAPH_RUNTIME_NOT_AVAILABLE
        assert result["execution_graph"]["process_id"] == "order_to_payment"
        assert "cross_system_target_dispatch" in result["detail"]
        assert "async_edge_scheduler" in result["detail"]

    def test_fork_graph_is_blocked_instead_of_selecting_first_successor(self):
        envelope = _envelope_with_steps()
        envelope["property_spec"] = {
            "process_graph": {
                "process_id": "forked_process",
                "nodes": [
                    {"node_id": "start", "operation_ref": "op_start"},
                    {"node_id": "left", "operation_ref": "op_left"},
                    {"node_id": "right", "operation_ref": "op_right"},
                ],
                "edges": [
                    {"source_node_id": "start", "target_node_id": "left"},
                    {"source_node_id": "start", "target_node_id": "right"},
                ],
            }
        }
        result = compile_multi_step_process_protocol(envelope)
        assert result["status"] == "BLOCKED"
        assert result["reason_code"] == MULTI_STEP_GRAPH_RUNTIME_NOT_AVAILABLE
        assert result["execution_graph"]["fork_groups"] == [
            {"fork_node_id": "start", "successor_node_ids": ["left", "right"]}
        ]

    def test_behavior_ir_process_graph_is_selected_by_operation(self):
        envelope = _envelope_with_steps()
        envelope["property_spec"] = {}
        envelope["operation_ref"] = "op_first"
        envelope["behavior_ir"] = {
            "operations": [],
            "relations": [],
            "process_graphs": [
                {
                    "process_id": "process_one",
                    "nodes": [
                        {"node_id": "first", "operation_ref": "op_first"},
                        {"node_id": "second", "operation_ref": "op_second"},
                    ],
                    "edges": [
                        {"source_node_id": "first", "target_node_id": "second"}
                    ],
                }
            ],
        }
        result = compile_multi_step_process_protocol(envelope)
        assert result["status"] == "COMPILED"
        assert result["execution_graph"]["source_kind"] == "BUSINESS_BEHAVIOR_IR_PROCESS_GRAPH"


class TestStateChainProtocol:
    def test_derives_full_unique_chain_from_transitions(self):
        result = compile_state_chain_protocol(_envelope_with_transitions())
        assert result["status"] == "COMPILED"
        assert [step["operation_ref"] for step in result["treatment_plan"]] == [
            "op_create",
            "op_submit",
            "op_approve",
        ]

    def test_assertion_kind_sequence(self):
        result = compile_state_chain_protocol(_envelope_with_transitions())
        assert result["assertion"]["kind"] == "step_sequence_order"

    def test_ambiguous_primary_transition_is_not_resolved_by_first_item(self):
        envelope = _envelope_with_transitions()
        envelope["behavior_ir"]["relations"].append(
            {
                "relation_id": "rel_create_other",
                "relation_type": "transitions",
                "operation_ref": "op_create",
                "from_ref": "draft",
                "to_ref": "other_created",
            }
        )
        result = compile_state_chain_protocol(envelope)
        assert result["status"] == "BLOCKED"
        assert result["reason_code"] == MULTI_STEP_PROCESS_GRAPH_AMBIGUOUS
        assert result["detail"] == "primary_transition_count:2"


class TestSequenceVerificationProtocol:
    def test_compiles(self):
        result = compile_sequence_verification_protocol(_envelope_with_steps())
        assert result["status"] == "COMPILED"
        assert result["assertion"]["kind"] == "step_sequence_order"


class TestProtocolRegistration:
    def test_register_all(self):
        registered = register_v150_multi_step_protocols()
        assert len(registered) == 3

    def test_resolvable_after_registration(self):
        register_v150_multi_step_protocols()
        registration = resolve_family_protocol("process", TEMPLATE_MULTI_STEP_PROCESS)
        assert registration is not None
        assert registration["per_step_evidence"] is True

    def test_state_chain_resolvable(self):
        register_v150_multi_step_protocols()
        assert resolve_family_protocol("state", TEMPLATE_STATE_CHAIN_PROCESS) is not None

    def test_idempotent_registration(self):
        first = register_v150_multi_step_protocols()
        second = register_v150_multi_step_protocols()
        assert first == second

    def test_registered_in_global_list(self):
        register_v150_multi_step_protocols()
        all_ids = registered_family_protocols()
        assert f"process:{TEMPLATE_MULTI_STEP_PROCESS}" in all_ids


class TestObserverProtocolIntegration:
    def test_observer_installed_before_protocol(self):
        install_process_step_surface()
        assert len(register_v150_multi_step_protocols()) == 3

    def test_sequence_assertion_kind_available(self):
        from ai_test_asset_center.assertion_dsl_base import registered_assertion_kinds

        install_process_step_surface()
        assert "step_sequence_order" in registered_assertion_kinds()
