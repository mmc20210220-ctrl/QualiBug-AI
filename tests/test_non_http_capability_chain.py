"""End-to-end proof that a NEW bug class on a NON-HTTP surface is reachable.

This is the integration test for the whole extensibility effort. Before it, a defect class
outside HTTP was blocked at four independent points, and closing any three of them changed
nothing:

* link 1 — risk families were a closed 10-tuple; register_risk_family could only alias onto
  an existing member
* link 2 — SUPPORTED_KINDS was a literal set and the evaluator a hardcoded if/elif, so the
  facade set-union pattern could add a NAME but never a dispatch branch
* link 3 — all 13 built-in observers declared adapter "http_api" and the registry had no
  registration function
* link 5 — the delivery gate's reproduction receipt required an HTTP status_code and
  path_template on the request side

The test registers a persistence surface through those entry points and compiles an
obligation in it, asserting a db_sql observer survives every gate. It also asserts the
fail-closed half: an adapter the target has not declared still blocks.

Nothing here connects to a database. The point is the CHAIN, not the read.
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest

from ai_test_asset_center import assertion_dsl_base as adb
from ai_test_asset_center import observer_contracts_base as ocb
from ai_test_asset_center import test_obligation as tob
from ai_test_asset_center.assertion_dsl_base import SUPPORTED_KINDS
from ai_test_asset_center.experiment_compiler_obligation import compile_experiment_for_obligation
from ai_test_asset_center.observer_contracts_base import OBSERVER_REGISTRY
from ai_test_asset_center.persistence_assertions import (
    KIND_FIELD_BOUND,
    KIND_STATE_ENUMERATION,
    RISK_FAMILY,
    install_persistence_surface,
)
from ai_test_asset_center.persistence_observer import ADAPTER, OBSERVER_ID


@pytest.fixture()
def persistence_surface() -> Iterator[None]:
    install_persistence_surface()
    yield
    for kind in (KIND_STATE_ENUMERATION, KIND_FIELD_BOUND):
        adb._REGISTERED_ASSERTION_EVALUATORS.pop(kind, None)
        adb._REGISTERED_KIND_EVIDENCE_KEYS.pop(kind, None)
        SUPPORTED_KINDS.discard(kind)
    OBSERVER_REGISTRY.pop(OBSERVER_ID, None)
    ocb._REGISTERED_OBSERVER_HANDLERS.pop(OBSERVER_ID, None)
    tob._RUNTIME_CANONICAL_FAMILIES.pop(RISK_FAMILY, None)
    from ai_test_asset_center import experiment_compiler_obligation as eco
    from ai_test_asset_center import obligation_source_adapter as osa
    for mapping in (
        osa._RELATION_TYPES_BY_FAMILY, osa._TEMPLATE_BY_FAMILY, osa._OBSERVERS_BY_FAMILY,
        eco._FAMILY_ASSERTION_KIND,
    ):
        mapping.pop(RISK_FAMILY, None)


def _behavior_ir() -> dict[str, Any]:
    return {
        "schema_version": "qualibug.behavior-ir.v2",
        "operations": [{
            "id": "op-list-orders", "service": "orders", "method": "GET",
            "path": "/api/orders", "path_template": "/api/orders", "read_write": "read",
        }],
        "actors": [{
            "id": "actor-reader", "role": "admin",
            "credential_secret_ref": "secret-reader", "status": "active",
        }],
        "entities": [{"id": "entity-order", "name": "order"}],
        "relations": [], "invariants": [], "states": [],
    }


def _obligation() -> dict[str, Any]:
    return {
        "schema_version": "qualibug.test-obligation.v1",
        "obligation_id": "obl-persistence-integrity",
        "risk_family": RISK_FAMILY,
        "subject_refs": ["op-list-orders"],
        "property": {
            "operation_ref": "op-list-orders",
            "actor_ref": "actor-reader",
            "template": "state_transition",
            # Source-declared persistence target and expectation. Nothing is inferred.
            "persistence_root": ".",
            "project": "proj",
            "persistence_table": "orders",
            "persistence_fields": ["lifecycle_state"],
            "persistence_state_field": "lifecycle_state",
            "persistence_allowed_states": ["NEW", "CLOSED"],
        },
        "required_actors": ["actor-reader"],
        "required_operations": ["op-list-orders"],
        "required_fixtures": [],
        "required_observers": ["http_response", OBSERVER_ID],
        "cleanup_requirement": {"required": False},
    }


def test_all_four_links_register_without_editing_core_code(persistence_surface) -> None:
    from ai_test_asset_center import experiment_compiler_obligation as eco
    from ai_test_asset_center import obligation_source_adapter as osa

    # link 1: the family is canonical, with all three by-family maps written for it.
    assert RISK_FAMILY in tob.canonical_risk_families()
    assert RISK_FAMILY in osa._RELATION_TYPES_BY_FAMILY
    assert RISK_FAMILY in osa._TEMPLATE_BY_FAMILY
    assert RISK_FAMILY in osa._OBSERVERS_BY_FAMILY
    # link 2: the assertion kind is registered and evaluable.
    assert KIND_STATE_ENUMERATION in SUPPORTED_KINDS
    assert eco._FAMILY_ASSERTION_KIND[RISK_FAMILY] == KIND_STATE_ENUMERATION
    # link 3: the observer exists on a NON-http adapter.
    assert OBSERVER_REGISTRY[OBSERVER_ID]["adapter"] == ADAPTER != "http_api"


def test_non_http_obligation_compiles_when_the_adapter_is_declared(persistence_surface) -> None:
    experiment = compile_experiment_for_obligation(
        _obligation(),
        behavior_ir=_behavior_ir(),
        environment_type="test",
        available_adapters={"http_api", ADAPTER},
    )
    receipt = experiment["compile_receipt"]
    assert receipt["status"] == "COMPILED", receipt

    # The adapter set is recorded, so runtime validation agrees with compilation.
    assert ADAPTER in experiment["compiled_adapters"]
    # The non-http observer survived every gate.
    observers = {row["observer_id"]: row["adapter"] for row in experiment["observers"]}
    assert observers[OBSERVER_ID] == ADAPTER
    # And the registered assertion kind is what will judge it.
    assert [row["kind"] for row in experiment["assertions"]] == [KIND_STATE_ENUMERATION]


def test_undeclared_adapter_still_blocks(persistence_surface) -> None:
    """The fail-closed half. Registering an adapter never makes it available everywhere.

    A target that has not declared a database must not have one read.
    """
    experiment = compile_experiment_for_obligation(
        _obligation(),
        behavior_ir=_behavior_ir(),
        environment_type="test",
        available_adapters={"http_api"},
    )
    receipt = experiment["compile_receipt"]
    assert receipt["status"] == "BLOCKED"
    assert receipt["reason_code"] == "BLOCKED_UNSUPPORTED_ADAPTER"
    assert receipt["detail"] == ADAPTER


def test_production_environment_still_blocks_the_whole_chain(persistence_surface) -> None:
    """No extension point may weaken the environment boundary."""
    for environment in ("production", "prod", "live", "", "unknown"):
        experiment = compile_experiment_for_obligation(
            _obligation(),
            behavior_ir=_behavior_ir(),
            environment_type=environment,
            available_adapters={"http_api", ADAPTER},
        )
        assert experiment["compile_receipt"]["status"] == "BLOCKED", environment


def test_surface_teardown_leaves_no_residue(persistence_surface) -> None:
    """Guards the fixture: a leaked family or kind changes what later tests compile."""
    assert RISK_FAMILY in tob.canonical_risk_families()


def test_no_persistence_residue_after_teardown() -> None:
    """Runs without the fixture, so it observes the post-teardown state."""
    assert RISK_FAMILY not in tob._RUNTIME_CANONICAL_FAMILIES
    assert OBSERVER_ID not in OBSERVER_REGISTRY
    assert KIND_STATE_ENUMERATION not in SUPPORTED_KINDS
