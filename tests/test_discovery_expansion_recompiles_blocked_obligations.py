"""Regression: discovery expansion must recompile compile-blocked obligations.

Round-0 obligations that failed experiment compilation (e.g. an order body
placeholder with no documented address route) were excluded from the
observation-driven expansion round even after the governed discovery probes
confirmed the missing resolver (``GET /api/users/addresses``). Only
obligations that actually COMPILED are immutable; compile-blocked ones made
zero target requests and may be reopened per the ledger rule and re-compiled
against the expanded Behavior IR.
"""
from __future__ import annotations

from ai_test_asset_center.discovery_runtime_execution import (
    _compiled_round0_obligation_ids,
    _runtime_recompile_round0_obligation_ids,
)
from ai_test_asset_center.experiment_compiler import (
    compile_experiment_for_obligation,
)
from ai_test_asset_center.runtime_binding_graph import build_binding_plan


def _obligation() -> dict:
    return {
        "obligation_id": "obl_create_order",
        "risk_family": "async",
        "required_operations": ["op_orders"],
        "source_refs": [
            {
                "source_id": "rules",
                "locator": "line:1",
                "kind": "rule",
            }
        ],
        "property": {
            "operation_ref": "op_orders",
            "invariant_ref": "rule_order",
            "expression": {
                "kind": "business_rule",
                "operator": "must_hold",
                "operands": [],
                "raw": "source rule",
            },
        },
    }


def _ir(*, with_address_resolver: bool) -> dict:
    operations = [
        {
            "id": "op_orders",
            "method": "POST",
            "path": "/api/orders",
            "request_example": {
                "items": [{"sku": "SKU-1", "qty": 1}],
                "addressId": "<address_id>",
            },
            "request_schema": {
                "content": {
                    "application/json": {
                        "example": {
                            "items": [{"sku": "SKU-1", "qty": 1}],
                            "addressId": "<address_id>",
                        }
                    }
                }
            },
            "source_refs": [
                {
                    "source_id": "api_spec",
                    "locator": "POST /api/orders",
                    "kind": "api_operation",
                }
            ],
        }
    ]
    if with_address_resolver:
        operations.extend(
            [
                {
                    "id": "op_addresses",
                    "method": "GET",
                    "path": "/api/users/addresses",
                    "source_refs": [
                        {
                            "source_id": "runtime-observed",
                            "locator": "GET /api/users/addresses",
                            "kind": "runtime_observation",
                        }
                    ],
                },
                {
                    "id": "op_order_detail",
                    "method": "GET",
                    "path": "/api/orders/:id",
                    "source_refs": [
                        {
                            "source_id": "api_spec",
                            "locator": "GET /api/orders/:id",
                            "kind": "api_operation",
                        }
                    ],
                },
            ]
        )
    return {
        "schema_version": "qualibug.behavior-ir.v2",
        "project_id": "industry-neutral-test",
        "operations": operations,
        "actors": [
            {
                "id": "buyer01",
                "role": "buyer",
                "account_ref": "buyer01",
                "credential_secret_ref": "secret_ref:test_accounts:buyer01",
                "runtime_bound": True,
            }
        ],
        "relations": [
            {
                "id": "perm_buyer_orders",
                "relation_type": "permits",
                "actor_ref": "buyer01",
                "from_ref": "buyer01",
                "to_ref": "op_orders",
                "operation_ref": "op_orders",
                "status": "accepted",
                "source_refs": [
                    {
                        "source_id": "rules",
                        "locator": "buyer may place orders",
                        "kind": "rule",
                    }
                ],
            }
        ],
        "invariants": [],
        "states": [],
        "entities": [],
    }


def test_order_obligation_blocks_without_resolver_and_compiles_with_it() -> None:
    blocked = compile_experiment_for_obligation(
        _obligation(),
        behavior_ir=_ir(with_address_resolver=False),
        environment_type="test",
        available_adapters={"http_api"},
    )
    assert blocked["compile_receipt"]["reason_code"] == "BLOCKED_MISSING_BINDING"
    assert "BODY_PARAMETER_NOT_SOURCE_BOUND" in str(
        blocked["compile_receipt"].get("detail") or ""
    )

    compiled = compile_experiment_for_obligation(
        _obligation(),
        behavior_ir=_ir(with_address_resolver=True),
        environment_type="test",
        available_adapters={"http_api"},
    )
    # The address binding is now resolvable; later gates (observer, cleanup)
    # are separate source contracts and may still block in this minimal IR.
    assert compiled["compile_receipt"]["reason_code"] != "BLOCKED_MISSING_BINDING"


def test_compiled_round0_ids_exclude_blocked_and_collapse_variants() -> None:
    experiments = [
        {
            "obligation_id": "obl_a",
            "compile_receipt": {"status": "COMPILED"},
        },
        {
            "obligation_id": "obl_a__v_1234",
            "compile_receipt": {"status": "COMPILED"},
        },
        {
            "obligation_id": "obl_blocked",
            "compile_receipt": {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_BINDING",
            },
        },
        {
            "obligation_id": "obl_blocked__v_5678",
            "compile_receipt": {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_BINDING",
            },
        },
    ]
    compiled = _compiled_round0_obligation_ids(experiments)
    assert compiled == {"obl_a"}
    assert "obl_blocked" not in compiled


def test_runtime_recompile_selection_is_limited_to_source_bound_body_blockers() -> None:
    obligations = [
        {"obligation_id": "obl_body"},
        {"obligation_id": "obl_body_abstract"},
        {"obligation_id": "obl_cleanup"},
        {"obligation_id": "obl_observer"},
        {"obligation_id": "obl_compiled"},
    ]
    experiments = {
        "obl_body": {
            "compile_receipt": {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_BINDING",
                "detail": "unresolvable_path_placeholders:BODY_PARAMETER_NOT_SOURCE_BOUND",
            }
        },
        "obl_body_abstract": {
            "compile_receipt": {
                "status": "ABSTRACT",
                "reason_code": "BLOCKED_MISSING_BINDING",
                "detail": "unresolvable_path_placeholders:BODY_PARAMETER_NOT_SOURCE_BOUND",
                "abstract_retained": True,
            }
        },
        "obl_cleanup": {
            "compile_receipt": {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_NON_REVERSIBLE_WRITE",
                "detail": "cleanup_unresolved",
            }
        },
        "obl_observer": {
            "compile_receipt": {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_OBSERVER",
                "detail": "write_observer",
            }
        },
        "obl_compiled": {"compile_receipt": {"status": "COMPILED"}},
    }

    assert _runtime_recompile_round0_obligation_ids(obligations, experiments) == {
        "obl_body",
        "obl_body_abstract",
    }


def test_body_placeholder_resolver_uses_entity_route_not_operation_collection() -> None:
    operation = {
        "id": "op_orders",
        "method": "POST",
        "path": "/api/orders",
        "request_example": {
            "items": [{"sku": "SKU-1", "qty": 1}],
            "couponCode": "NEW100",
            "addressId": "<address_id>",
        },
        "request_schema": {
            "content": {
                "application/json": {
                    "example": {
                        "items": [{"sku": "SKU-1", "qty": 1}],
                        "couponCode": "NEW100",
                        "addressId": "<address_id>",
                    }
                }
            }
        },
    }
    behavior_ir = {
        "operations": [
            operation,
            {
                "id": "op_addresses",
                "method": "GET",
                "path": "/api/users/addresses",
            },
            {"id": "op_get_orders", "method": "GET", "path": "/api/orders"},
            {
                "id": "op_get_order_detail",
                "method": "GET",
                "path": "/api/orders/:id",
            },
        ],
        "actors": [],
        "relations": [],
    }
    plan = build_binding_plan(
        operation=operation,
        obligation={},
        behavior_ir=behavior_ir,
    )
    binding = next(row for row in plan if row.get("target") == "address_id")
    assert binding["status"] == "runtime_resolvable"
    assert binding["resolver_operations"]
    assert (
        binding["resolver_operations"][0]["path"]
        == "/api/users/addresses"
    )
    assert all(
        resolver["path"] != "/api/orders"
        for resolver in binding["resolver_operations"]
    )
