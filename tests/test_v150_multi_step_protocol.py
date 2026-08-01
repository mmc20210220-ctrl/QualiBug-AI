"""Multi-step protocol graph-authority regression tests."""
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
            "source_refs": [
                {"source_id": "src_proc_1", "doc_ref": "Business Process §4"}
            ],
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
                {
                    "id": "op_submit",
                    "method": "POST",
                    "path": "/api/records/{id}/submit",
                },
                {
                    "id": "op_approve",
                    "method": "POST",
                    "path": "/api/records/{id}/approve",
                },
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
    def test_compiles_declared_linear_steps_with_graph_on_every_step(self):
        result = compile_multi_step_process_protocol(_envelope_with_steps())
        assert result["status"] == "COMPILED"
        assert len(result["treatment_plan"]) == 3
        assert result["execution_graph"]["topological_order"] == [
            "step_create",
            "step_validate",
            "step_approve",
        ]
        graph_ids = {
            step["_execution_graph"]["execution_graph_id"]
            for step in result["treatment_plan"]
        }
        assert graph_ids == {result["execution_graph"]["execution_graph_id"]}

    def test_duplicate_step_id_blocks(self):
        envelope = _envelope_with_steps()
        envelope["property_spec"]["process_steps"][1]["step_id"] = "step_create"
        result = compile_multi_step_process_protocol(envelope)
        assert result["status"] == "BLOCKED"
        assert "duplicate" in result["detail"]

    def test_cleanup_uses_explicit_compensation_in_reverse_order(self):
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

    def test_missing_compensation_never_reuses_original_write(self):
        envelope = _envelope_with_steps()
        for step in envelope["property_spec"]["process_steps"]:
            step.pop("cleanup_operation_ref", None)
        result = compile_multi_step_process_protocol(envelope)
        assert result["status"] == "COMPILED"
        assert result["cleanup_plan"] == []

    def test_synchronous_cross_system_dependency_compiles(self):
        envelope = _envelope_with_steps()
        envelope["property_spec"] = {
            "process_graph": {
                "process_id": "order_to_payment",
                "nodes": [
                    {
                        "node_id": "read_order",
                        "operation_ref": "op_read_order",
                        "actor_ref": "actor_admin",
                        "system_ref": "erp",
                        "method": "GET",
                    },
                    {
                        "node_id": "read_payment",
                        "operation_ref": "op_read_payment",
                        "actor_ref": "actor_admin",
                        "system_ref": "payment",
                        "method": "GET",
                    },
                ],
                "edges": [
                    {
                        "source_node_id": "read_order",
                        "target_node_id": "read_payment",
                        "relation_type": "DEPENDS_ON",
                    }
                ],
            }
        }
        result = compile_multi_step_process_protocol(envelope)
        assert result["status"] == "COMPILED"
        assert result["execution_graph"]["scheduler_mode"] == "dependency_waves"
        assert {step["system_ref"] for step in result["treatment_plan"]} == {
            "erp",
            "payment",
        }

    def test_async_cross_system_edge_remains_visible_and_blocked(self):
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
        assert (
            result["semantic_reason_code"]
            == "PROCESS_GRAPH_ASYNC_EDGE_WAIT_UNCOVERED"
        )
        assert result["wait_contract_compile_receipt"]["issues"] == [
            "async_edge_uncovered:create_order->charge_payment"
        ]
        assert result["execution_graph"]["process_id"] == "order_to_payment"

    def test_fork_and_join_compile_as_dependency_graph(self):
        envelope = _envelope_with_steps()
        envelope["property_spec"] = {
            "process_graph": {
                "process_id": "fork_join",
                "nodes": [
                    {"node_id": "start", "operation_ref": "op_start"},
                    {"node_id": "left", "operation_ref": "op_left"},
                    {"node_id": "right", "operation_ref": "op_right"},
                    {"node_id": "join", "operation_ref": "op_join"},
                ],
                "edges": [
                    {"source_node_id": "start", "target_node_id": "left"},
                    {"source_node_id": "start", "target_node_id": "right"},
                    {"source_node_id": "left", "target_node_id": "join"},
                    {"source_node_id": "right", "target_node_id": "join"},
                ],
            }
        }
        result = compile_multi_step_process_protocol(envelope)
        assert result["status"] == "COMPILED"
        assert result["execution_graph"]["fork_groups"] == [
            {"fork_node_id": "start", "successor_node_ids": ["left", "right"]}
        ]
        assert result["execution_graph"]["join_groups"] == [
            {"join_node_id": "join", "predecessor_node_ids": ["left", "right"]}
        ]
        assert result["expected_order"] == []

    def test_behavior_ir_graph_selected_by_operation(self):
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
        assert result["execution_graph"]["source_kind"] == (
            "BUSINESS_BEHAVIOR_IR_PROCESS_GRAPH"
        )


class TestStateChainProtocol:
    def test_derives_full_unique_chain(self):
        result = compile_state_chain_protocol(_envelope_with_transitions())
        assert result["status"] == "COMPILED"
        assert [step["operation_ref"] for step in result["treatment_plan"]] == [
            "op_create",
            "op_submit",
            "op_approve",
        ]

    def test_ambiguous_primary_transition_does_not_select_first(self):
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
    def test_linear_graph_compiles(self):
        result = compile_sequence_verification_protocol(_envelope_with_steps())
        assert result["status"] == "COMPILED"
        assert result["assertion"]["kind"] == "step_sequence_order"

    def test_partial_order_graph_is_not_fabricated_as_total_order(self):
        envelope = _envelope_with_steps()
        envelope["property_spec"] = {
            "process_graph": {
                "process_id": "parallel",
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
        result = compile_sequence_verification_protocol(envelope)
        assert result["status"] == "BLOCKED"
        assert result["detail"] == "sequence_total_order_not_source_declared"


class TestProtocolRegistration:
    def test_registration_is_existing_registry_and_idempotent(self):
        first = register_v150_multi_step_protocols()
        second = register_v150_multi_step_protocols()
        assert first == second
        assert len(first) == 3
        assert resolve_family_protocol("process", TEMPLATE_MULTI_STEP_PROCESS) is not None
        assert resolve_family_protocol("state", TEMPLATE_STATE_CHAIN_PROCESS) is not None
        assert (
            f"process:{TEMPLATE_SEQUENCE_VERIFICATION}"
            in registered_family_protocols()
        )

    def test_observer_surface_is_installed_first(self):
        install_process_step_surface()
        assert len(register_v150_multi_step_protocols()) == 3
