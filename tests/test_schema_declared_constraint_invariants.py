"""Schema names are structure, never undeclared business-rule evidence.

Column/FK names may help retain source-declared field structure, but names such
as ``limit``, ``scope`` and ``status`` do not state how the business must
behave.  Executable invariants require a typed bound/enum or an explicit source
rule; the Behavior IR must not manufacture rules from vocabulary alone.
"""
from __future__ import annotations

from ai_test_asset_center.behavior_ir_core import (
    build_behavior_ir_from_knowledge_asset,
)


_REMOVED_SCHEMA_DERIVATIONS = {
    "schema_declared_constraint",
    "schema_declared_state_gate",
}


def _schema_only_asset() -> dict:
    return {
        "entities": [
            {
                "name": "records",
                "table": "records",
                "fields": ["id", "daily_limit", "category_scope", "status"],
                "identity_fields": ["id"],
            },
            {
                "name": "record_links",
                "table": "record_links",
                "fields": ["id", "record_id"],
                "identity_fields": ["id"],
            },
        ],
        "data_tables": [
            {
                "name": "records",
                "columns": ["id", "daily_limit", "category_scope", "status"],
                "foreign_keys": [],
            },
            {
                "name": "record_links",
                "columns": ["id", "record_id"],
                "foreign_keys": ["records"],
            },
        ],
        "rule_library": [],
        "field_dictionary": [],
        "permission_matrix": [],
        "state_machines": [],
        "relations": [],
    }


def _generic_operations() -> list[dict]:
    return [
        {
            "id": "op_apply_record",
            "operation_id": "api:POST:/records/apply",
            "method": "POST",
            "path": "/records/apply",
            "entity_refs": ["records"],
            "request_example": {"record_id": "record-1"},
            "read_write": "write",
        },
        {
            "id": "op_create_link",
            "operation_id": "api:POST:/record-links",
            "method": "POST",
            "path": "/record-links",
            "request_example": {"record_id": "record-1"},
            "read_write": "write",
        },
    ]


def _removed_schema_invariants(ir: dict) -> list[dict]:
    return [
        invariant
        for invariant in (ir.get("invariants") or [])
        if invariant.get("derived_invariant_kind") in _REMOVED_SCHEMA_DERIVATIONS
    ]


def test_constraint_shaped_column_names_do_not_create_business_invariants() -> None:
    ir = build_behavior_ir_from_knowledge_asset(
        _schema_only_asset(), api_operations=_generic_operations()
    )

    assert _removed_schema_invariants(ir) == []


def test_foreign_key_plus_status_does_not_create_a_state_gate() -> None:
    """An FK proves a structural reference, not which states are consumable."""
    ir = build_behavior_ir_from_knowledge_asset(
        _schema_only_asset(), api_operations=_generic_operations()
    )

    assert not any(
        (invariant.get("expression") or {}).get("operator") == "state_eligible"
        for invariant in (ir.get("invariants") or [])
    )


def test_typed_bounds_and_enum_are_retained_without_inventing_an_invariant() -> None:
    asset = _schema_only_asset()
    asset["data_tables"][0]["field_dictionary"] = [
        {"field": "daily_limit", "type": "integer", "min": 1, "max": 25},
        {"field": "status", "type": "string", "enum": ["OPEN", "CLOSED"]},
    ]

    ir = build_behavior_ir_from_knowledge_asset(
        asset, api_operations=_generic_operations()
    )
    records = next(row for row in ir["entities"] if row.get("name") == "records")
    fields = {
        row.get("name"): row
        for row in records.get("fields", [])
        if isinstance(row, dict) and row.get("name")
    }

    assert fields["daily_limit"]["min_value"] == 1
    assert fields["daily_limit"]["max_value"] == 25
    assert fields["status"]["enum_values"] == ["OPEN", "CLOSED"]
    assert _removed_schema_invariants(ir) == []


def test_explicit_source_rule_remains_the_business_invariant_channel() -> None:
    asset = _schema_only_asset()
    asset["rule_library"] = [
        {
            "rule_id": "rule-record-limit",
            "statement": "A record submission count must be between 1 and 25.",
            "kind": "validation",
            "operator": "within_bound",
            "operation_refs": ["api:POST:/records/apply"],
            "source_id": "requirements",
        }
    ]

    ir = build_behavior_ir_from_knowledge_asset(
        asset, api_operations=_generic_operations()
    )
    source_invariants = [
        invariant
        for invariant in (ir.get("invariants") or [])
        if "rule-record-limit" in (invariant.get("source_rule_refs") or [])
    ]

    assert len(source_invariants) == 1
    assert source_invariants[0]["derivation"] == "explicit"
    assert (source_invariants[0].get("expression") or {}).get("operator") == "within_bound"
    assert _removed_schema_invariants(ir) == []


def test_ddl_check_enum_projects_into_entity_field_enum_values() -> None:
    """A DB CHECK ``col IN (...)`` is source-declared legal-value material.

    The DDL parser emits ``check_constraints`` rows of the shape
    ``{column, operator: "in", values: [...]}``. Those values must reach the
    entity field's ``enum_values`` so example-enum normalization and fixture
    writes judge against the database's real legal set. Without this, a fixture
    body could send a value the DB CHECK rejects (observed: product status
    ACTIVE into ``products_status_check``) and crash the target.
    """
    asset = _schema_only_asset()
    asset["data_tables"][0]["check_constraints"] = [
        {"column": "status", "operator": "in", "values": ["OPEN", "CLOSED"]},
    ]

    ir = build_behavior_ir_from_knowledge_asset(
        asset, api_operations=_generic_operations()
    )
    records = next(row for row in ir["entities"] if row.get("name") == "records")
    fields = {
        row.get("name"): row
        for row in records.get("fields", [])
        if isinstance(row, dict) and row.get("name")
    }

    assert fields["status"]["enum_values"] == ["OPEN", "CLOSED"]
    assert _removed_schema_invariants(ir) == []

