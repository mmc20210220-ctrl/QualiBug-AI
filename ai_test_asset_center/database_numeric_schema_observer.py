"""Schema-material-declared database numeric before/after observer chain.

The approved database observer chain (operator-approved contracts -> runtime
plans -> execution drafts -> BEFORE/AFTER read-only phase receipts -> numeric
oracle) is complete, but it requires an operator approval decision. Obligations
whose numeric assertion cannot bind to an approved contract were therefore
blocked as ``BLOCKED_DATABASE_NUMERIC_HTTP_FALLBACK_OBSERVER_MISSING`` (380 in
the authenticated run) — a structural observer-capability gap, not a data
problem.

This module closes that gap with a *schema-material-declared* variant of the
same chain. It is NOT a new observer implementation and NOT a new oracle:

* The read-only SQL execution, governance gate, identifier introspection and
  BEFORE/AFTER phase receipts all remain owned by the existing
  ``database_observer_runtime`` / ``database_observer_experiment_runtime``
  chain (``approved_database_readback`` + ``approved_database_phase_aggregate``).
* The numeric verdict remains owned by the existing
  ``database_numeric_oracle`` assertion kinds. This module never opens a
  connection and never authors a verdict.
* What it adds is a compile-time binding source: the exact table/field/identity
  facts parsed from the customer-declared schema material
  (``data_tables`` / ``field_dictionary`` in the knowledge asset), so a
  numeric obligation can be observed WITHOUT an operator approval step.

Fail-closed rules (identical spirit to the approved chain):

* Column resolution is exact-name only, and must be unambiguous. A name that
  matches no declared column, or matches columns in more than one table
  (without an exact entity hint), is a named gap — never a guess.
* The table must declare an identity column, and that identity column must
  resolve to exactly one value source in the compiled treatment request
  (body / path parameter / query). No identity, no source, or an ambiguous
  source is a named gap.
* The synthesized contract is validated with the same runtime contract
  validator that gates execution, at compile time, so a binding that the
  runtime would refuse is refused here.
* No business semantics are inferred: no entity/table-name aliasing, no
  fuzzy matching, no industry vocabulary, no automatic conflict winner.
"""
from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from typing import Any, Iterable

from .database_observer_experiment_runtime import PHASE_AGGREGATE_OBSERVER_ID
from .database_observer_runtime import (
    _validate_contract as _validate_runtime_contract,
)

SCHEMA_NUMERIC_OBSERVER_SCHEMA = (
    "qualibug.database-numeric-schema-observer.v1"
)
CONTRACT_SCHEMA = "qualibug.database-observer-contract.v1"
DRAFT_SCHEMA = "qualibug.database-observer-execution-draft.v1"
FIELD_BINDING_SCHEMA = "qualibug.database-observer-field-binding.v1"
DERIVATION = "source_declared_schema_material"
DIRECT_READBACK_HANDLER_ID = "approved_database_readback"
_SOURCE_KINDS = frozenset({"field_delta", "conservation"})
_OPERATOR_TERMS = frozenset({"unchanged_sum", "conservation"})
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_ID_KEYS = frozenset(
    {
        "field_id",
        "field_ref",
        "api_field_id",
        "database_field_id",
        "field_binding_id",
    }
)
_NAME_KEYS = frozenset(
    {
        "field",
        "field_name",
        "api_field_name",
        "database_field_name",
        "json_path",
        "name",
    }
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(_text(value) for value in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


def _name_token(value: Any) -> str:
    text = _text(value)
    if text.startswith("$."):
        text = text[2:]
    text = text.replace("[]", "").strip(".")
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text


def _walk_tokens(value: Any, ids: set[str], names: set[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = _text(key).lower()
            if normalized in _ID_KEYS and _text(child):
                ids.add(_text(child))
            elif normalized in _NAME_KEYS and _name_token(child):
                names.add(_name_token(child))
            if isinstance(child, (dict, list)):
                _walk_tokens(child, ids, names)
    elif isinstance(value, list):
        for child in value:
            _walk_tokens(child, ids, names)


def _looks_like_identifier(value: Any) -> bool:
    text = _text(value)
    return bool(
        text.startswith("#/")
        or ":" in text
        or text.startswith("field_")
        or text.startswith("binding_")
    )


def _source_kind(assertion: dict[str, Any]) -> str:
    return _text(assertion.get("kind") or assertion.get("type")).lower()


def _delta_terms(assertion: dict[str, Any]) -> list[Any]:
    terms = _list(assertion.get("fields") or assertion.get("operands"))
    if not terms:
        return []
    if len(terms) == 1 and isinstance(terms[0], dict):
        return [terms[0]]
    return terms


def _conservation_terms(assertion: dict[str, Any]) -> list[Any]:
    equation = _dict(assertion.get("equation"))
    if _text(equation.get("operator")).lower() not in _OPERATOR_TERMS:
        return []
    terms = _list(equation.get("terms") or equation.get("fields"))
    if not terms:
        property_expression = _dict(
            _dict(assertion.get("property")).get("expression")
        )
        terms = _list(
            _dict(property_expression.get("equation")).get("terms")
            or _dict(property_expression.get("equation")).get("fields")
        )
    return terms


def _assertion_raw_terms(assertion: dict[str, Any]) -> list[Any]:
    kind = _source_kind(assertion)
    if kind not in _SOURCE_KINDS:
        return []
    return (
        _delta_terms(assertion) if kind == "field_delta" else _conservation_terms(assertion)
    )


def _term_tokens(raw: Any) -> tuple[set[str], set[str]]:
    ids: set[str] = set()
    names: set[str] = set()
    if isinstance(raw, str):
        text = _text(raw)
        if _looks_like_identifier(text):
            ids.add(text)
        elif _name_token(text):
            names.add(_name_token(text))
    else:
        _walk_tokens(raw, ids, names)
    return ids, names


def _field_id_to_name_pairs(experiment: dict[str, Any]) -> dict[str, str]:
    """Exact ``field_id -> field`` pairs declared on the obligation itself."""
    pairs: dict[str, str] = {}
    for row in _list(experiment.get("assertions")):
        for raw in _list(_dict(row).get("operands")):
            operand = _dict(raw)
            field_id = _text(operand.get("field_id"))
            field = _text(operand.get("field"))
            if field_id and field:
                pairs[field_id] = field
        property_expression = _dict(
            _dict(_dict(row).get("property")).get("expression")
        )
        for raw in _list(property_expression.get("operands")):
            operand = _dict(raw)
            field_id = _text(operand.get("field_id"))
            field = _text(operand.get("field"))
            if field_id and field:
                pairs[field_id] = field
    runtime_contract = _dict(experiment.get("field_oracle_runtime_contract"))
    typed_expression = _dict(runtime_contract.get("typed_expression"))
    for raw in _list(typed_expression.get("operands")):
        operand = _dict(raw)
        field_id = _text(operand.get("field_id"))
        field = _text(operand.get("field"))
        if field_id and field:
            pairs[field_id] = field
    return pairs


def _entity_table_hints(assertion: dict[str, Any]) -> set[str]:
    """Exact entity_ref values that are also declared table names (source data)."""
    hints: set[str] = set()
    for raw in _list(assertion.get("operands")):
        entity_ref = _text(_dict(raw).get("entity_ref"))
        if entity_ref:
            hints.add(entity_ref)
    property_expression = _dict(
        _dict(assertion.get("property")).get("expression")
    )
    for raw in _list(property_expression.get("operands")):
        entity_ref = _text(_dict(raw).get("entity_ref"))
        if entity_ref:
            hints.add(entity_ref)
    return hints


def _tables_by_name(schema_view: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    by_name: dict[str, list[dict[str, Any]]] = {}
    for raw in _list(schema_view.get("tables")):
        row = _dict(raw)
        name = _text(row.get("name"))
        if name:
            by_name.setdefault(name, []).append(row)
    return by_name


def _column_tables(schema_view: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    by_column: dict[str, list[dict[str, Any]]] = {}
    for raw in _list(schema_view.get("tables")):
        row = _dict(raw)
        for column in _list(row.get("columns")):
            name = _text(column)
            if name:
                by_column.setdefault(name, []).append(row)
    return by_column


def _identity_columns(table: dict[str, Any]) -> list[str]:
    columns: list[str] = []
    seen: set[str] = set()
    for raw in _list(table.get("identity_keys")):
        for column in _list(_dict(raw).get("columns")):
            name = _text(column)
            if name and name not in seen:
                seen.add(name)
                columns.append(name)
    for raw in _list(table.get("identity_fields")):
        name = _text(raw)
        if name and name not in seen:
            seen.add(name)
            columns.append(name)
    return columns


def _request_value_sources(experiment: dict[str, Any]) -> dict[str, list[str]]:
    """Map request key -> value sources (request.body.* / request.parameter.*)."""
    sources: dict[str, list[str]] = {}
    for raw in _list(experiment.get("treatment_plan")):
        step = _dict(raw)
        body = _dict(step.get("body"))
        for key in body:
            if _text(key):
                sources.setdefault(key, []).append(f"request.body.{key}")
        path = _text(step.get("path"))
        for match in re.finditer(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", path):
            name = match.group(1)
            if name:
                sources.setdefault(name, []).append(f"request.parameter.{name}")
        for collection in ("path_parameters", "query", "query_parameters"):
            for key in _dict(step.get(collection)):
                if _text(key):
                    sources.setdefault(key, []).append(
                        f"request.parameter.{key}"
                    )
    return sources


def _resolve_term_column(
    names: set[str],
    *,
    table_hints: set[str],
    tables_by_name: dict[str, list[dict[str, Any]]],
    column_tables: dict[str, list[dict[str, Any]]],
) -> tuple[str, str, str]:
    """Resolve one term to exactly one (table_id, table_name, column).

    Returns ``("", "", "")`` with an empty reason only on success; otherwise the
    reason string is non-empty (fail-closed).
    """
    for name in sorted(names):
        if not _SAFE_IDENTIFIER.fullmatch(name):
            continue
        if table_hints:
            hinted: dict[str, dict[str, Any]] = {}
            for hint in sorted(table_hints):
                for table in tables_by_name.get(hint, []):
                    if name in set(_list(table.get("columns"))):
                        hinted[_text(table.get("table_id"))] = table
            if hinted:
                if len(hinted) != 1:
                    return (
                        "",
                        "",
                        f"SCHEMA_NUMERIC_COLUMN_ENTITY_SCOPE_AMBIGUOUS:{name}",
                    )
                table = next(iter(hinted.values()))
                return (
                    _text(table.get("table_id")),
                    _text(table.get("name")),
                    name,
                )
            declared_hints = {
                hint for hint in table_hints if hint in tables_by_name
            }
            if declared_hints:
                # The source itself scoped this term to declared entities, and
                # none of those tables carries the column. Falling back to a
                # global column search could bind an unrelated table — fail
                # closed instead of guessing.
                return (
                    "",
                    "",
                    f"SCHEMA_NUMERIC_COLUMN_NOT_FOUND_IN_ENTITY_SCOPE:{name}",
                )
            # Entity refs that name no declared table (e.g. Behavior IR node
            # ids) do not scope the search; fall through to the global check.
        matches = column_tables.get(name, [])
        if not matches:
            return "", "", f"SCHEMA_NUMERIC_COLUMN_NOT_FOUND:{name}"
        if len(matches) != 1:
            return "", "", f"SCHEMA_NUMERIC_COLUMN_AMBIGUOUS:{name}"
        table = matches[0]
        return (
            _text(table.get("table_id")),
            _text(table.get("name")),
            name,
        )
    return "", "", "SCHEMA_NUMERIC_TERM_NAME_MISSING"


def _resolve_identity_value_source(
    table: dict[str, Any],
    request_sources: dict[str, list[str]],
) -> tuple[str, str]:
    for column in _identity_columns(table):
        if not _SAFE_IDENTIFIER.fullmatch(column):
            continue
        candidates = request_sources.get(column)
        if not candidates:
            continue
        if len(candidates) == 1:
            return column, candidates[0]
        return "", f"SCHEMA_NUMERIC_IDENTITY_VALUE_SOURCE_AMBIGUOUS:{column}"
    return "", "SCHEMA_NUMERIC_IDENTITY_VALUE_SOURCE_MISSING"


def _term_evidence(table: dict[str, Any], column: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    table_source = _text(table.get("source_id"))
    if table_source:
        items.append(
            {
                "kind": "DATABASE_TABLE_DECLARATION",
                "source_id": table_source,
                "source_locator": f"table:{_text(table.get('name'))}",
                "asset_ref": _text(table.get("table_id")),
                "exact": True,
            }
        )
    for raw in _list(table.get("field_declarations")):
        row = _dict(raw)
        if _text(row.get("field")) != column:
            continue
        items.append(
            {
                "kind": "DATABASE_FIELD_DECLARATION",
                "source_id": _text(row.get("source_id")) or table_source,
                "source_locator": _text(row.get("field_path"))
                or f"table:{_text(table.get('name'))}:column:{column}",
                "asset_ref": _text(row.get("field_id")),
                "exact": True,
            }
        )
        break
    if not items:
        items.append(
            {
                "kind": "DATABASE_TABLE_DECLARATION",
                "source_id": table_source,
                "source_locator": f"table:{_text(table.get('name'))}:column:{column}",
                "asset_ref": _text(table.get("table_id")),
                "exact": True,
            }
        )
    return items


def _build_contract_and_drafts(
    *,
    table: dict[str, Any],
    column_names: list[str],
    identity_column: str,
    identity_value_source: str,
    operation_ref: str,
    operation_path: str,
    interface_id: str,
    materialization_ref: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    table_id = _text(table.get("table_id"))
    table_name = _text(table.get("name"))
    observer_id = _stable_id(
        "database_observer", table_id, *sorted(column_names)
    )
    field_bindings: list[dict[str, Any]] = []
    for index, column in enumerate(sorted(column_names)):
        field_binding_id = _stable_id(
            "database_observer_field_binding", table_id, column
        )
        field_bindings.append(
            {
                "schema": FIELD_BINDING_SCHEMA,
                "field_binding_id": field_binding_id,
                "operation_schema_binding_id": "",
                "interface_id": interface_id,
                "database_table_id": table_id,
                "api_field_id": "",
                "api_field_name": column,
                "api_property_path": [column],
                "database_field_id": _stable_id(
                    "database_schema_field", table_id, column
                ),
                "database_field_name": column,
                "value_source": identity_value_source,
                "direction": "",
                "type_compatibility": {},
                "mapping_decision_id": _stable_id(
                    "schema_declared_mapping", table_id, column
                ),
                "authoritative": True,
                "read_only": True,
                "write_target_allowed": False,
                "oracle_authority_allowed": False,
                "term_index": index,
                "evidence": _term_evidence(table, column),
            }
        )
    identity_key = [identity_column]
    predicates = [
        {
            "database_field_name": identity_column,
            "database_field_id": _stable_id(
                "database_schema_field", table_id, identity_column
            ),
            "operator": "=",
            "value_source": identity_value_source,
        }
    ]
    contract = {
        "schema": CONTRACT_SCHEMA,
        "observer_id": observer_id,
        "operation_schema_binding_id": "",
        "interface_id": interface_id,
        "method": "",
        "path": operation_path,
        "direction": "",
        "response_status": "",
        "media_type": "",
        "api_schema_entity_id": "",
        "database_table_id": table_id,
        "database_schema_name": "",
        "database_table_name": table_name,
        "database_qualified_name": table_name,
        "table_mapping_decision_id": _stable_id(
            "schema_declared_table_mapping", table_id
        ),
        "field_bindings": field_bindings,
        "identity_key_options": [
            {
                "identity_key_id": "",
                "columns": identity_key,
                "source": "SCHEMA_DECLARED_IDENTITY_FIELDS",
                "explicit_group": False,
            }
        ],
        "selected_identity_key": identity_key,
        "selected_identity_key_id": "",
        "selected_identity_source": "SCHEMA_DECLARED_IDENTITY_FIELDS",
        "selected_identity_is_explicit_group": False,
        "identity_predicates": predicates,
        "query_plan": {
            "operation": "SELECT_ONE",
            "database_table_id": table_id,
            "projection": sorted(column_names),
            "predicates": predicates,
            "parameterized": True,
            "maximum_rows": 2,
            "raw_sql": "",
        },
        "status": "READY_FOR_RUNTIME_CONNECTION_BINDING",
        "reason_code": "",
        "mapping_authoritative": True,
        "runtime_observer_authoritative": True,
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
        "derivation": DERIVATION,
        "operator_approval_required": False,
        "evidence": _term_evidence(table, sorted(column_names)[0]),
    }
    # Compile-time fail-closed: the same validator that gates runtime execution
    # must accept this contract, or the obligation stays visibly unresolved.
    try:
        _validate_runtime_contract(contract)
    except Exception as exc:  # noqa: BLE001 - surfaced as a named gap
        reason = f"SCHEMA_NUMERIC_CONTRACT_REFUSED:{type(exc).__name__}"
        return {}, [{"reason_code": reason, "detail": str(exc)[:200]}]

    drafts: list[dict[str, Any]] = []
    for phase in ("BEFORE", "AFTER"):
        drafts.append(
            {
                "schema": DRAFT_SCHEMA,
                "draft_id": _stable_id(
                    "database_observer_execution_draft",
                    materialization_ref,
                    observer_id,
                    phase,
                ),
                "runtime_materialization_ref": materialization_ref,
                "runtime_plan_ref": "",
                "observer_handler_id": DIRECT_READBACK_HANDLER_ID,
                "observer_contract_ref": observer_id,
                "observation_phase": phase,
                "database_observer_contract": contract,
                "database_connection_ref": "",
                "identity_value_sources": [identity_value_source],
                "projection": sorted(column_names),
                "source_slot_refs": [],
                "mapping_decision_refs": [
                    _text(_dict(row).get("mapping_decision_id"))
                    for row in field_bindings
                ],
                "required": True,
                "runtime_identity_values_materialized": False,
                "runtime_connection_bound": False,
                "database_connection_opened": False,
                "query_executed": False,
                "query_parameterized": True,
                "raw_sql_retained": False,
                "predicate_values_retained": False,
                "secret_values_retained": False,
                "observer_receipt_produced": False,
                "oracle_verdict_emitted": False,
                "write_target_allowed": False,
                "mutation_allowed": False,
                "derivation": DERIVATION,
            }
        )
    return contract, drafts


def _unresolved_numeric_assertions(experiment: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in _list(experiment.get("assertions"))
        if isinstance(row, dict) and _source_kind(row) in _SOURCE_KINDS
    ]


def resolve_schema_numeric_observation(
    experiment: dict[str, Any],
    schema_view: dict[str, Any],
) -> dict[str, Any]:
    """Resolve exact schema-declared DB before/after observation for the experiment.

    Returns ``{"status": "BOUND", "contracts": [...], "drafts": [...],
    "gaps": []}`` or ``{"status": "UNRESOLVED", "contracts": [], "drafts": [],
    "gaps": [...]}``. Every gap carries a named reason code; nothing is guessed.
    """
    exp = dict(experiment or {})
    tables_by_name = _tables_by_name(schema_view)
    column_tables = _column_tables(schema_view)
    request_sources = _request_value_sources(exp)
    id_to_name = _field_id_to_name_pairs(exp)
    operation_ref = _text(exp.get("operation_ref"))
    operation_path = ""
    interface_id = ""
    for raw in _list(exp.get("treatment_plan")):
        step = _dict(raw)
        operation_ref = operation_ref or _text(step.get("operation_ref"))
        operation_path = operation_path or _text(step.get("path"))
        interface_id = interface_id or _text(step.get("interface_id"))
    materialization_ref = _text(
        _dict(exp.get("runtime_materialization_contract")).get(
            "materialization_id"
        )
    ) or _text(
        _dict(_dict(exp.get("runtime_materialization_contract")).get("lineage")).get(
            "materialization_id"
        )
    ) or _text(
        _dict(exp.get("compile_receipt")).get("runtime_materialization_id")
    ) or f"schema-material-numeric:{_text(exp.get('experiment_id'))}"

    term_resolutions: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    seen_terms: set[tuple[str, str]] = set()

    for assertion in _unresolved_numeric_assertions(exp):
        assertion_id = _text(assertion.get("assertion_id")) or "unknown"
        table_hints = _entity_table_hints(assertion)
        for raw_term in _assertion_raw_terms(assertion):
            ids, names = _term_tokens(raw_term)
            for field_id in sorted(ids):
                mapped = _text(id_to_name.get(field_id))
                if mapped:
                    names.add(mapped)
                else:
                    gaps.append(
                        {
                            "assertion_id": assertion_id,
                            "term_tokens": sorted(ids | names),
                            "reason_code": "SCHEMA_NUMERIC_TERM_ID_WITHOUT_NAME",
                        }
                    )
            if names:
                table_id, table_name, column = _resolve_term_column(
                    names,
                    table_hints=table_hints,
                    tables_by_name=tables_by_name,
                    column_tables=column_tables,
                )
                if not table_id:
                    gaps.append(
                        {
                            "assertion_id": assertion_id,
                            "term_tokens": sorted(names),
                            "reason_code": table_name or column,
                        }
                    )
                    continue
                key = (table_id, column)
                if key not in seen_terms:
                    seen_terms.add(key)
                    term_resolutions.append(
                        {
                            "table_id": table_id,
                            "table_name": table_name,
                            "column": column,
                            "term_names": sorted(names),
                            "term_ids": sorted(ids),
                            "assertion_ids": [assertion_id],
                        }
                    )
                else:
                    for row in term_resolutions:
                        if (
                            row["table_id"] == table_id
                            and row["column"] == column
                            and assertion_id not in row["assertion_ids"]
                        ):
                            row["assertion_ids"].append(assertion_id)
                            row["term_names"].extend(
                                name for name in names if name not in row["term_names"]
                            )

    if gaps:
        return {
            "status": "UNRESOLVED",
            "contracts": [],
            "drafts": [],
            "gaps": gaps,
        }

    if not term_resolutions:
        return {
            "status": "UNRESOLVED",
            "contracts": [],
            "drafts": [],
            "gaps": [
                {
                    "reason_code": "SCHEMA_NUMERIC_TERMS_MISSING",
                    "detail": "no resolvable numeric terms in unresolved assertions",
                }
            ],
        }

    tables_by_id: dict[str, dict[str, Any]] = {
        _text(row.get("table_id")): row
        for row in _list(schema_view.get("tables"))
        if isinstance(row, dict) and _text(row.get("table_id"))
    }
    by_table: dict[str, list[dict[str, Any]]] = {}
    for resolution in term_resolutions:
        by_table.setdefault(resolution["table_id"], []).append(resolution)

    contracts: list[dict[str, Any]] = []
    drafts: list[dict[str, Any]] = []
    for table_id, resolutions in by_table.items():
        table = tables_by_id.get(table_id)
        if not table:
            gaps.append(
                {"table_id": table_id, "reason_code": "SCHEMA_NUMERIC_TABLE_MISSING"}
            )
            continue
        identity_column, value_source = _resolve_identity_value_source(
            table, request_sources
        )
        if not identity_column:
            gaps.append(
                {
                    "table_id": table_id,
                    "table_name": _text(table.get("name")),
                    "columns": sorted(
                        {row["column"] for row in resolutions}
                    ),
                    "reason_code": value_source,
                }
            )
            continue
        column_names = sorted({row["column"] for row in resolutions})
        contract, contract_drafts = _build_contract_and_drafts(
            table=table,
            column_names=column_names,
            identity_column=identity_column,
            identity_value_source=value_source,
            operation_ref=operation_ref,
            operation_path=operation_path,
            interface_id=interface_id,
            materialization_ref=materialization_ref,
        )
        if not contract:
            gaps.append(
                {
                    "table_id": table_id,
                    "reason_code": (
                        contract_drafts[0].get("reason_code")
                        if contract_drafts
                        else "SCHEMA_NUMERIC_CONTRACT_BUILD_FAILED"
                    ),
                    "detail": (
                        contract_drafts[0].get("detail") if contract_drafts else ""
                    ),
                }
            )
            continue
        contracts.append(contract)
        drafts.extend(contract_drafts)

    if gaps or not contracts:
        return {
            "status": "UNRESOLVED",
            "contracts": contracts,
            "drafts": [],
            "gaps": gaps
            or [
                {
                    "reason_code": "SCHEMA_NUMERIC_CONTRACTS_MISSING",
                    "detail": "no schema-declared contract could be built",
                }
            ],
        }
    return {"status": "BOUND", "contracts": contracts, "drafts": drafts, "gaps": []}


def attach_schema_numeric_observation(
    experiment: dict[str, Any],
    schema_view: dict[str, Any],
) -> dict[str, Any]:
    """Attach schema-declared drafts and the phase-aggregate Observer requirement.

    The attached drafts are consumed by the EXISTING experiment executor
    (``execute_database_observer_phase`` BEFORE/AFTER) and the existing
    phase-aggregate observer; this module only supplies the binding.
    """
    result = dict(experiment or {})
    resolution = resolve_schema_numeric_observation(result, schema_view)
    if resolution["status"] != "BOUND":
        result["database_numeric_schema_observer"] = {
            "schema": SCHEMA_NUMERIC_OBSERVER_SCHEMA,
            "status": "UNRESOLVED",
            "derivation": DERIVATION,
            "contract_count": 0,
            "draft_count": 0,
            "gaps": resolution["gaps"],
        }
        return result

    drafts = [
        dict(row) for row in resolution["drafts"] if isinstance(row, dict)
    ]
    draft_ids = {_text(row.get("draft_id")) for row in drafts if _text(row.get("draft_id"))}
    existing = [
        dict(row)
        for row in _list(result.get("database_observer_execution_drafts"))
        if isinstance(row, dict)
        and _text(row.get("draft_id")) not in draft_ids
    ]
    result["database_observer_execution_drafts"] = [*existing, *drafts]
    result["database_observer_phase_receipts_required"] = True
    result["database_observer_finalizer_must_not_requery"] = True
    observers = [
        dict(row)
        for row in _list(result.get("observers"))
        if isinstance(row, dict)
        and _text(row.get("observer_id")) != PHASE_AGGREGATE_OBSERVER_ID
    ]
    observers.append(
        {
            "observer_id": PHASE_AGGREGATE_OBSERVER_ID,
            "surface": "database_read_only",
            "adapter": "db_sql",
            "required": True,
            "phase_receipts_required": True,
            "direct_readback_observer_id": DIRECT_READBACK_HANDLER_ID,
            "derivation": DERIVATION,
        }
    )
    result["observers"] = observers
    result["database_numeric_schema_observer"] = {
        "schema": SCHEMA_NUMERIC_OBSERVER_SCHEMA,
        "status": "BOUND",
        "derivation": DERIVATION,
        "contract_count": len(resolution["contracts"]),
        "draft_count": len(drafts),
        "table_refs": sorted(
            {
                _text(_dict(row).get("database_table_id"))
                for row in resolution["contracts"]
                if _text(_dict(row).get("database_table_id"))
            }
        ),
        "identity_value_sources": sorted(
            {
                _text(_dict(row).get("value_source"))
                for contract in resolution["contracts"]
                for row in _list(_dict(contract).get("identity_predicates"))
                if _text(_dict(row).get("value_source"))
            }
        ),
        "operator_approval_required": False,
        "gaps": [],
    }
    receipt = _dict(result.get("compile_receipt"))
    receipt.update(
        {
            "database_numeric_schema_observer_status": "BOUND",
            "database_numeric_schema_observer_draft_count": len(drafts),
            "database_numeric_schema_observer_contract_count": len(
                resolution["contracts"]
            ),
        }
    )
    result["compile_receipt"] = receipt
    return result


__all__ = [
    "CONTRACT_SCHEMA",
    "DERIVATION",
    "DRAFT_SCHEMA",
    "SCHEMA_NUMERIC_OBSERVER_SCHEMA",
    "attach_schema_numeric_observation",
    "resolve_schema_numeric_observation",
]
