"""Enterprise business understanding model package."""
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
from .gate import assess_understanding_model
from .implementation_binding import (
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

# Additive parser metadata only. This wrapper stores field locations, response contracts and
# security scheme names; it never retains request examples, secret values or credentials.
install_interface_runtime_contract_parser()
# All materialization entrypoints, including direct low-level builder calls, must scrub an
# unapproved runtime value before any non-sendable draft is assembled.
install_secure_runtime_value_resolver()

# Backward-compatible package names now point to the single governed and security-audited
# authority. The explicit ``*_core_*`` names remain module-internal primitives and are not exported
# through ``__all__``.
build_runtime_materializations_v1 = build_secure_runtime_materializations_v1
project_runtime_materializations_to_asset = project_secure_runtime_materializations_to_asset

__all__ = [
    "BEHAVIOR_SCHEMA",
    "BEHAVIOR_ROW_LEDGER_SCHEMA",
    "BEHAVIOR_GATE_SCHEMA",
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
    "build_decision_matrix_row_ledger",
    "build_business_behavior_ir",
    "build_governed_business_behavior_ir",
    "build_business_behavior_ir_v1",
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
    "build_enterprise_understanding_model",
    "assess_understanding_model",
    "enrich_asset_with_enterprise_understanding",
    "install_enterprise_understanding_model",
    "build_lifecycles",
    "build_object_graph",
]
