"""Execute one approved database Observer contract against a declared non-production source.

This is the runtime half of ``database_observer_contract_projection``.  It accepts only a
current, operator-approved, read-only contract; resolves one customer-declared database profile;
materializes identity predicates from runtime request/response values; validates identifiers
against the live schema; and executes one parameterized ``SELECT ... LIMIT 2``.

It never accepts raw SQL, never guesses a business table, never opens production, never executes a
mutation, and never places a password, DSN or raw predicate value in the observation receipt.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Iterable

from .observer_contracts_base import _receipt, register_observer, registered_observer_ids
from .persistence_observer import PersistenceObserverError, persistence_read_allowed

OBSERVER_ID = "approved_database_readback"
SURFACE = "database_read_only"
ADAPTER = "db_sql"
EVIDENCE_KEY = "approved_database_snapshot"
RUNTIME_RECEIPT_SCHEMA = "qualibug.database-observer-runtime-receipt.v1"

_MAX_ROWS = 2
_STATEMENT_TIMEOUT_MS = 5_000
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_SECRET_FIELD = re.compile(
    r"(?:^|_)(?:password|passwd|pwd|secret|token|credential|cookie|authorization|private_key)(?:$|_)",
    re.I,
)
_READ_ONLY_MODES = {"read_only", "readonly", "read-only", "ro"}


class DatabaseObserverRuntimeError(RuntimeError):
    """The approved Observer could not produce trustworthy runtime evidence."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _config_candidates(root: Path, project: str) -> list[Path]:
    return [
        root / "platform_workspace" / project / "multi_service_config.json",
        root / "platform_inputs" / project / "multi_service_config.json",
    ]


def _declared_config(root: Path, project: str) -> dict[str, Any]:
    for path in _config_candidates(root, project):
        if not path.is_file():
            continue
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DatabaseObserverRuntimeError(
                f"database_observer_config_unreadable:{path.name}:{type(exc).__name__}"
            ) from exc
        if not isinstance(loaded, dict):
            raise DatabaseObserverRuntimeError("database_observer_config_root_invalid")
        return loaded
    return {}


def _dialect(block: dict[str, Any]) -> tuple[str, str]:
    declared = _text(block.get("dialect") or block.get("driver") or block.get("type")).lower()
    aliases = {
        "postgres": "postgresql",
        "postgresql": "postgresql",
        "psql": "postgresql",
        "mysql": "mysql",
        "mariadb": "mysql",
        "sqlite": "sqlite",
        "sqlite3": "sqlite",
    }
    if declared:
        resolved = aliases.get(declared)
        if not resolved:
            raise DatabaseObserverRuntimeError(f"database_observer_dialect_unsupported:{declared}")
        return resolved, "EXPLICIT_DECLARATION"
    if _text(block.get("path")) or _text(block.get("file")):
        return "sqlite", "DECLARED_FILE_DATABASE"
    # Backward compatibility with the existing multi_service_config contract, whose DB block
    # historically meant PostgreSQL. This is a transport default, never a table/business guess.
    return "postgresql", "LEGACY_POSTGRESQL_CONFIG_DEFAULT"


def _password(block: dict[str, Any]) -> tuple[str, str]:
    env_name = _text(block.get("password_env"))
    if env_name:
        value = os.environ.get(env_name, "")
        if not value:
            raise DatabaseObserverRuntimeError(
                f"database_observer_password_environment_missing:{env_name}"
            )
        return value, "ENVIRONMENT_REFERENCE"
    return _text(block.get("password")), "INLINE_DECLARED_SECRET" if "password" in block else "NONE"


def resolve_declared_read_only_database_profiles(
    root: Path | str,
    project: str,
) -> list[dict[str, Any]]:
    """Resolve customer-declared DB profiles without returning a DSN.

    The returned dicts are runtime-only and may carry a password for the connector. Callers must
    never serialize them. Every profile must explicitly declare read-only access.
    """
    config = _declared_config(Path(root), project)
    profiles: list[dict[str, Any]] = []
    seen_refs: set[str] = set()
    for raw_service in _list(config.get("services")):
        service = _dict(raw_service)
        block = _dict(service.get("db"))
        if not block:
            continue
        service_ref = _text(
            block.get("connection_ref") or block.get("service_ref") or service.get("name")
        )
        if not service_ref:
            raise DatabaseObserverRuntimeError("database_observer_connection_ref_missing")
        if service_ref in seen_refs:
            raise DatabaseObserverRuntimeError(
                f"database_observer_connection_ref_duplicate:{service_ref}"
            )
        seen_refs.add(service_ref)
        access_mode = _text(block.get("access_mode")).lower()
        read_only = bool(block.get("read_only")) or access_mode in _READ_ONLY_MODES
        if not read_only:
            raise DatabaseObserverRuntimeError(
                f"database_observer_read_only_declaration_required:{service_ref}"
            )
        dialect, dialect_derivation = _dialect(block)
        password, password_source = _password(block)
        profile: dict[str, Any] = {
            "connection_ref": service_ref,
            "service": _text(service.get("name")) or service_ref,
            "dialect": dialect,
            "dialect_derivation": dialect_derivation,
            "read_only": True,
            "password_source": password_source,
            "password": password,
            "connect_timeout_seconds": min(
                max(int(block.get("connect_timeout_seconds") or 5), 1), 30
            ),
            "statement_timeout_ms": min(
                max(int(block.get("statement_timeout_ms") or _STATEMENT_TIMEOUT_MS), 100),
                30_000,
            ),
        }
        if dialect == "sqlite":
            path = _text(block.get("path") or block.get("file") or block.get("name"))
            if not path:
                raise DatabaseObserverRuntimeError(
                    f"database_observer_sqlite_path_missing:{service_ref}"
                )
            profile["path"] = path
            profile["database"] = Path(path).name
        else:
            host = _text(block.get("host"))
            database = _text(block.get("name") or block.get("database"))
            user = _text(block.get("user"))
            if not (host and database and user):
                raise DatabaseObserverRuntimeError(
                    f"database_observer_config_incomplete:{service_ref}"
                )
            profile.update(
                {
                    "host": host,
                    "port": int(
                        block.get("port")
                        or (3306 if dialect == "mysql" else 5432)
                    ),
                    "database": database,
                    "user": user,
                }
            )
        profiles.append(profile)
    return profiles


def _public_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "connection_ref": _text(profile.get("connection_ref")),
        "service": _text(profile.get("service")),
        "dialect": _text(profile.get("dialect")),
        "database_fingerprint": _fingerprint(_text(profile.get("database")))[:20],
        "read_only": bool(profile.get("read_only")),
        "credential_source": _text(profile.get("password_source")),
        "secret_value_retained": False,
        "dsn_retained": False,
        "host_retained": False,
        "user_retained": False,
    }


def _select_profile(
    profiles: list[dict[str, Any]], connection_ref: str
) -> dict[str, Any]:
    if connection_ref:
        matches = [
            row for row in profiles if _text(row.get("connection_ref")) == connection_ref
        ]
        if len(matches) != 1:
            raise DatabaseObserverRuntimeError(
                f"database_observer_connection_ref_not_found_or_ambiguous:{connection_ref}"
            )
        return matches[0]
    if len(profiles) == 1:
        return profiles[0]
    if not profiles:
        raise DatabaseObserverRuntimeError("database_observer_source_not_declared")
    raise DatabaseObserverRuntimeError(
        "database_observer_connection_ref_required_for_multi_database"
    )


def _validate_contract(contract: dict[str, Any]) -> dict[str, Any]:
    row = deepcopy(_dict(contract))
    if _text(row.get("schema")) != "qualibug.database-observer-contract.v1":
        raise DatabaseObserverRuntimeError("database_observer_contract_schema_invalid")
    if _text(row.get("status")) != "READY_FOR_RUNTIME_CONNECTION_BINDING":
        raise DatabaseObserverRuntimeError("database_observer_contract_not_runtime_bindable")
    if not bool(row.get("runtime_observer_authoritative")):
        raise DatabaseObserverRuntimeError("database_observer_runtime_authority_missing")
    if not bool(row.get("read_only")) or bool(row.get("mutation_allowed")):
        raise DatabaseObserverRuntimeError("database_observer_contract_not_read_only")
    if bool(row.get("write_target_allowed")) or bool(row.get("oracle_authority_allowed")):
        raise DatabaseObserverRuntimeError("database_observer_contract_authority_scope_invalid")
    plan = _dict(row.get("query_plan"))
    if (
        _text(plan.get("operation")) != "SELECT_ONE"
        or not bool(plan.get("parameterized"))
        or _text(plan.get("raw_sql"))
        or int(plan.get("maximum_rows") or 0) != _MAX_ROWS
    ):
        raise DatabaseObserverRuntimeError("database_observer_query_plan_invalid")
    predicates = [item for item in _list(row.get("identity_predicates")) if isinstance(item, dict)]
    projection = [_text(value) for value in _list(plan.get("projection")) if _text(value)]
    table = _text(row.get("database_table_name"))
    if not (table and predicates and projection):
        raise DatabaseObserverRuntimeError("database_observer_query_contract_incomplete")
    sensitive = sorted(
        {name for name in [*projection, *[_text(p.get("database_field_name")) for p in predicates]] if _SECRET_FIELD.search(name)}
    )
    if sensitive:
        raise DatabaseObserverRuntimeError(
            "database_observer_secret_field_refused:" + ",".join(sensitive)
        )
    return row


def _validated_identifier(name: str, allowed: Iterable[str]) -> str:
    candidate = _text(name)
    if not _SAFE_IDENTIFIER.fullmatch(candidate):
        raise DatabaseObserverRuntimeError(
            f"database_observer_identifier_shape_refused:{candidate[:40]}"
        )
    if candidate not in set(allowed):
        raise DatabaseObserverRuntimeError(
            f"database_observer_identifier_not_introspected:{candidate}"
        )
    return candidate


def _quote_identifier(name: str, dialect: str) -> str:
    if dialect == "mysql":
        return f"`{name}`"
    return f'"{name}"'


def _connect(profile: dict[str, Any]) -> Any:
    dialect = _text(profile.get("dialect"))
    if dialect == "sqlite":
        path = Path(_text(profile.get("path"))).expanduser()
        uri = f"file:{path.as_posix()}?mode=ro"
        conn = sqlite3.connect(
            uri,
            uri=True,
            timeout=float(profile.get("connect_timeout_seconds") or 5),
        )
        conn.execute("PRAGMA query_only=ON")
        return conn
    if dialect == "postgresql":
        kwargs = {
            "host": profile.get("host"),
            "port": profile.get("port"),
            "dbname": profile.get("database"),
            "user": profile.get("user"),
            "password": profile.get("password"),
            "connect_timeout": profile.get("connect_timeout_seconds"),
        }
        try:
            import psycopg  # type: ignore

            conn = psycopg.connect(**kwargs)
        except ImportError:
            try:
                import psycopg2  # type: ignore
            except ImportError as exc:
                raise DatabaseObserverRuntimeError(
                    "database_observer_postgresql_driver_missing"
                ) from exc
            conn = psycopg2.connect(**kwargs)
        conn.autocommit = False
        cursor = conn.cursor()
        try:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute(
                "SET LOCAL statement_timeout = %s",
                (int(profile.get("statement_timeout_ms") or _STATEMENT_TIMEOUT_MS),),
            )
        finally:
            cursor.close()
        return conn
    if dialect == "mysql":
        try:
            import pymysql  # type: ignore
        except ImportError as exc:
            raise DatabaseObserverRuntimeError(
                "database_observer_mysql_driver_missing"
            ) from exc
        conn = pymysql.connect(
            host=profile.get("host"),
            port=int(profile.get("port") or 3306),
            database=profile.get("database"),
            user=profile.get("user"),
            password=profile.get("password"),
            connect_timeout=int(profile.get("connect_timeout_seconds") or 5),
            read_timeout=max(1, int(profile.get("statement_timeout_ms") or 5000) // 1000),
            write_timeout=max(1, int(profile.get("statement_timeout_ms") or 5000) // 1000),
            autocommit=False,
        )
        cursor = conn.cursor()
        try:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
        finally:
            cursor.close()
        return conn
    raise DatabaseObserverRuntimeError(f"database_observer_dialect_unsupported:{dialect}")


def _introspect(conn: Any, profile: dict[str, Any], schema_name: str) -> dict[str, set[str]]:
    dialect = _text(profile.get("dialect"))
    cursor = conn.cursor()
    try:
        if dialect == "sqlite":
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [_text(row[0]) for row in cursor.fetchall() if row and _text(row[0])]
            result: dict[str, set[str]] = {}
            for table in tables:
                if not _SAFE_IDENTIFIER.fullmatch(table):
                    continue
                cursor.execute(f'PRAGMA table_info("{table}")')
                result[table] = {
                    _text(row[1]) for row in cursor.fetchall() if len(row) > 1 and _text(row[1])
                }
            return result
        if dialect == "postgresql":
            schema = schema_name or "public"
            cursor.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema=%s ORDER BY table_name, ordinal_position",
                (schema,),
            )
        else:
            cursor.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema=DATABASE() ORDER BY table_name, ordinal_position"
            )
        result: dict[str, set[str]] = {}
        for raw in cursor.fetchall():
            table, column = _text(raw[0]), _text(raw[1])
            if table and column:
                result.setdefault(table, set()).add(column)
        return result
    finally:
        cursor.close()


def _get_path(value: Any, path: list[str]) -> Any:
    current = value
    for part in path:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _runtime_context(
    runtime_values: dict[str, Any] | None,
    envelope: dict[str, Any] | None,
) -> dict[str, Any]:
    explicit = deepcopy(_dict(runtime_values))
    env = _dict(envelope)
    observations = _dict(env.get("observations"))
    treatment = _dict(
        env.get("treatment_observation") or observations.get("treatment_observation")
    )
    experiment = _dict(env.get("experiment"))
    materialized = _dict(
        experiment.get("materialized_request") or observations.get("materialized_request")
    )
    request_body = (
        explicit.get("request_body")
        or _dict(explicit.get("request")).get("body")
        or materialized.get("body")
        or treatment.get("request_body")
        or _dict(treatment.get("request")).get("body")
        or {}
    )
    request_parameters: dict[str, Any] = {}
    for source in (
        _dict(explicit.get("request")).get("parameter"),
        explicit.get("request_parameters"),
        materialized.get("path_parameters"),
        materialized.get("query_parameters"),
        treatment.get("request_parameters"),
    ):
        if isinstance(source, dict):
            request_parameters.update(source)
    response_body = (
        explicit.get("response_body")
        or _dict(explicit.get("response")).get("body")
        or treatment.get("body")
        or _dict(treatment.get("response")).get("body")
        or {}
    )
    return {
        "direct": explicit,
        "request": {"body": request_body, "parameter": request_parameters},
        "response": {"body": response_body},
    }


def _resolve_value(source: str, context: dict[str, Any]) -> Any:
    direct = _dict(context.get("direct"))
    if source in direct:
        return direct[source]
    parts = [_text(value) for value in source.split(".") if _text(value)]
    if not parts:
        return None
    return _get_path(context, parts)


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def execute_database_observer_contract(
    contract: dict[str, Any],
    *,
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
    """Execute a single approved, parameterized Observer contract and return a typed receipt."""

    def refuse(reason_code: str, **evidence: Any) -> dict[str, Any]:
        return _receipt(
            observer_id=OBSERVER_ID,
            status="INDETERMINATE",
            reason_code=reason_code,
            evidence={
                "schema": RUNTIME_RECEIPT_SCHEMA,
                "write_attempted": False,
                "raw_sql_retained": False,
                "secret_values_retained": False,
                **_json_safe(evidence),
            },
            campaign_id=campaign_id,
            execution_id=execution_id,
        )

    try:
        approved = _validate_contract(contract)
    except DatabaseObserverRuntimeError as exc:
        return refuse("DATABASE_OBSERVER_CONTRACT_REFUSED", detail=str(exc))

    allowed, detail = persistence_read_allowed(Path(root), project, runtime_contract)
    if not allowed:
        return refuse("DATABASE_OBSERVER_READ_NOT_PERMITTED", detail=detail)

    try:
        profiles = resolve_declared_read_only_database_profiles(root, project)
        profile = _select_profile(profiles, _text(connection_ref))
    except DatabaseObserverRuntimeError as exc:
        return refuse("DATABASE_OBSERVER_CONNECTION_BINDING_FAILED", detail=str(exc))

    predicates = [
        dict(row)
        for row in _list(approved.get("identity_predicates"))
        if isinstance(row, dict)
    ]
    context = _runtime_context(runtime_values, envelope)
    values: list[Any] = []
    missing_sources: list[str] = []
    for predicate in predicates:
        source = _text(predicate.get("value_source"))
        value = _resolve_value(source, context)
        if value is None or value == "":
            missing_sources.append(source)
        else:
            values.append(value)
    if missing_sources:
        return refuse(
            "DATABASE_OBSERVER_IDENTITY_VALUE_MISSING",
            observer_contract_ref=approved.get("observer_id"),
            missing_value_sources=missing_sources,
            connection_profile=_public_profile(profile),
        )

    conn = None
    cursor = None
    rolled_back = False
    try:
        conn = (connection_factory or _connect)(profile)
        schema_name = _text(approved.get("database_schema_name"))
        schema = _introspect(conn, profile, schema_name)
        table = _validated_identifier(
            _text(approved.get("database_table_name")), schema.keys()
        )
        columns = schema.get(table, set())
        projection = [
            _validated_identifier(_text(value), columns)
            for value in _list(_dict(approved.get("query_plan")).get("projection"))
            if _text(value)
        ]
        predicate_columns = [
            _validated_identifier(_text(row.get("database_field_name")), columns)
            for row in predicates
        ]
        dialect = _text(profile.get("dialect"))
        placeholder = "?" if dialect == "sqlite" else "%s"
        qualified = _quote_identifier(table, dialect)
        if schema_name and dialect == "postgresql":
            if not _SAFE_IDENTIFIER.fullmatch(schema_name):
                raise DatabaseObserverRuntimeError(
                    f"database_observer_identifier_shape_refused:{schema_name[:40]}"
                )
            qualified = (
                f"{_quote_identifier(schema_name, dialect)}."
                f"{_quote_identifier(table, dialect)}"
            )
        projection_sql = ", ".join(_quote_identifier(name, dialect) for name in projection)
        predicate_sql = " AND ".join(
            f"{_quote_identifier(name, dialect)} = {placeholder}"
            for name in predicate_columns
        )
        sql = f"SELECT {projection_sql} FROM {qualified} WHERE {predicate_sql} LIMIT {_MAX_ROWS}"
        cursor = conn.cursor()
        cursor.execute(sql, tuple(values))
        raw_rows = cursor.fetchall()
        rows = [
            {projection[index]: record[index] for index in range(min(len(projection), len(record)))}
            for record in raw_rows
        ]
        if hasattr(conn, "rollback"):
            conn.rollback()
            rolled_back = True
    except DatabaseObserverRuntimeError as exc:
        return refuse(
            "DATABASE_OBSERVER_QUERY_REFUSED",
            detail=str(exc),
            observer_contract_ref=approved.get("observer_id"),
            connection_profile=_public_profile(profile),
            transaction_rolled_back=rolled_back,
        )
    except Exception as exc:  # noqa: BLE001 - runtime failure becomes explicit evidence
        return refuse(
            "DATABASE_OBSERVER_QUERY_FAILED",
            detail=f"{type(exc).__name__}:{exc}",
            observer_contract_ref=approved.get("observer_id"),
            connection_profile=_public_profile(profile),
            transaction_rolled_back=rolled_back,
        )
    finally:
        try:
            if cursor is not None:
                cursor.close()
        except Exception:
            pass
        try:
            if conn is not None:
                if not rolled_back and hasattr(conn, "rollback"):
                    conn.rollback()
                    rolled_back = True
                conn.close()
        except Exception:
            pass

    safe_rows = _json_safe(rows)
    match_status = "NOT_FOUND" if not safe_rows else "MATCHED_ONE" if len(safe_rows) == 1 else "NON_UNIQUE_IDENTITY"
    payload = {
        "schema": RUNTIME_RECEIPT_SCHEMA,
        "observer_contract_ref": _text(approved.get("observer_id")),
        "operation_schema_binding_id": _text(
            approved.get("operation_schema_binding_id")
        ),
        "interface_id": _text(approved.get("interface_id")),
        "connection_profile": _public_profile(profile),
        "database_table_ref": _text(approved.get("database_table_id")),
        "database_table_name": _text(approved.get("database_table_name")),
        "projection": projection,
        "identity_key": deepcopy(_list(approved.get("selected_identity_key"))),
        "identity_parameter_fingerprints": [_fingerprint(value)[:20] for value in values],
        "row_count": len(safe_rows),
        "match_status": match_status,
        "rows": safe_rows,
        "row_fingerprint": _fingerprint(safe_rows),
        "maximum_rows": _MAX_ROWS,
        "parameterized": True,
        "read_only": True,
        "write_attempted": False,
        "transaction_rolled_back": True,
        "raw_sql_retained": False,
        "predicate_values_retained": False,
        "secret_values_retained": False,
        "dsn_retained": False,
        "oracle_verdict_emitted": False,
    }
    return _receipt(
        observer_id=OBSERVER_ID,
        status="OBSERVED",
        evidence={EVIDENCE_KEY: payload, "observation_fingerprint": _fingerprint(payload)},
        campaign_id=campaign_id,
        execution_id=execution_id,
    )


def observe_approved_database_contract(envelope: dict[str, Any]) -> dict[str, Any]:
    """Registered Observer handler for one formal database Observer contract."""
    env = _dict(envelope)
    observations = _dict(env.get("observations"))
    prop = _dict(env.get("property") or _dict(env.get("assertion")).get("property"))
    experiment = _dict(env.get("experiment"))
    contract = _dict(
        prop.get("database_observer_contract")
        or observations.get("database_observer_contract")
        or experiment.get("database_observer_contract")
    )
    root = _text(prop.get("persistence_root") or experiment.get("persistence_root"))
    project = _text(prop.get("project") or experiment.get("project"))
    if not contract or not root or not project:
        return _receipt(
            observer_id=OBSERVER_ID,
            status="INDETERMINATE",
            reason_code="DATABASE_OBSERVER_RUNTIME_INPUT_MISSING",
            evidence={
                "contract_present": bool(contract),
                "root_present": bool(root),
                "project_present": bool(project),
                "secret_values_retained": False,
            },
        )
    runtime_values = _dict(
        observations.get("database_observer_runtime_values")
        or prop.get("database_observer_runtime_values")
    )
    return execute_database_observer_contract(
        contract,
        root=root,
        project=project,
        runtime_values=runtime_values,
        runtime_contract=_dict(experiment.get("runtime_contract")),
        connection_ref=_text(
            prop.get("database_connection_ref")
            or observations.get("database_connection_ref")
        ),
        envelope=env,
        campaign_id=_text(env.get("campaign_id")),
        execution_id=_text(env.get("execution_id")),
    )


def install_approved_database_observer() -> str:
    """Register the formal read-only database Observer; registration opens no connection."""
    if OBSERVER_ID in registered_observer_ids():
        return OBSERVER_ID
    return register_observer(
        OBSERVER_ID,
        surface=SURFACE,
        adapter=ADAPTER,
        handler=observe_approved_database_contract,
        evidence_keys=(EVIDENCE_KEY,),
    )


__all__ = [
    "ADAPTER",
    "EVIDENCE_KEY",
    "OBSERVER_ID",
    "DatabaseObserverRuntimeError",
    "execute_database_observer_contract",
    "install_approved_database_observer",
    "observe_approved_database_contract",
    "resolve_declared_read_only_database_profiles",
]
