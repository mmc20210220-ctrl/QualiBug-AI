"""Compile approved FK relation candidates into read-only child collection contracts."""
from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any, Iterable

DATABASE_RELATION_OBSERVER_CONTRACT_SCHEMA = "qualibug.database-relation-observer-contract.v1"
DATABASE_RELATION_OBSERVER_PROJECTION_SCHEMA = "qualibug.database-relation-observer-projection.v1"


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
    output: dict[str, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        identity = _text(raw.get(identity_field))
        if identity:
            output[identity] = deepcopy(raw)
    return list(output.values())


def enrich_asset_with_database_relation_observer_contracts(
    asset: dict[str, Any],
) -> dict[str, Any]:
    """Rebuild formal relation contracts from the current valid approval set."""
    result = dict(asset or {})
    contracts: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    relationships = [
        deepcopy(row)
        for row in _list(result.get("relationships"))
        if isinstance(row, dict)
        and _text(row.get("relation")) != "database_observer_has_child_collection"
        and not _text(row.get("edge_id")).startswith("edge:database-relation-observer:")
    ]

    for raw in _list(result.get("database_relation_observer_candidates")):
        candidate = _dict(raw)
        if (
            _text(candidate.get("status")) != "APPROVED_READ_ONLY_RELATION_OBSERVER"
            or candidate.get("observer_authority_allowed") is not True
            or candidate.get("relation_mapping_confirmed") is not True
        ):
            continue
        pairs = [dict(row) for row in _list(candidate.get("predicate_pairs")) if isinstance(row, dict)]
        fields = [dict(row) for row in _list(candidate.get("available_child_fields")) if isinstance(row, dict)]
        decision_id = _text(_dict(candidate.get("relation_authority")).get("decision_id"))
        valid_pairs = bool(pairs) and all(
            _text(row.get("child_database_field_name"))
            and _text(row.get("parent_database_field_name"))
            and _text(row.get("parent_field_binding_id"))
            and _text(row.get("value_source"))
            for row in pairs
        )
        if not valid_pairs:
            reason = "DATABASE_RELATION_CONTRACT_PREDICATE_PAIR_INCOMPLETE"
        elif not fields:
            reason = "DATABASE_RELATION_CONTRACT_CHILD_FIELD_CATALOG_MISSING"
        elif not decision_id:
            reason = "DATABASE_RELATION_CONTRACT_AUTHORITY_DECISION_MISSING"
        else:
            reason = ""

        contract_id = _stable_id(
            "database_relation_observer",
            candidate.get("candidate_id"),
            decision_id,
        )
        ready = not reason
        contract = {
            "schema": DATABASE_RELATION_OBSERVER_CONTRACT_SCHEMA,
            "relation_observer_id": contract_id,
            "candidate_id": _text(candidate.get("candidate_id")),
            "relation_mapping_decision_id": decision_id,
            "root_observer_id": _text(candidate.get("root_observer_id")),
            "operation_schema_binding_id": _text(candidate.get("operation_schema_binding_id")),
            "interface_id": _text(candidate.get("interface_id")),
            "method": _text(candidate.get("method")),
            "path": _text(candidate.get("path")),
            "database_relationship_id": _text(candidate.get("database_relationship_id")),
            "parent_table_id": _text(candidate.get("parent_table_id")),
            "parent_schema_name": _text(candidate.get("parent_schema_name")),
            "parent_table_name": _text(candidate.get("parent_table_name")),
            "parent_columns": deepcopy(_list(candidate.get("parent_columns"))),
            "child_table_id": _text(candidate.get("child_table_id")),
            "child_schema_name": _text(candidate.get("child_schema_name")),
            "child_table_name": _text(candidate.get("child_table_name")),
            "child_columns": deepcopy(_list(candidate.get("child_columns"))),
            "relation_predicates": [
                {
                    "ordinal": row.get("ordinal"),
                    "child_database_field_name": _text(row.get("child_database_field_name")),
                    "parent_database_field_name": _text(row.get("parent_database_field_name")),
                    "parent_database_field_id": _text(row.get("parent_database_field_id")),
                    "parent_field_binding_id": _text(row.get("parent_field_binding_id")),
                    "operator": "=",
                    "value_source": _text(row.get("value_source")),
                }
                for row in pairs
            ],
            "allowed_child_fields": fields,
            "query_plan": {
                "operation": "SELECT_MANY",
                "parameterized": True,
                "maximum_rows": 10000,
                "allowed_aggregates": ["COUNT", "SUM", "MIN", "MAX"],
                "order_by": [],
                "raw_sql": "",
                "client_side_filter": False,
            },
            "status": "READY_FOR_RUNTIME_CONNECTION_BINDING" if ready else "BLOCKED",
            "reason_code": reason,
            "runtime_observer_authoritative": ready,
            "mapping_authoritative": True,
            "read_only": True,
            "mutation_allowed": False,
            "write_target_allowed": False,
            "oracle_authority_allowed": False,
            "business_mapping_authority_allowed": False,
            "runtime_connection_binding_required": True,
            "connection_secret_embedded": False,
            "parameterized_query_required": True,
            "raw_sql_generated": False,
            "client_side_filter_allowed": False,
            "database_rows_read": 0,
            "evidence": {
                "source_id": _text(candidate.get("source_id")),
                "source_locator": _text(candidate.get("source_locator")),
                "relationship_evidence": deepcopy(_dict(candidate.get("relationship_evidence"))),
                "root_mapping_decision_refs": deepcopy(_list(candidate.get("root_mapping_decision_refs"))),
                "relation_mapping_decision_id": decision_id,
                "exact_foreign_key": True,
            },
        }
        contracts.append(contract)
        if ready:
            relationships.append(
                {
                    "edge_id": f"edge:database-relation-observer:{contract_id}",
                    "from": contract["root_observer_id"],
                    "to": contract_id,
                    "relation": "database_observer_has_child_collection",
                    "confidence": 1.0,
                    "status": "accepted_read_only_relation_observer",
                    "derivation": "operator_approved_database_foreign_key_relation",
                    "evidence": {
                        "database_relationship_id": contract["database_relationship_id"],
                        "relation_mapping_decision_id": decision_id,
                        "write_target_allowed": False,
                        "oracle_authority_allowed": False,
                    },
                }
            )
        else:
            gaps.append(
                {
                    "kind": "DATABASE_RELATION_OBSERVER_CONTRACT_BLOCKED",
                    "gap_type": "database_relation_observer_contract_incomplete",
                    "relation_observer_id": contract_id,
                    "candidate_id": contract["candidate_id"],
                    "reason_code": reason,
                    "blocks_relation_runtime": True,
                }
            )

    contracts = _dedupe(contracts, "relation_observer_id")
    relationships = _dedupe(relationships, "edge_id")
    retained_gaps = [
        deepcopy(row)
        for row in _list(result.get("coverage_gaps"))
        if isinstance(row, dict)
        and _text(row.get("kind")) != "DATABASE_RELATION_OBSERVER_CONTRACT_BLOCKED"
    ]
    result["database_relation_observer_contracts"] = contracts
    result["relationships"] = relationships
    result["coverage_gaps"] = [*retained_gaps, *gaps]
    ready_count = sum(
        1 for row in contracts if _text(row.get("status")) == "READY_FOR_RUNTIME_CONNECTION_BINDING"
    )
    result["database_relation_observer_projection"] = {
        "schema": DATABASE_RELATION_OBSERVER_PROJECTION_SCHEMA,
        "status": "NOT_APPLICABLE" if not contracts else "PARTIAL" if gaps else "COMPLETE",
        "contract_count": len(contracts),
        "runtime_bindable_contract_count": ready_count,
        "blocked_contract_count": len(gaps),
        "automatic_relation_mapping_count": 0,
        "client_side_filter_count": 0,
        "database_rows_read": 0,
        "raw_sql_generated": False,
        "write_target_authority_count": 0,
        "oracle_authority_count": 0,
    }
    summary = _dict(result.get("summary"))
    summary.update(
        {
            "database_relation_observer_contract_count": len(contracts),
            "database_relation_runtime_bindable_contract_count": ready_count,
        }
    )
    result["summary"] = summary
    governance = _dict(result.get("governance"))
    governance.update(
        {
            "database_relation_observers_require_explicit_relation_approval": True,
            "database_relation_observers_use_exact_foreign_keys": True,
            "database_relation_observers_are_parameterized_read_only_plans": True,
            "database_relation_observers_forbid_client_side_filtering": True,
            "database_relation_observers_do_not_authorize_writes": True,
            "database_relation_observers_do_not_self_authorize_oracles": True,
        }
    )
    result["governance"] = governance
    return result


__all__ = [
    "DATABASE_RELATION_OBSERVER_CONTRACT_SCHEMA",
    "DATABASE_RELATION_OBSERVER_PROJECTION_SCHEMA",
    "enrich_asset_with_database_relation_observer_contracts",
]
