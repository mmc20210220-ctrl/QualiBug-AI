"""Execute approved FK-scoped database child collection aggregate observations.

The implementation reuses the existing database Observer connection, non-production, read-only,
identifier-introspection and rollback authorities. It executes only parameterized aggregate SELECTs
and returns fact-only receipts. Raw rows, SQL, DSNs, credentials and predicate values are never
retained, and no Oracle verdict is emitted.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from .database_observer_runtime import (
    ADAPTER,
    DatabaseObserverRuntimeError,
    _connect,
    _fingerprint,
    _introspect,
    _json_safe,
    _public_profile,
    _quote_identifier,
    _resolve_value,
    _runtime_context,
    _select_profile,
    _sensitive_field,
    _text,
    _validated_live_identifier,
    _validate_identifier_shape,
    resolve_declared_read_only_database_profiles,
)
from .observer_contracts_base import _receipt, register_observer, registered_observer_ids
from .persistence_observer import persistence_read_allowed

OBSERVER_ID = "approved_database_relation_aggregate"
SURFACE = "database_relation_read_only"
EVIDENCE_KEY = "approved_database_relation_aggregate_snapshot"
RUNTIME_RECEIPT_SCHEMA = "qualibug.database-relation-observer-runtime-receipt.v1"
CONTRACT_SCHEMA = "qualibug.database-relation-observer-contract.v1"
_ALLOWED_AGGREGATES = {"COUNT", "SUM", "MIN", "MAX"}
_MAX_AGGREGATE_REQUESTS = 16


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _validate_contract(contract: dict[str, Any]) -> dict[str, Any]:
    row = deepcopy(_dict(contract))
    if _text(row.get("schema")) != CONTRACT_SCHEMA:
        raise DatabaseObserverRuntimeError("database_relation_contract_schema_invalid")
    if _text(row.get("status")) != "READY_FOR_RUNTIME_CONNECTION_BINDING":
        raise DatabaseObserverRuntimeError("database_relation_contract_not_runtime_bindable")
    if row.get("runtime_observer_authoritative") is not True:
        raise DatabaseObserverRuntimeError("database_relation_runtime_authority_missing")
    if row.get("read_only") is not True or row.get("mutation_allowed") is not False:
        raise DatabaseObserverRuntimeError("database_relation_contract_not_read_only")
    if row.get("write_target_allowed") is not False or row.get("oracle_authority_allowed") is not False:
        raise DatabaseObserverRuntimeError("database_relation_contract_authority_scope_invalid")
    if row.get("connection_secret_embedded") is True:
        raise DatabaseObserverRuntimeError("database_relation_contract_embedded_secret_refused")

    table = _validate_identifier_shape(_text(row.get("child_table_name")))
    schema_name = _text(row.get("child_schema_name"))
    if schema_name:
        _validate_identifier_shape(schema_name)
    predicates = [dict(item) for item in _list(row.get("relation_predicates")) if isinstance(item, dict)]
    if not predicates or any(
        not _text(item.get("child_database_field_name"))
        or _text(item.get("operator")) != "="
        or not _text(item.get("value_source"))
        for item in predicates
    ):
        raise DatabaseObserverRuntimeError("database_relation_predicate_contract_incomplete")
    for item in predicates:
        _validate_identifier_shape(_text(item.get("child_database_field_name")))

    allowed_fields = [
        dict(item)
        for item in _list(row.get("allowed_child_fields"))
        if isinstance(item, dict)
        and _text(item.get("database_field_id"))
        and _text(item.get("database_field_name"))
    ]
    if not allowed_fields:
        raise DatabaseObserverRuntimeError("database_relation_child_field_catalog_missing")
    for field in allowed_fields:
        name = _validate_identifier_shape(_text(field.get("database_field_name")))
        if _sensitive_field(name):
            field["runtime_observation_allowed"] = False
        else:
            field["runtime_observation_allowed"] = True
    plan = _dict(row.get("query_plan"))
    if (
        _text(plan.get("operation")) != "SELECT_MANY"
        or plan.get("parameterized") is not True
        or _text(plan.get("raw_sql"))
        or plan.get("client_side_filter") is not False
    ):
        raise DatabaseObserverRuntimeError("database_relation_query_plan_invalid")
    allowed_aggregates = {
        _text(value).upper() for value in _list(plan.get("allowed_aggregates")) if _text(value)
    }
    if not allowed_aggregates or not allowed_aggregates.issubset(_ALLOWED_AGGREGATES):
        raise DatabaseObserverRuntimeError("database_relation_aggregate_allowlist_invalid")
    row["child_table_name"] = table
    row["child_schema_name"] = schema_name
    row["relation_predicates"] = predicates
    row["allowed_child_fields"] = allowed_fields
    row["query_plan"] = {**plan, "allowed_aggregates": sorted(allowed_aggregates)}
    return row


def _validate_requests(
    requests: list[dict[str, Any]], approved: dict[str, Any]
) -> list[dict[str, Any]]:
    if not requests or len(requests) > _MAX_AGGREGATE_REQUESTS:
        raise DatabaseObserverRuntimeError("database_relation_aggregate_request_count_invalid")
    allowed = {
        (_text(row.get("database_field_id")), _text(row.get("database_field_name"))): row
        for row in _list(approved.get("allowed_child_fields"))
        if isinstance(row, dict)
    }
    allowed_aggregates = {
        _text(value).upper()
        for value in _list(_dict(approved.get("query_plan")).get("allowed_aggregates"))
    }
    output: list[dict[str, Any]] = []
    aliases: set[str] = set()
    for index, raw in enumerate(requests):
        row = _dict(raw)
        aggregate = _text(row.get("aggregate")).upper()
        field_id = _text(row.get("database_field_id"))
        field_name = _text(row.get("database_field_name"))
        if aggregate not in allowed_aggregates:
            raise DatabaseObserverRuntimeError("database_relation_aggregate_not_allowed")
        if aggregate == "COUNT" and not field_name:
            field_id = ""
        else:
            catalog = allowed.get((field_id, field_name))
            if not catalog or catalog.get("runtime_observation_allowed") is not True:
                raise DatabaseObserverRuntimeError("database_relation_aggregate_field_not_authorized")
            _validate_identifier_shape(field_name)
        alias = _text(row.get("alias")) or f"aggregate_{index}"
        alias = _validate_identifier_shape(alias)
        if alias in aliases:
            raise DatabaseObserverRuntimeError("database_relation_aggregate_alias_duplicate")
        aliases.add(alias)
        output.append(
            {
                "aggregate": aggregate,
                "database_field_id": field_id,
                "database_field_name": field_name,
                "alias": alias,
            }
        )
    return output


def _execute_aggregate_query(
    *,
    approved: dict[str, Any],
    requests: list[dict[str, Any]],
    profile: dict[str, Any],
    values: list[Any],
    connection_factory: Callable[[dict[str, Any]], Any] | None,
) -> tuple[dict[str, Any], bool]:
    conn = None
    cursor = None
    failure: DatabaseObserverRuntimeError | None = None
    rollback_ok = False
    result: dict[str, Any] = {}
    try:
        conn = (connection_factory or _connect)(profile)
        schema_name = _text(approved.get("child_schema_name"))
        schema = _introspect(conn, profile, schema_name)
        table = _validated_live_identifier(_text(approved.get("child_table_name")), schema.keys())
        columns = schema.get(table, set())
        predicates = _list(approved.get("relation_predicates"))
        predicate_columns = [
            _validated_live_identifier(_text(_dict(row).get("child_database_field_name")), columns)
            for row in predicates
        ]
        dialect = _text(profile.get("dialect"))
        placeholder = "?" if dialect == "sqlite" else "%s"
        qualified = _quote_identifier(table, dialect)
        if schema_name and dialect == "postgresql":
            qualified = (
                f"{_quote_identifier(schema_name, dialect)}."
                f"{_quote_identifier(table, dialect)}"
            )
        expressions: list[str] = []
        for request in requests:
            aggregate = request["aggregate"]
            alias = _quote_identifier(request["alias"], dialect)
            if aggregate == "COUNT" and not request["database_field_name"]:
                expressions.append(f"COUNT(*) AS {alias}")
            else:
                field = _validated_live_identifier(request["database_field_name"], columns)
                expressions.append(
                    f"{aggregate}({_quote_identifier(field, dialect)}) AS {alias}"
                )
        predicate_sql = " AND ".join(
            f"{_quote_identifier(name, dialect)} = {placeholder}"
            for name in predicate_columns
        )
        statement = f"SELECT {', '.join(expressions)} FROM {qualified} WHERE {predicate_sql}"
        cursor = conn.cursor()
        cursor.execute(statement, tuple(values))
        raw = cursor.fetchone()
        result = {
            request["alias"]: (raw[index] if raw is not None and index < len(raw) else None)
            for index, request in enumerate(requests)
        }
    except DatabaseObserverRuntimeError as exc:
        failure = exc
    except Exception as exc:  # noqa: BLE001 - driver text may contain secrets
        failure = DatabaseObserverRuntimeError(
            f"database_relation_driver_failure:{type(exc).__name__}"
        )
    finally:
        try:
            if cursor is not None:
                cursor.close()
        except Exception:
            pass
        if conn is not None:
            try:
                conn.rollback()
                rollback_ok = True
            except Exception:
                rollback_ok = False
            try:
                conn.close()
            except Exception:
                pass
    if conn is not None and not rollback_ok:
        raise DatabaseObserverRuntimeError("database_relation_transaction_rollback_failed")
    if failure is not None:
        raise failure
    return result, rollback_ok


def execute_database_relation_observer_contract(
    contract: dict[str, Any],
    *,
    aggregate_requests: list[dict[str, Any]],
    root: Path | str,
    project: str,
    runtime_values: dict[str, Any] | None = None,
    runtime_contract: dict[str, Any] | None = None,
    connection_ref: str = "",
    envelope: dict[str, Any] | None = None,
    connection_factory: Callable[[dict[str, Any]], Any] | None = None,
    campaign_id: str = "",
    execution_id: str = "",
) -> dict[str, Any]:
    """Execute one approved relation aggregate and return a typed fact receipt."""

    def refuse(reason_code: str, **evidence: Any) -> dict[str, Any]:
        return _receipt(
            observer_id=OBSERVER_ID,
            status="INDETERMINATE",
            reason_code=reason_code,
            evidence={
                "schema": RUNTIME_RECEIPT_SCHEMA,
                "write_attempted": False,
                "raw_sql_retained": False,
                "predicate_values_retained": False,
                "secret_values_retained": False,
                "dsn_retained": False,
                **_json_safe(evidence),
            },
            campaign_id=campaign_id,
            execution_id=execution_id,
        )

    try:
        approved = _validate_contract(contract)
        requests = _validate_requests(
            [dict(row) for row in aggregate_requests if isinstance(row, dict)], approved
        )
    except DatabaseObserverRuntimeError as exc:
        return refuse("DATABASE_RELATION_OBSERVER_CONTRACT_REFUSED", detail=str(exc))

    allowed, detail = persistence_read_allowed(Path(root), project, runtime_contract)
    if not allowed:
        return refuse("DATABASE_RELATION_OBSERVER_READ_NOT_PERMITTED", detail=detail)
    try:
        profiles = resolve_declared_read_only_database_profiles(root, project)
        profile = _select_profile(profiles, _text(connection_ref))
    except DatabaseObserverRuntimeError as exc:
        return refuse("DATABASE_RELATION_OBSERVER_CONNECTION_BINDING_FAILED", detail=str(exc))

    context = _runtime_context(runtime_values, envelope)
    values: list[Any] = []
    missing_sources: list[str] = []
    for raw in _list(approved.get("relation_predicates")):
        source = _text(_dict(raw).get("value_source"))
        value = _resolve_value(source, context)
        if value in (None, ""):
            missing_sources.append(source)
        else:
            values.append(value)
    if missing_sources:
        return refuse(
            "DATABASE_RELATION_PARENT_VALUE_MISSING",
            relation_observer_ref=approved.get("relation_observer_id"),
            missing_value_sources=missing_sources,
            connection_profile=_public_profile(profile),
        )

    try:
        aggregates, rolled_back = _execute_aggregate_query(
            approved=approved,
            requests=requests,
            profile=profile,
            values=values,
            connection_factory=connection_factory,
        )
    except DatabaseObserverRuntimeError as exc:
        return refuse(
            "DATABASE_RELATION_OBSERVER_QUERY_FAILED",
            detail=str(exc),
            relation_observer_ref=approved.get("relation_observer_id"),
            connection_profile=_public_profile(profile),
            transaction_rolled_back=("transaction_rollback_failed" not in str(exc)),
        )

    safe_aggregates = _json_safe(aggregates)
    payload = {
        "schema": RUNTIME_RECEIPT_SCHEMA,
        "relation_observer_ref": _text(approved.get("relation_observer_id")),
        "root_observer_ref": _text(approved.get("root_observer_id")),
        "database_relationship_id": _text(approved.get("database_relationship_id")),
        "parent_table_ref": _text(approved.get("parent_table_id")),
        "child_table_ref": _text(approved.get("child_table_id")),
        "child_table_name": _text(approved.get("child_table_name")),
        "connection_profile": _public_profile(profile),
        "relation_key": [
            {
                "child_database_field_name": _text(row.get("child_database_field_name")),
                "parent_database_field_name": _text(row.get("parent_database_field_name")),
            }
            for row in _list(approved.get("relation_predicates"))
            if isinstance(row, dict)
        ],
        "relation_parameter_fingerprints": [_fingerprint(value)[:20] for value in values],
        "aggregate_requests": requests,
        "aggregate_values": safe_aggregates,
        "aggregate_fingerprint": _fingerprint(safe_aggregates),
        "parameterized": True,
        "read_only": True,
        "write_attempted": False,
        "transaction_rolled_back": rolled_back,
        "client_side_filter_used": False,
        "raw_rows_retained": False,
        "raw_sql_retained": False,
        "predicate_values_retained": False,
        "secret_values_retained": False,
        "dsn_retained": False,
        "oracle_verdict_emitted": False,
    }
    return _receipt(
        observer_id=OBSERVER_ID,
        status="OBSERVED",
        evidence={
            EVIDENCE_KEY: payload,
            "observation_fingerprint": _fingerprint(payload),
        },
        campaign_id=campaign_id,
        execution_id=execution_id,
    )


def observe_approved_database_relation(envelope: dict[str, Any]) -> dict[str, Any]:
    env = _dict(envelope)
    observations = _dict(env.get("observations"))
    prop = _dict(env.get("property") or _dict(env.get("assertion")).get("property"))
    experiment = _dict(env.get("experiment"))
    contract = _dict(
        prop.get("database_relation_observer_contract")
        or observations.get("database_relation_observer_contract")
        or experiment.get("database_relation_observer_contract")
    )
    requests = [
        dict(row)
        for row in _list(
            prop.get("aggregate_requests")
            or observations.get("database_relation_aggregate_requests")
            or experiment.get("database_relation_aggregate_requests")
        )
        if isinstance(row, dict)
    ]
    root = _text(prop.get("persistence_root") or experiment.get("persistence_root"))
    project = _text(prop.get("project") or experiment.get("project"))
    if not contract or not requests or not root or not project:
        return _receipt(
            observer_id=OBSERVER_ID,
            status="INDETERMINATE",
            reason_code="DATABASE_RELATION_OBSERVER_RUNTIME_INPUT_MISSING",
            evidence={
                "contract_present": bool(contract),
                "aggregate_requests_present": bool(requests),
                "root_present": bool(root),
                "project_present": bool(project),
                "secret_values_retained": False,
                "dsn_retained": False,
            },
        )
    return execute_database_relation_observer_contract(
        contract,
        aggregate_requests=requests,
        root=root,
        project=project,
        runtime_values=_dict(observations.get("database_observer_runtime_values")),
        runtime_contract=_dict(experiment.get("runtime_contract")),
        connection_ref=_text(
            prop.get("database_connection_ref") or observations.get("database_connection_ref")
        ),
        envelope=env,
        campaign_id=_text(env.get("campaign_id")),
        execution_id=_text(env.get("execution_id")),
    )


def install_approved_database_relation_observer() -> str:
    if OBSERVER_ID in registered_observer_ids():
        return OBSERVER_ID
    return register_observer(
        OBSERVER_ID,
        surface=SURFACE,
        adapter=ADAPTER,
        handler=observe_approved_database_relation,
        evidence_keys=(EVIDENCE_KEY,),
    )


__all__ = [
    "EVIDENCE_KEY",
    "OBSERVER_ID",
    "RUNTIME_RECEIPT_SCHEMA",
    "execute_database_relation_observer_contract",
    "install_approved_database_relation_observer",
    "observe_approved_database_relation",
]
