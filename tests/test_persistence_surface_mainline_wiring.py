"""Persistence surface mainline wiring (mechanism contract, no target I/O).

Guards the four-link chain for the db_sql surface end to end: adapter-declared
install -> IR enum retention -> persistence_integrity obligation generation with
workspace identity -> observer compile gate -> observer dispatch (fail-closed) ->
registered assertion kind evaluation. No database is contacted and no finding is
fabricated: every persistence observation in these tests must refuse visibly with a
named reason code.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_test_asset_center.assertion_dsl_base import (  # noqa: E402
    evaluate_assertion,
    registered_assertion_kinds,
)
from ai_test_asset_center.behavior_ir_core import (  # noqa: E402
    build_behavior_ir_from_knowledge_asset,
)
from ai_test_asset_center.obligation_compiler import (  # noqa: E402
    compile_obligations_from_behavior_ir,
)
from ai_test_asset_center.observer_contracts_base import (  # noqa: E402
    OBSERVER_REGISTRY,
    compile_observer_requirements,
    observe_experiment_requirements,
)
from ai_test_asset_center.persistence_assertions import (  # noqa: E402
    KIND_FIELD_BOUND,
    KIND_STATE_ENUMERATION,
    RISK_FAMILY,
    install_persistence_surface,
)
from ai_test_asset_center.persistence_observer import OBSERVER_ID  # noqa: E402
from ai_test_asset_center.test_obligation import canonical_risk_families  # noqa: E402


@pytest.fixture(scope="module")
def persistence_ir() -> dict:
    install_persistence_surface()
    asset = {
        "entities": [
            {
                "name": "order",
                "table": "orders",
                "fields": ["status", "quantity", "openapi_quantity"],
                "identity_fields": ["id"],
            },
        ],
        "data_tables": [
            {
                "name": "orders",
                "field_dictionary": [
                    {
                        "field": "status",
                        "type": "string",
                        "enum": ["pending", "approved", "rejected"],
                    },
                    {
                        "field": "quantity",
                        "type": "integer",
                        "min": 1,
                        "max": 1000,
                    },
                ],
            },
        ],
        "relationships": [],
    }
    api_operations = [
        {
            "id": "create_order",
            "operation_id": "create_order",
            "method": "POST",
            "path": "/orders",
            "entity_refs": ["order"],
            "read_write": "write",
            "source_refs": [
                {
                    "source_id": "api-doc",
                    "kind": "api_operation",
                    "locator": "POST /orders",
                }
            ],
            "request_schema": {
                "properties": {
                    "openapi_quantity": {"type": "integer", "minimum": 0, "maximum": 500},
                }
            },
        },
    ]
    yield build_behavior_ir_from_knowledge_asset(
        asset,
        project_id="persistence-wiring-test",
        api_operations=api_operations,
    )
    # Full teardown: restore the pre-install registry so test order never leaks
    # an installed surface into unrelated files (observer popped but family maps
    # surviving is exactly the state a later registry check would reject).
    from ai_test_asset_center import assertion_dsl_base as _adb
    from ai_test_asset_center import experiment_compiler_obligation as _eco
    from ai_test_asset_center import obligation_source_adapter as _osa
    from ai_test_asset_center import observer_contracts_base as _ocb
    from ai_test_asset_center.test_obligation import _RUNTIME_CANONICAL_FAMILIES

    for kind in (KIND_STATE_ENUMERATION, KIND_FIELD_BOUND):
        _adb._REGISTERED_ASSERTION_EVALUATORS.pop(kind, None)
        _adb._REGISTERED_KIND_EVIDENCE_KEYS.pop(kind, None)
        _adb.SUPPORTED_KINDS.discard(kind)
    OBSERVER_REGISTRY.pop(OBSERVER_ID, None)
    _ocb._REGISTERED_OBSERVER_HANDLERS.pop(OBSERVER_ID, None)
    _RUNTIME_CANONICAL_FAMILIES.pop(RISK_FAMILY, None)
    _osa._RELATION_TYPES_BY_FAMILY.pop(RISK_FAMILY, None)
    _osa._TEMPLATE_BY_FAMILY.pop(RISK_FAMILY, None)
    _osa._OBSERVERS_BY_FAMILY.pop(RISK_FAMILY, None)
    _eco._FAMILY_ASSERTION_KIND.pop(RISK_FAMILY, None)


def test_surface_install_registers_observer_kinds_and_family() -> None:
    installed = install_persistence_surface()
    assert OBSERVER_ID in OBSERVER_REGISTRY
    assert RISK_FAMILY in canonical_risk_families()
    assert KIND_STATE_ENUMERATION in registered_assertion_kinds()
    assert KIND_FIELD_BOUND in registered_assertion_kinds()
    assert installed["risk_family"] == RISK_FAMILY


def test_ir_retains_source_declared_enum_values(persistence_ir: dict) -> None:
    entity = next(
        row for row in persistence_ir["entities"] if row.get("name") == "order"
    )
    assert entity.get("table") == "orders"
    status_field = next(
        (
            row
            for row in entity.get("fields", [])
            if isinstance(row, dict) and row.get("name") == "status"
        ),
        None,
    )
    assert status_field is not None
    assert status_field.get("enum_values") == ["pending", "approved", "rejected"]


def test_ir_retains_source_declared_bounds(persistence_ir: dict) -> None:
    """Both declaration paths survive into the IR: field dictionary min/max and
    OpenAPI schema minimum/maximum."""
    entity = next(
        row for row in persistence_ir["entities"] if row.get("name") == "order"
    )
    fields = {
        row.get("name"): row
        for row in entity.get("fields", [])
        if isinstance(row, dict) and row.get("name")
    }
    quantity = fields.get("quantity")
    assert quantity is not None
    assert quantity.get("min_value") == 1
    assert quantity.get("max_value") == 1000
    openapi_quantity = fields.get("openapi_quantity")
    assert openapi_quantity is not None
    assert openapi_quantity.get("min_value") == 0
    assert openapi_quantity.get("max_value") == 500


def test_persistence_bound_obligation_generated(persistence_ir: dict) -> None:
    pack = compile_obligations_from_behavior_ir(
        persistence_ir,
        root=str(ROOT),
        project="persistence-wiring-test",
    )
    bound_obligations = [
        row
        for row in pack["obligations"]
        if row.get("risk_family") == RISK_FAMILY
        and row.get("property", {}).get("template") == "persistence_field_bound"
    ]
    assert bound_obligations, "no persisted_field_bound obligation compiled"
    by_field = {
        row["property"]["persistence_bounded_field"]: row["property"]
        for row in bound_obligations
    }
    quantity_prop = by_field.get("quantity")
    assert quantity_prop is not None
    assert quantity_prop.get("persistence_min") == 1
    assert quantity_prop.get("persistence_max") == 1000
    openapi_prop = by_field.get("openapi_quantity")
    assert openapi_prop is not None
    assert openapi_prop.get("persistence_min") == 0
    assert openapi_prop.get("persistence_max") == 500


def test_persistence_obligation_generated_with_workspace_identity(
    persistence_ir: dict,
) -> None:
    pack = compile_obligations_from_behavior_ir(
        persistence_ir,
        root=str(ROOT),
        project="persistence-wiring-test",
    )
    obligations = [
        row
        for row in pack["obligations"]
        if row.get("risk_family") == RISK_FAMILY
    ]
    assert obligations, "no persistence_integrity obligation compiled"
    prop = obligations[0]["property"]
    assert prop.get("persistence_table") == "orders"
    assert prop.get("persistence_allowed_states") == [
        "pending",
        "approved",
        "rejected",
    ]
    assert prop.get("project") == "persistence-wiring-test"
    assert prop.get("persistence_root")
    assert OBSERVER_ID in obligations[0].get("required_observers", [])


def test_no_workspace_identity_means_no_persistence_obligation(
    persistence_ir: dict,
) -> None:
    pack = compile_obligations_from_behavior_ir(persistence_ir)
    obligations = [
        row
        for row in pack["obligations"]
        if row.get("risk_family") == RISK_FAMILY
    ]
    # Diagnostic compiles without a workspace must not fabricate obligations
    # whose observer could never resolve a DSN.
    assert obligations == []


def test_observer_requirements_compile_for_declared_db_target(
    persistence_ir: dict,
) -> None:
    pack = compile_obligations_from_behavior_ir(
        persistence_ir,
        root=str(ROOT),
        project="persistence-wiring-test",
    )
    obligation = next(
        row
        for row in pack["obligations"]
        if row.get("risk_family") == RISK_FAMILY
    )
    requirements, reason_code, detail = compile_observer_requirements(
        obligation.get("required_observers", []),
        risk_family=RISK_FAMILY,
        available_adapters={"http_api", "db_sql", "process_ledger"},
    )
    assert reason_code == "", f"compile blocked: {reason_code} {detail}"
    assert len(requirements) == 2


def test_observer_requirements_block_without_db_declaration(
    persistence_ir: dict,
) -> None:
    pack = compile_obligations_from_behavior_ir(
        persistence_ir,
        root=str(ROOT),
        project="persistence-wiring-test",
    )
    obligation = next(
        row
        for row in pack["obligations"]
        if row.get("risk_family") == RISK_FAMILY
    )
    _, reason_code, detail = compile_observer_requirements(
        obligation.get("required_observers", []),
        risk_family=RISK_FAMILY,
        available_adapters={"http_api"},
    )
    assert reason_code == "BLOCKED_UNSUPPORTED_ADAPTER"
    assert detail == "db_sql"


def test_observer_dispatch_reaches_handler_and_refuses_fail_closed(
    persistence_ir: dict,
) -> None:
    pack = compile_obligations_from_behavior_ir(
        persistence_ir,
        root=str(ROOT),
        project="persistence-wiring-test",
    )
    obligation = next(
        row
        for row in pack["obligations"]
        if row.get("risk_family") == RISK_FAMILY
    )
    prop = obligation["property"]
    receipts = observe_experiment_requirements(
        {
            "assertions": [{"kind": KIND_STATE_ENUMERATION, "property": prop}],
            "observers": [
                {"observer_id": "http_response"},
                {"observer_id": OBSERVER_ID},
            ],
            "source_refs": obligation.get("source_refs", []),
        },
        observations={
            "control_observation": {
                "status_code": 200,
                "body": {"id": "o1", "status": "pending"},
                "method": "GET",
                "path": "/orders",
            },
            "treatment_observation": {},
            "control_actor_ref": "actor-owner",
            "treatment_actor_ref": "actor-viewer",
            "execution_steps": [],
        },
        campaign_id="campaign-wiring",
        execution_id="execution-wiring",
    )
    persistence_receipts = [
        row for row in receipts if row.get("observer_id") == OBSERVER_ID
    ]
    assert len(persistence_receipts) == 1
    receipt = persistence_receipts[0]
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"], "refusal must carry a named reason code"


def test_registered_assertion_kind_evaluates_through_facade() -> None:
    receipt = evaluate_assertion(
        {"kind": KIND_STATE_ENUMERATION, "property": {}},
        observations={},
        campaign_id="campaign-wiring",
        execution_id="execution-wiring",
    )
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "ASSERTION_EVIDENCE_MISSING"
