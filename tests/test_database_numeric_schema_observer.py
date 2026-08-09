"""Schema-material-declared DB numeric before/after observer chain.

The approved database observer chain requires operator-approved contracts.
Obligations whose numeric assertion cannot bind to an approved contract were
blocked as ``BLOCKED_DATABASE_NUMERIC_HTTP_FALLBACK_OBSERVER_MISSING``. This
suite verifies the generic schema-material fallback: exact table/field/identity
resolution from the parsed schema material, reusing the existing governed
read-only database phase chain and the existing numeric oracle.
"""
from __future__ import annotations

import json
from copy import deepcopy

import pytest

from ai_test_asset_center.database_numeric_schema_observer import (
    attach_schema_numeric_observation,
    resolve_schema_numeric_observation,
)
from ai_test_asset_center.database_observer_experiment_runtime import (
    PHASE_AGGREGATE_OBSERVER_ID,
)
from ai_test_asset_center.database_observer_runtime import _validate_contract
from ai_test_asset_center.database_numeric_oracle import (
    evaluate_database_numeric_conservation,
)
from ai_test_asset_center.database_numeric_experiment_projection import (
    project_database_numeric_assertions,
)
from ai_test_asset_center.runtime_materialization_experiment_bridge import (
    capture_enterprise_runtime_materializations,
)


def _schema_view() -> dict:
    return {
        "tables": [
            {
                "table_id": "table:inventory",
                "source_id": "src_schema",
                "name": "inventory",
                "columns": [
                    "available_qty",
                    "locked_qty",
                    "safety_stock",
                    "sku",
                    "updated_at",
                    "warehouse_code",
                ],
                "identity_fields": ["sku"],
                "identity_keys": [],
            },
            {
                "table_id": "table:cart_items",
                "source_id": "src_schema",
                "name": "cart_items",
                "columns": ["created_at", "id", "price_snapshot", "qty", "selected", "sku", "user_id"],
                "identity_fields": ["id"],
                "identity_keys": [],
            },
            {
                "table_id": "table:order_items",
                "source_id": "src_schema",
                "name": "order_items",
                "columns": ["id", "line_amount", "order_id", "price", "qty", "sku", "title"],
                "identity_fields": ["id"],
                "identity_keys": [],
            },
            {
                "table_id": "table:users",
                "source_id": "src_schema",
                "name": "users",
                "columns": ["balance", "created_at", "email", "id", "name", "password", "phone", "role", "status"],
                "identity_fields": ["email", "id"],
                "identity_keys": [],
            },
        ],
        "fields": [
            {
                "field_id": "field:src_schema:e4c7",
                "table_id": "table:inventory",
                "field": "available_qty",
                "field_path": "inventory.available_qty",
            },
            {
                "field_id": "field:src_schema:c848",
                "table_id": "table:inventory",
                "field": "locked_qty",
                "field_path": "inventory.locked_qty",
            },
        ],
    }


def _conservation_experiment(
    *,
    terms: list[str],
    body: dict | None = None,
    operands: list[dict] | None = None,
) -> dict:
    body = body if body is not None else {"orderId": "{orderId}", "qty": 1, "sku": "SKU-PHONE-001"}
    operands = operands if operands is not None else [
        {
            "entity_ref": "bir_entity",
            "field": "available_qty",
            "field_id": "cf_18f38a780c0384a3",
            "semantic_type": "QUANTITY_BALANCE",
        }
    ]
    assertion = {
        "assertion_id": "assert_conservation",
        "kind": "conservation",
        "template": "invariant_conservation",
        "equation": {"operator": "unchanged_sum", "terms": terms},
        "operands": deepcopy(operands),
        "property": {
            "template": "invariant_conservation",
            "expression": {
                "kind": "data_conservation",
                "operator": "must_hold",
                "operands": deepcopy(operands),
                "equation": {"operator": "unchanged_sum", "terms": terms},
            },
        },
    }
    return {
        "experiment_id": "exp_schema_numeric_test",
        "obligation_id": "obl_schema_numeric_test",
        "risk_family": "conservation",
        "operation_ref": "bir_op",
        "assertions": [assertion],
        "field_oracle_runtime_contract": {
            "schema_version": "qualibug.field-oracle-runtime-contract.v1",
            "rule_id": "bir_rule",
            "rule_type": "conservation",
            "assertion_kind": "conservation",
            "required_field_ids": ["cf_18f38a780c0384a3", "available_qty"],
            "typed_expression": {
                "kind": "data_conservation",
                "operator": "must_hold",
                "operands": deepcopy(operands),
                "equation": {"operator": "unchanged_sum", "terms": terms},
            },
        },
        "treatment_plan": [
            {
                "step_id": "treatment_1",
                "operation_ref": "bir_op",
                "path": "/api/inventory/release",
                "body": body,
            }
        ],
        "control_plan": [],
        "observers": [
            {"observer_id": "typed_assertion", "adapter": "http_api"},
            {"observer_id": "source_invariant", "adapter": "http_api"},
            {"observer_id": "entity_state", "adapter": "http_api"},
        ],
        "compile_receipt": {"status": "COMPILED"},
    }


def _capture_schema(project: str = "test_project") -> None:
    view = _schema_view()
    asset = {
        "data_tables": [
            {**table, "field_declarations": []} for table in view["tables"]
        ],
        "field_dictionary": view["fields"],
    }
    capture_enterprise_runtime_materializations(project, asset)


# ── resolution unit tests ───────────────────────────────────────────────────


def test_resolve_bound_single_table():
    experiment = _conservation_experiment(terms=["available_qty"])
    result = resolve_schema_numeric_observation(experiment, _schema_view())
    assert result["status"] == "BOUND"
    assert len(result["contracts"]) == 1
    assert len(result["drafts"]) == 2
    contract = result["contracts"][0]
    assert contract["database_table_id"] == "table:inventory"
    assert contract["database_table_name"] == "inventory"
    assert contract["selected_identity_key"] == ["sku"]
    assert contract["identity_predicates"] == [
        {
            "database_field_name": "sku",
            "database_field_id": contract["identity_predicates"][0]["database_field_id"],
            "operator": "=",
            "value_source": "request.body.sku",
        }
    ]
    assert contract["query_plan"]["maximum_rows"] == 2
    assert contract["read_only"] is True
    assert contract["mutation_allowed"] is False
    assert contract["write_target_allowed"] is False
    assert contract["oracle_authority_allowed"] is False
    assert contract["derivation"] == "source_declared_schema_material"
    phases = {row["observation_phase"] for row in result["drafts"]}
    assert phases == {"BEFORE", "AFTER"}
    for draft in result["drafts"]:
        assert draft["required"] is True
        assert draft["observer_handler_id"] == "approved_database_readback"


def test_resolve_contract_passes_runtime_validator():
    experiment = _conservation_experiment(terms=["available_qty"])
    result = resolve_schema_numeric_observation(experiment, _schema_view())
    assert result["status"] == "BOUND"
    contract = _validate_contract(result["contracts"][0])
    assert contract["status"] == "READY_FOR_RUNTIME_CONNECTION_BINDING"


def test_resolve_ambiguous_column_fails_closed():
    # qty exists in cart_items and order_items without an entity hint.
    experiment = _conservation_experiment(terms=["qty"], operands=[])
    result = resolve_schema_numeric_observation(experiment, _schema_view())
    assert result["status"] == "UNRESOLVED"
    assert result["drafts"] == []
    assert any(
        "SCHEMA_NUMERIC_COLUMN_AMBIGUOUS:qty" in row.get("reason_code", "")
        for row in result["gaps"]
    )


def test_resolve_entity_scope_fail_closed_no_global_fallback():
    # The source scopes the term to inventory, which has no qty column. A global
    # search would find cart_items/order_items — that must NOT happen.
    experiment = _conservation_experiment(
        terms=["qty"],
        operands=[{"entity_ref": "inventory", "field": "qty", "field_id": "cf_q"}],
    )
    result = resolve_schema_numeric_observation(experiment, _schema_view())
    assert result["status"] == "UNRESOLVED"
    assert any(
        "SCHEMA_NUMERIC_COLUMN_NOT_FOUND_IN_ENTITY_SCOPE:qty"
        in row.get("reason_code", "")
        for row in result["gaps"]
    )


def test_resolve_identity_value_source_missing():
    view = deepcopy(_schema_view())
    view["tables"][0]["identity_fields"] = ["sku"]
    # Body without sku key.
    experiment = _conservation_experiment(
        terms=["available_qty"],
        body={"orderId": "{orderId}", "qty": 1},
    )
    result = resolve_schema_numeric_observation(experiment, view)
    assert result["status"] == "UNRESOLVED"
    assert any(
        "SCHEMA_NUMERIC_IDENTITY_VALUE_SOURCE_MISSING" in row.get("reason_code", "")
        for row in result["gaps"]
    )


def test_resolve_no_schema_view_fails_closed():
    experiment = _conservation_experiment(terms=["available_qty"])
    result = resolve_schema_numeric_observation(experiment, {})
    assert result["status"] == "UNRESOLVED"
    assert result["drafts"] == []


def test_attach_adds_drafts_and_phase_aggregate_observer():
    experiment = _conservation_experiment(terms=["available_qty"])
    attached = attach_schema_numeric_observation(experiment, _schema_view())
    assert (
        attached["database_numeric_schema_observer"]["status"] == "BOUND"
    )
    drafts = attached.get("database_observer_execution_drafts") or []
    assert len(drafts) == 2
    observer_ids = {
        row.get("observer_id") for row in attached.get("observers") or []
    }
    assert PHASE_AGGREGATE_OBSERVER_ID in observer_ids
    assert attached.get("database_observer_phase_receipts_required") is True


# ── planning-level projection tests ─────────────────────────────────────────


def test_projection_unblocks_schema_bound_conservation():
    _capture_schema()
    experiment = _conservation_experiment(terms=["available_qty"])
    pack = project_database_numeric_assertions({"experiments": [experiment]})
    assert pack["blocked_experiments"] == []
    assert len(pack["experiments"]) == 1
    bound = pack["experiments"][0]
    assert bound.get("database_numeric_projection_status") == "BOUND"
    kinds = {
        row.get("kind")
        for row in bound.get("assertions") or []
    }
    assert "database_numeric_conservation" in kinds
    observer_ids = {
        row.get("observer_id") for row in bound.get("observers") or []
    }
    assert PHASE_AGGREGATE_OBSERVER_ID in observer_ids
    drafts = bound.get("database_observer_execution_drafts") or []
    assert len(drafts) == 2
    receipt = bound.get("compile_receipt") or {}
    assert receipt.get("database_numeric_projection_status") == "BOUND"
    assert receipt.get("database_numeric_schema_observer_status") == "BOUND"
    summary = pack.get("database_numeric_experiment_projection") or {}
    assert summary.get("schema_observer_bound_experiment_count") == 1
    # The numeric term must carry the exact schema-declared binding.
    numeric_rows = [
        row for row in bound.get("assertions") or []
        if row.get("kind") == "database_numeric_conservation"
    ]
    assert numeric_rows
    terms = numeric_rows[0].get("numeric_terms") or []
    assert terms
    assert terms[0]["database_table_ref"] == "table:inventory"
    assert terms[0]["database_field_name"] == "available_qty"
    assert terms[0]["match_basis"] == "EXACT_FIELD_NAME"


def test_projection_keeps_ambiguous_blocked_with_gap_detail():
    _capture_schema()
    experiment = _conservation_experiment(terms=["qty"], operands=[])
    pack = project_database_numeric_assertions({"experiments": [experiment]})
    blocked = pack.get("blocked_experiments") or []
    assert len(blocked) == 1
    receipt = blocked[0].get("compile_receipt") or {}
    assert (
        receipt.get("reason_code")
        == "BLOCKED_DATABASE_NUMERIC_HTTP_FALLBACK_OBSERVER_MISSING"
    )
    # Gap detail must now be attached (previously lost on the blocked path).
    assert blocked[0].get("database_numeric_projection_gaps")
    assert blocked[0]["database_numeric_schema_observer"]["status"] == "UNRESOLVED"


def test_projection_no_schema_view_behaves_as_before():
    # Without a captured schema view the fallback is not attempted and the
    # legacy block is preserved byte-for-byte in reason code.
    capture_enterprise_runtime_materializations("test_project", {})
    experiment = _conservation_experiment(terms=["available_qty"])
    pack = project_database_numeric_assertions({"experiments": [experiment]})
    blocked = pack.get("blocked_experiments") or []
    assert len(blocked) == 1
    receipt = blocked[0].get("compile_receipt") or {}
    assert (
        receipt.get("reason_code")
        == "BLOCKED_DATABASE_NUMERIC_HTTP_FALLBACK_OBSERVER_MISSING"
    )


# ── numeric oracle consumption of phase receipts ────────────────────────────


def _phase_receipts(before_value, after_value):
    def receipt(phase, value):
        return {
            "draft_id": f"draft_{phase.lower()}",
            "observation_phase": phase,
            "observer_contract_ref": "obs_schema_inventory",
            "receipt_id": f"receipt_{phase.lower()}",
            "observer_id": "approved_database_readback",
            "status": "OBSERVED",
            "evidence": {
                "approved_database_snapshot": {
                    "database_table_ref": "table:inventory",
                    "database_table_name": "inventory",
                    "match_status": "MATCHED_ONE",
                    "row_count": 1,
                    "row_fingerprint": f"fp_{phase.lower()}",
                    "identity_key": ["sku"],
                    "identity_parameter_fingerprints": ["fp_identity"],
                    "rows": [{"available_qty": value}],
                }
            },
            "oracle_verdict_emitted": False,
            "campaign_id": "CMP",
            "execution_id": "EXE",
        }

    return [receipt("BEFORE", before_value), receipt("AFTER", after_value)]


def _numeric_spec() -> dict:
    return {
        "kind": "database_numeric_conservation",
        "numeric_policy": "UNCHANGED_WEIGHTED_SUM",
        "numeric_terms": [
            {
                "term_id": "numeric-term:0",
                "database_observer_contract_ref": "obs_schema_inventory",
                "before_draft_id": "draft_before",
                "after_draft_id": "draft_after",
                "database_table_ref": "table:inventory",
                "database_table_name": "inventory",
                "database_field_id": "field:schema:inventory:available_qty",
                "database_field_name": "available_qty",
                "field_binding_id": "binding:inventory:available_qty",
                "coefficient": 1,
            }
        ],
    }


def test_numeric_conservation_oracle_consumes_phase_receipts():
    # Same value before/after -> conserved.
    passed = evaluate_database_numeric_conservation(
        {
            "spec": _numeric_spec(),
            "observations": {"approved_database_observer_phase_receipts": _phase_receipts(10, 10)},
        }
    )
    assert passed["passed"] is True
    assert passed["reason_code"] == ""

    # Changed value -> violation.
    violated = evaluate_database_numeric_conservation(
        {
            "spec": _numeric_spec(),
            "observations": {"approved_database_observer_phase_receipts": _phase_receipts(10, 7)},
        }
    )
    assert violated["passed"] is False
    assert violated["reason_code"] == "DATABASE_NUMERIC_CONSERVATION_VIOLATED"
    assert violated["actual"]["difference"] == "-3"


def test_numeric_oracle_fails_closed_on_missing_phase_pair():
    result = evaluate_database_numeric_conservation(
        {
            "spec": _numeric_spec(),
            "observations": {
                "approved_database_observer_phase_receipts": _phase_receipts(10, 10)[:1]
            },
        }
    )
    assert result["passed"] is None
    assert result["reason_code"] == "DATABASE_NUMERIC_SNAPSHOT_PAIR_MISSING"
