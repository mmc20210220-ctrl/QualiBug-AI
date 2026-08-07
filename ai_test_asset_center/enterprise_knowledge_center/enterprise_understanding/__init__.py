"""Enterprise business understanding model package.

Importing this package is declarative: it exports builders and schemas but does not
register parsers, replace resolvers or wrap Probe compilers.
"""
from .behavior_ir import (
    BEHAVIOR_GATE_SCHEMA,
    BEHAVIOR_ROW_LEDGER_SCHEMA,
    BEHAVIOR_SCHEMA,
    build_business_behavior_ir,
    build_decision_matrix_row_ledger,
)
from .behavior_ir_governance import build_governed_business_behavior_ir
from .behavior_ir_logic_gate import build_business_behavior_ir_v1
from .builder import build_enterprise_understanding_model
from .business_world_model import build_business_world_model, project_business_world_model
from .chinese_semantic_behavior_ir_adapter import (
    apply_semantic_frames_to_behavior_ir,
    project_semantic_frames_to_behavior_ir,
)
from .chinese_semantic_ledger_adapter import (
    frames_from_asset,
    project_business_facts_to_semantic_frames,
    project_fact_to_semantic_frame,
)
from .chinese_semantic_receipts import (
    CHINESE_SEMANTIC_RECEIPT_SCHEMA,
    build_receipt,
    validate_receipt,
)
from .chinese_semantic_schema import (
    CHINESE_SEMANTIC_FRAME_SCHEMA,
    FRAME_TYPES,
    REASON_CODES,
    SLOT_STATUSES,
    empty_frame,
    semantic_signature,
    semantic_structure_payload,
    validate_semantic_frame,
)
from .business_world_model_schema import (
    BUSINESS_WORLD_MODEL_GATE_SCHEMA,
    BUSINESS_WORLD_MODEL_SCHEMA,
    empty_business_world_model,
    validate_business_world_model_shape,
)
from .gate import assess_understanding_model
from .identity_structural_review import (
    ACTION_CONFIRM_ALIAS,
    ACTION_REJECT_CANDIDATE,
    DECISION_KIND as IDENTITY_STRUCTURAL_REVIEW_DECISION_KIND,
    REVIEW_QUEUE_SCHEMA as IDENTITY_STRUCTURAL_REVIEW_QUEUE_SCHEMA,
    REVIEW_RECEIPT_SCHEMA as IDENTITY_STRUCTURAL_REVIEW_RECEIPT_SCHEMA,
)
from .identity_structural_review_command import (
    record_identity_structural_review_decision,
)
from .identity_structural_review_query import get_identity_structural_review_queue
from .implementation_binding_authority import (
    IMPLEMENTATION_BINDING_GATE_SCHEMA,
    IMPLEMENTATION_BINDING_SCHEMA,
    build_behavior_implementation_bindings,
)
from .implementation_binding_governance import (
    build_governed_behavior_implementation_bindings,
)
from .implementation_binding_projection import (
    SCENARIO_PLANNING_GATE_SCHEMA,
    build_final_scenario_planning_gate,
    project_final_scenario_planning_gate,
)
from .integration import (
    enrich_asset_with_enterprise_understanding,
    install_enterprise_understanding_model,
)
from .interface_runtime_contracts import (
    OPENAPI_RUNTIME_CONTRACT_SCHEMA,
    enrich_openapi_runtime_contracts,
    install_interface_runtime_contract_parser,
)
from .lifecycle_builder import build_lifecycles
from .object_graph import build_object_graph
from .probe_policy import (
    build_gated_probes,
    probe_generation_allowed,
    probe_generation_block_reason,
)
from .process_graph_ir import (
    PROCESS_GRAPH_GATE_SCHEMA,
    PROCESS_GRAPH_SCHEMA,
    build_business_process_graphs,
)
from .runtime_materialization import (
    RUNTIME_MATERIALIZATION_GATE_SCHEMA,
    RUNTIME_MATERIALIZATION_SCHEMA,
    build_runtime_materializations_v1 as build_runtime_materializations_core_v1,
    project_runtime_materializations_to_asset as project_runtime_materializations_core_to_asset,
)
from .runtime_materialization_governance import (
    project_governed_runtime_materializations_to_asset,
)
from .runtime_materialization_security import (
    build_secure_runtime_materializations_v1,
    install_secure_runtime_value_resolver,
    project_secure_runtime_materializations_to_asset,
)
from .runtime_plan import (
    RUNTIME_PLAN_GATE_SCHEMA,
    RUNTIME_PLAN_SCHEMA,
    build_runtime_plans_v1,
    project_runtime_plans_to_asset,
)
from .runtime_plan_governance import project_governed_runtime_plans_to_asset
from .scenario_execution_contract import (
    SCENARIO_EXECUTION_CONTRACT_GATE_SCHEMA,
    SCENARIO_EXECUTION_CONTRACT_SCHEMA,
    build_scenario_execution_contracts,
    project_scenario_execution_contracts,
)
from .scenario_execution_contract_projection import (
    project_governed_scenario_execution_contracts,
)
from .scenario_ir import (
    SCENARIO_IR_GATE_SCHEMA,
    SCENARIO_IR_SCHEMA,
    build_scenario_ir_v1,
    project_scenario_ir_to_asset,
)
from .scenario_ir_asset_governance import project_scenario_ir_asset_governance
from .schema import *  # noqa: F401,F403

# Package-level public names point to the secure projection, but no module-level
# function is replaced to achieve this alias.
build_runtime_materializations_v1 = build_secure_runtime_materializations_v1
project_runtime_materializations_to_asset = project_secure_runtime_materializations_to_asset

__all__ = [
    "BEHAVIOR_SCHEMA",
    "BEHAVIOR_ROW_LEDGER_SCHEMA",
    "BEHAVIOR_GATE_SCHEMA",
    "PROCESS_GRAPH_SCHEMA",
    "PROCESS_GRAPH_GATE_SCHEMA",
    "IMPLEMENTATION_BINDING_SCHEMA",
    "IMPLEMENTATION_BINDING_GATE_SCHEMA",
    "SCENARIO_PLANNING_GATE_SCHEMA",
    "SCENARIO_IR_SCHEMA",
    "SCENARIO_IR_GATE_SCHEMA",
    "SCENARIO_EXECUTION_CONTRACT_SCHEMA",
    "SCENARIO_EXECUTION_CONTRACT_GATE_SCHEMA",
    "RUNTIME_PLAN_SCHEMA",
    "RUNTIME_PLAN_GATE_SCHEMA",
    "RUNTIME_MATERIALIZATION_SCHEMA",
    "RUNTIME_MATERIALIZATION_GATE_SCHEMA",
    "OPENAPI_RUNTIME_CONTRACT_SCHEMA",
    "IDENTITY_STRUCTURAL_REVIEW_DECISION_KIND",
    "IDENTITY_STRUCTURAL_REVIEW_QUEUE_SCHEMA",
    "IDENTITY_STRUCTURAL_REVIEW_RECEIPT_SCHEMA",
    "ACTION_CONFIRM_ALIAS",
    "ACTION_REJECT_CANDIDATE",
    "get_identity_structural_review_queue",
    "record_identity_structural_review_decision",
    "build_decision_matrix_row_ledger",
    "build_business_behavior_ir",
    "build_governed_business_behavior_ir",
    "build_business_behavior_ir_v1",
    "build_business_process_graphs",
    "build_behavior_implementation_bindings",
    "build_governed_behavior_implementation_bindings",
    "build_final_scenario_planning_gate",
    "project_final_scenario_planning_gate",
    "build_scenario_ir_v1",
    "project_scenario_ir_to_asset",
    "project_scenario_ir_asset_governance",
    "build_scenario_execution_contracts",
    "project_scenario_execution_contracts",
    "project_governed_scenario_execution_contracts",
    "build_runtime_plans_v1",
    "project_runtime_plans_to_asset",
    "project_governed_runtime_plans_to_asset",
    "build_runtime_materializations_v1",
    "project_runtime_materializations_to_asset",
    "build_secure_runtime_materializations_v1",
    "project_secure_runtime_materializations_to_asset",
    "install_secure_runtime_value_resolver",
    "enrich_openapi_runtime_contracts",
    "install_interface_runtime_contract_parser",
    "build_gated_probes",
    "probe_generation_allowed",
    "probe_generation_block_reason",
    "build_enterprise_understanding_model",
    "CHINESE_SEMANTIC_FRAME_SCHEMA",
    "CHINESE_SEMANTIC_RECEIPT_SCHEMA",
    "FRAME_TYPES",
    "REASON_CODES",
    "SLOT_STATUSES",
    "empty_frame",
    "semantic_signature",
    "semantic_structure_payload",
    "validate_semantic_frame",
    "build_receipt",
    "validate_receipt",
    "project_business_facts_to_semantic_frames",
    "project_fact_to_semantic_frame",
    "frames_from_asset",
    "apply_semantic_frames_to_behavior_ir",
    "project_semantic_frames_to_behavior_ir",
    "BUSINESS_WORLD_MODEL_SCHEMA",
    "BUSINESS_WORLD_MODEL_GATE_SCHEMA",
    "empty_business_world_model",
    "validate_business_world_model_shape",
    "build_business_world_model",
    "project_business_world_model",
    "assess_understanding_model",
    "enrich_asset_with_enterprise_understanding",
    "install_enterprise_understanding_model",
    "build_lifecycles",
    "build_object_graph",
]
