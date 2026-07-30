"""Compile operator-approved storage mappings into formal read-only observer contracts.

Approval confirms only an observation mapping. A contract becomes runtime-bindable only when the
source-declared table has an identity key and every identity field has an approved API value source.
No SQL is generated here, no database is contacted, and no mapping grants write or oracle authority.
"""
from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any, Iterable

DATABASE_OBSERVER_CONTRACT_SCHEMA = "qualibug.database-observer-contract.v1"
DATABASE_OBSERVER_PROJECTION_SCHEMA = "qualibug.database-observer-projection.v1"
DATABASE_OBSERVER_FIELD_BINDING_SCHEMA = "qualibug.database-observer-field-binding.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(_text(value) for value in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


def _dedupe(rows: Iterable[Any], identity_field: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        row = deepcopy(raw)
        identity = _text(row.get(identity_field))
        if not identity or identity in seen:
            continue
        seen.add(identity)
        result.append(row)
    return result


def _identity_keys(table: dict[str, Any]) -> list[list[str]]:
    keys: list[list[str]] = []
    direct = [_text(value) for value in _list(table.get("identity_fields")) if _text(value)]
    if direct:
        keys.append(direct)
    for raw in _list(table.get("identity_keys")):
        if isinstance(raw, dict):
            values = [
                _text(value)
                for value in _list(raw.get("fields") or raw.get("columns"))
                if _text(value)
            ]
        else:
            values = [_text(value) for value in _list(raw) if _text(value)]
        if values and values not in keys:
            keys.append(values)
    return keys


def _value_source(candidate: dict[str, Any]) -> str:
    path = [
        _text(value)
        for value in _list(candidate.get("api_property_path"))
        if _text(value) and _text(value) not in {"[]", "*"}
    ]
    leaf = ".".join(path) or _text(candidate.get("api_field_name"))
    direction = _text(candidate.get("direction"))
    if not leaf:
        return ""
    if direction == "request":
        return f"request.body.{leaf}"
    if direction == "response":
        return f"response.body.{leaf}"
    if direction == "parameter":
        return f"request.parameter.{leaf}"
    return ""


def _lookup(rows: Iterable[Any], identity_field: str) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get(identity_field)): dict(row)
        for row in rows
        if isinstance(row, dict) and _text(row.get(identity_field))
    }


def _mapping_decision_id(candidate: dict[str, Any]) -> str:
    return _text(_dict(candidate.get("mapping_authority")).get("decision_id"))


def _source_evidence(
    *,
    api_binding: dict[str, Any],
    api_field: dict[str, Any] | None = None,
    database_table: dict[str, Any] | None = None,
    database_field: dict[str, Any] | None = None,
    decision_id: str = "",
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for kind, row in (
        ("OPENAPI_OPERATION_SCHEMA_BINDING", api_binding),
        ("OPENAPI_SCHEMA_FIELD", api_field or {}),
        ("DATABASE_TABLE_DECLARATION", database_table or {}),
        ("DATABASE_FIELD_DECLARATION", database_field or {}),
    ):
        locator = _text(row.get("source_locator"))
        source_id = _text(row.get("source_id"))
        asset_ref = _text(
            row.get("binding_id")
            or row.get("field_fact_id")
            or row.get("field_id")
            or row.get("table_id")
        )
        if locator or source_id or asset_ref:
            evidence.append(
                {
                    "kind": kind,
                    "source_id": source_id,
                    "source_locator": locator,
                    "asset_ref": asset_ref,
                    "exact": bool(locator),
                }
            )
    if decision_id:
        evidence.append(
            {
                "kind": "OPERATOR_DATABASE_MAPPING_AUTHORITY",
                "decision_id": decision_id,
                "exact": True,
            }
        )
    return evidence


def enrich_asset_with_database_observer_contracts(
    asset: dict[str, Any],
) -> dict[str, Any]:
    """Compile approved operation-scoped mappings into parameterized read plans."""
    result = dict(asset or {})
    table_candidates = [
        deepcopy(row)
        for row in _list(result.get("api_operation_database_table_candidates"))
        if isinstance(row, dict)
        and _text(row.get("status")) == "APPROVED_READ_ONLY_OBSERVER_TABLE"
        and bool(row.get("observer_authority_allowed"))
    ]
    field_candidates = [
        deepcopy(row)
        for row in _list(result.get("api_operation_database_field_candidates"))
        if isinstance(row, dict)
        and _text(row.get("status")) == "APPROVED_READ_ONLY_OBSERVER_FIELD"
        and bool(row.get("observer_authority_allowed"))
    ]
    tables = _lookup(
        [*_list(result.get("tables")), *_list(result.get("data_tables"))],
        "table_id",
    )
    database_fields = _lookup(result.get("field_dictionary") or [], "field_id")
    api_fields = _lookup(result.get("openapi_schema_fields") or [], "field_fact_id")
    api_bindings = _lookup(result.get("api_operation_schema_bindings") or [], "binding_id")

    fields_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for candidate in field_candidates:
        key = (
            _text(candidate.get("operation_schema_binding_id")),
            _text(candidate.get("database_table_id")),
        )
        fields_by_key.setdefault(key, []).append(candidate)

    contracts: list[dict[str, Any]] = []
    observer_field_bindings: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    relationships = [
        deepcopy(row)
        for row in _list(result.get("relationships"))
        if isinstance(row, dict)
    ]

    for table_candidate in table_candidates:
        binding_id = _text(table_candidate.get("operation_schema_binding_id"))
        table_id = _text(table_candidate.get("database_table_id"))
        key = (binding_id, table_id)
        approved_fields = fields_by_key.get(key, [])
        table = tables.get(table_id, {})
        binding = api_bindings.get(binding_id, {})
        identity_options = _identity_keys(table)

        mappings: list[dict[str, Any]] = []
        by_database_name: dict[str, list[dict[str, Any]]] = {}
        for candidate in approved_fields:
            api_field_id = _text(candidate.get("api_field_id"))
            database_field_id = _text(candidate.get("database_field_id"))
            database_name = _text(candidate.get("database_field_name"))
            source = _value_source(candidate)
            field_binding_id = _stable_id(
                "database_observer_field_binding",
                binding_id,
                table_id,
                api_field_id,
                database_field_id,
            )
            mapping = {
                "schema": DATABASE_OBSERVER_FIELD_BINDING_SCHEMA,
                "field_binding_id": field_binding_id,
                "operation_schema_binding_id": binding_id,
                "interface_id": _text(candidate.get("interface_id")),
                "database_table_id": table_id,
                "api_field_id": api_field_id,
                "api_field_name": _text(candidate.get("api_field_name")),
                "api_property_path": deepcopy(
                    _list(candidate.get("api_property_path"))
                ),
                "database_field_id": database_field_id,
                "database_field_name": database_name,
                "value_source": source,
                "direction": _text(candidate.get("direction")),
                "type_compatibility": deepcopy(
                    _dict(candidate.get("type_compatibility"))
                ),
                "mapping_decision_id": _mapping_decision_id(candidate),
                "authoritative": True,
                "read_only": True,
                "write_target_allowed": False,
                "oracle_authority_allowed": False,
                "evidence": _source_evidence(
                    api_binding=binding,
                    api_field=api_fields.get(api_field_id),
                    database_table=table,
                    database_field=database_fields.get(database_field_id),
                    decision_id=_mapping_decision_id(candidate),
                ),
            }
            mappings.append(mapping)
            if database_name:
                by_database_name.setdefault(database_name, []).append(mapping)

        chosen_identity: list[str] = []
        identity_predicates: list[dict[str, Any]] = []
        for option in identity_options:
            if all(
                len(
                    [
                        row
                        for row in by_database_name.get(field_name, [])
                        if _text(row.get("value_source"))
                    ]
                )
                == 1
                for field_name in option
            ):
                chosen_identity = option
                identity_predicates = [
                    {
                        "database_field_name": field_name,
                        "database_field_id": _text(
                            by_database_name[field_name][0].get("database_field_id")
                        ),
                        "operator": "=",
                        "value_source": _text(
                            by_database_name[field_name][0].get("value_source")
                        ),
                        "field_binding_id": _text(
                            by_database_name[field_name][0].get("field_binding_id")
                        ),
                    }
                    for field_name in option
                ]
                break

        if not approved_fields:
            status = "BLOCKED_NO_APPROVED_OBSERVER_FIELDS"
            reason = "DATABASE_OBSERVER_APPROVED_FIELDS_REQUIRED"
        elif not identity_options:
            status = "BLOCKED_DATABASE_IDENTITY_NOT_DECLARED"
            reason = "DATABASE_OBSERVER_TABLE_IDENTITY_REQUIRED"
        elif not chosen_identity:
            status = "BLOCKED_IDENTITY_FIELD_MAPPING_REQUIRED"
            reason = "DATABASE_OBSERVER_IDENTITY_MAPPING_REQUIRED"
        else:
            status = "READY_FOR_RUNTIME_CONNECTION_BINDING"
            reason = ""

        observer_id = _stable_id(
            "database_observer",
            binding_id,
            table_id,
            *sorted(_text(row.get("field_binding_id")) for row in mappings),
        )
        contract = {
            "schema": DATABASE_OBSERVER_CONTRACT_SCHEMA,
            "observer_id": observer_id,
            "operation_schema_binding_id": binding_id,
            "interface_id": _text(table_candidate.get("interface_id")),
            "method": _text(table_candidate.get("method")),
            "path": _text(table_candidate.get("path")),
            "direction": _text(table_candidate.get("direction")),
            "response_status": _text(table_candidate.get("response_status")),
            "media_type": _text(table_candidate.get("media_type")),
            "api_schema_entity_id": _text(
                table_candidate.get("api_schema_entity_id")
            ),
            "database_table_id": table_id,
            "database_schema_name": _text(
                table_candidate.get("database_schema_name")
                or table.get("schema_name")
            ),
            "database_table_name": _text(
                table.get("name") or table_candidate.get("database_qualified_name")
            ),
            "database_qualified_name": _text(
                table.get("qualified_name")
                or table_candidate.get("database_qualified_name")
            ),
            "table_mapping_decision_id": _mapping_decision_id(table_candidate),
            "field_bindings": mappings,
            "identity_key_options": identity_options,
            "selected_identity_key": chosen_identity,
            "identity_predicates": identity_predicates,
            "query_plan": {
                "operation": "SELECT_ONE",
                "database_table_id": table_id,
                "projection": sorted(
                    {
                        _text(row.get("database_field_name"))
                        for row in mappings
                        if _text(row.get("database_field_name"))
                    }
                ),
                "predicates": identity_predicates,
                "parameterized": True,
                "maximum_rows": 2,
                "raw_sql": "",
            },
            "status": status,
            "reason_code": reason,
            "mapping_authoritative": True,
            "runtime_observer_authoritative": status
            == "READY_FOR_RUNTIME_CONNECTION_BINDING",
            "observer_surface": "database_read_only",
            "read_only": True,
            "mutation_allowed": False,
            "write_target_allowed": False,
            "oracle_authority_allowed": False,
            "runtime_connection_binding_required": True,
            "connection_secret_embedded": False,
            "parameterized_query_required": True,
            "raw_sql_generated": False,
            "database_rows_read": 0,
            "business_flow_inferred": False,
            "evidence": _source_evidence(
                api_binding=binding,
                database_table=table,
                decision_id=_mapping_decision_id(table_candidate),
            ),
        }
        contracts.append(contract)
        for mapping in mappings:
            observer_field_bindings.append(
                {
                    **deepcopy(mapping),
                    "observer_id": observer_id,
                    "observer_status": status,
                    "runtime_observer_authoritative": bool(
                        contract["runtime_observer_authoritative"]
                    ),
                }
            )

        if status == "READY_FOR_RUNTIME_CONNECTION_BINDING":
            relationships.append(
                {
                    "edge_id": f"edge:api-operation-database-observer:{observer_id}",
                    "from": contract["interface_id"],
                    "to": observer_id,
                    "relation": "api_operation_has_database_observer",
                    "confidence": 1.0,
                    "status": "accepted_read_only_observer",
                    "derivation": "operator_approved_database_mapping",
                    "evidence": {
                        "observer_id": observer_id,
                        "table_mapping_decision_id": contract[
                            "table_mapping_decision_id"
                        ],
                        "field_mapping_decision_ids": sorted(
                            {
                                _text(row.get("mapping_decision_id"))
                                for row in mappings
                                if _text(row.get("mapping_decision_id"))
                            }
                        ),
                        "write_target_allowed": False,
                        "oracle_authority_allowed": False,
                    },
                }
            )
        else:
            gaps.append(
                {
                    "kind": "DATABASE_OBSERVER_CONTRACT_BLOCKED",
                    "gap_type": "database_observer_contract_incomplete",
                    "observer_id": observer_id,
                    "interface_id": contract["interface_id"],
                    "database_table_id": table_id,
                    "status": status,
                    "reason_code": reason,
                    "blocks_database_observer_runtime": True,
                    "operator_action": (
                        "approve exact table/field mappings and provide an approved identity "
                        "field value source"
                    ),
                }
            )

    contracts = _dedupe(
        [*_list(result.get("database_observer_contracts")), *contracts],
        "observer_id",
    )
    observer_field_bindings = _dedupe(
        [
            *_list(result.get("approved_database_observer_field_bindings")),
            *observer_field_bindings,
        ],
        "field_binding_id",
    )
    relationships = _dedupe(relationships, "edge_id")
    retained_gaps = [
        deepcopy(row)
        for row in _list(result.get("coverage_gaps"))
        if isinstance(row, dict)
        and _text(row.get("kind")) != "DATABASE_OBSERVER_CONTRACT_BLOCKED"
    ]
    result["database_observer_contracts"] = contracts
    result["approved_database_observer_field_bindings"] = observer_field_bindings
    result["relationships"] = relationships
    result["coverage_gaps"] = [*retained_gaps, *gaps]

    ready_count = sum(
        1
        for row in contracts
        if _text(row.get("status")) == "READY_FOR_RUNTIME_CONNECTION_BINDING"
    )
    result["database_observer_projection"] = {
        "schema": DATABASE_OBSERVER_PROJECTION_SCHEMA,
        "status": (
            "NOT_APPLICABLE"
            if not table_candidates
            else "PARTIAL"
            if gaps
            else "COMPLETE"
        ),
        "approved_table_mapping_count": len(table_candidates),
        "approved_field_mapping_count": len(field_candidates),
        "observer_contract_count": len(contracts),
        "runtime_bindable_observer_count": ready_count,
        "blocked_observer_count": len(gaps),
        "automatic_mapping_count": 0,
        "database_rows_read": 0,
        "raw_sql_generated": False,
        "write_target_authority_count": 0,
        "oracle_authority_count": 0,
        "runtime_connection_binding_required": True,
    }
    summary = _dict(result.get("summary"))
    summary.update(
        {
            "database_observer_contract_count": len(contracts),
            "database_runtime_bindable_observer_count": ready_count,
            "database_observer_blocked_count": len(gaps),
        }
    )
    result["summary"] = summary
    governance = _dict(result.get("governance"))
    governance.update(
        {
            "database_observers_require_operator_approved_mapping": True,
            "database_observers_require_declared_identity": True,
            "database_observers_require_approved_identity_value_source": True,
            "database_observers_are_parameterized_read_only_plans": True,
            "database_observers_embed_no_connection_secrets": True,
            "database_observers_do_not_authorize_writes": True,
            "database_observers_do_not_self_authorize_oracles": True,
        }
    )
    result["governance"] = governance
    return result


__all__ = [
    "DATABASE_OBSERVER_CONTRACT_SCHEMA",
    "DATABASE_OBSERVER_PROJECTION_SCHEMA",
    "DATABASE_OBSERVER_FIELD_BINDING_SCHEMA",
    "enrich_asset_with_database_observer_contracts",
]
