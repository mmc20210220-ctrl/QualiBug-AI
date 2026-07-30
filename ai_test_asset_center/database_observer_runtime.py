"""Execute an approved database Observer contract on a declared non-production source.

Only the current ``database-observer-contract.v1`` authority is accepted. Runtime binding is
read-only, identity-scoped, schema-introspected, parameterized and row-capped. The receipt never
retains a password, DSN, host, user, raw SQL or raw identity predicate value, and it never emits an
Oracle verdict.
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
from urllib.parse import quote

from .observer_contracts_base import _receipt, register_observer, registered_observer_ids
from .persistence_observer import persistence_read_allowed

OBSERVER_ID = "approved_database_readback"
SURFACE = "database_read_only"
ADAPTER = "db_sql"
EVIDENCE_KEY = "approved_database_snapshot"
RUNTIME_RECEIPT_SCHEMA = "qualibug.database-observer-runtime-receipt.v1"

_MAX_ROWS = 2
_DEFAULT_CONNECT_TIMEOUT_SECONDS = 5
_DEFAULT_STATEMENT_TIMEOUT_MS = 5_000
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_READ_ONLY_MODES = {"read_only", "readonly", "read-only", "ro"}
_SECRET_TOKENS = {
    "authorization",
    "cookie",
    "credential",
    "credentials",
    "password",
    "passwd",
    "privatekey",
    "pwd",
    "secret",
    "token",
}


class DatabaseObserverRuntimeError(RuntimeError):
    """The approved Observer could not produce trustworthy runtime evidence."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value if value not in (None, "") else default)
    except (TypeError, ValueError) as exc:
        raise DatabaseObserverRuntimeError("database_observer_numeric_config_invalid") from exc
    return min(max(parsed, minimum), maximum)


def _config_candidates(root: Path, project: str) -> list[Path]:
    return [
        root / "platform_workspace" / project / "multi_service_config.json",
        root / "platform_inputs" / project / "multi_service_config.json",
    ]


def _declared_config(root: Path, project: str) -> tuple[dict[str, Any], Path | None]:
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
        return loaded, path
    return {}, None


def _dialect(block: dict[str, Any]) -> tuple[str, str]:
    declared = _text(
        block.get("dialect") or block.get("driver") or block.get("type")
    ).lower()
    aliases = {
        "mariadb": "mysql",
        "mysql": "mysql",
        "postgres": "postgresql",
        "postgresql": "postgresql",
        "psql": "postgresql",
        "sqlite": "sqlite",
        "sqlite3": "sqlite",
    }
    if declared:
        resolved = aliases.get(declared)
        if not resolved:
            raise DatabaseObserverRuntimeError(
                f"database_observer_dialect_unsupported:{declared}"
            )
        return resolved, "EXPLICIT_DECLARATION"
    if _text(block.get("path") or block.get("file")):
        return "sqlite", "DECLARED_FILE_DATABASE"
    # Existing multi_service_config DB blocks historically represented PostgreSQL.
    # This is transport compatibility only; no table, field or business mapping is inferred.
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
    if "password" in block:
        return _text(block.get("password")), "INLINE_DECLARED_SECRET"
    return "", "NONE"


def _resolved_sqlite_path(raw: str, config_path: Path | None) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        if config_path is None:
            raise DatabaseObserverRuntimeError(
                "database_observer_relative_sqlite_path_without_config"
            )
        path = config_path.parent / path
    return path.resolve(strict=False)


def resolve_declared_read_only_database_profiles(
    root: Path | str,
    project: str,
) -> list[dict[str, Any]]:
    """Resolve runtime-only profiles; every source must explicitly declare read-only access."""
    config, config_path = _declared_config(Path(root), project)
    profiles: list[dict[str, Any]] = []
    seen_refs: set[str] = set()
    for raw_service in _list(config.get("services")):
        service = _dict(raw_service)
        block = _dict(service.get("db"))
        if not block:
            continue
        connection_ref = _text(
            block.get("connection_ref")
            or block.get("service_ref")
            or service.get("name")
        )
        if not connection_ref:
            raise DatabaseObserverRuntimeError(
                "database_observer_connection_ref_missing"
            )
        if connection_ref in seen_refs:
            raise DatabaseObserverRuntimeError(
                f"database_observer_connection_ref_duplicate:{connection_ref}"
            )
        seen_refs.add(connection_ref)
        access_mode = _text(block.get("access_mode")).lower()
        if not (bool(block.get("read_only")) or access_mode in _READ_ONLY_MODES):
            raise DatabaseObserverRuntimeError(
                f"database_observer_read_only_declaration_required:{connection_ref}"
            )

        dialect, dialect_derivation = _dialect(block)
        password, password_source = _password(block)
        profile: dict[str, Any] = {
            "connection_ref": connection_ref,
            "service": _text(service.get("name")) or connection_ref,
            "dialect": dialect,
            "dialect_derivation": dialect_derivation,
            "read_only": True,
            "password_source": password_source,
            "password": password,
            "connect_timeout_seconds": _bounded_int(
                block.get("connect_timeout_seconds"),
                default=_DEFAULT_CONNECT_TIMEOUT_SECONDS,
                minimum=1,
                maximum=30,
            ),
            "statement_timeout_ms": _bounded_int(
                block.get("statement_timeout_ms"),
                default=_DEFAULT_STATEMENT_TIMEOUT_MS,
                minimum=100,
                maximum=30_000,
            ),
        }
        if dialect == "sqlite":
            raw_path = _text(block.get("path") or block.get("file") or block.get("name"))
            if not raw_path or raw_path == ":memory:":
                raise DatabaseObserverRuntimeError(
                    f"database_observer_sqlite_read_only_path_required:{connection_ref}"
                )
            resolved = _resolved_sqlite_path(raw_path, config_path)
            profile["path"] = str(resolved)
            profile["database"] = resolved.name
        else:
            host = _text(block.get("host"))
            database = _text(block.get("name") or block.get("database"))
            user = _text(block.get("user"))
            if not (host and database and user):
                raise DatabaseObserverRuntimeError(
                    f"database_observer_config_incomplete:{connection_ref}"
                )
            profile.update(
                {
                    "host": host,
                    "port": _bounded_int(
                        block.get("port"),
                        default=3306 if dialect == "mysql" else 5432,
                        minimum=1,
                        maximum=65_535,
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
        "path_retained": False,
    }


def _select_profile(
    profiles: list[dict[str, Any]], connection_ref: str
) -> dict[str, Any]:
    if connection_ref:
        matches = [
            row
            for row in profiles
            if _text(row.get("connection_ref")) == connection_ref
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


def _normalized_field(value: str) -> str:
    camel_split = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", _text(value))
    return re.sub(r"[^a-z0-9]+", "_", camel_split.lower()).strip("_")


def _sensitive_field(value: str) -> bool:
    normalized = _normalized_field(value)
    compact = normalized.replace("_", "")
    tokens = {token for token in normalized.split("_") if token}
    return bool(tokens.intersection(_SECRET_TOKENS)) or any(
        marker in compact
        for marker in ("accesstoken", "refreshtoken", "privatekey", "clientsecret")
    )


def _validate_identifier_shape(value: str) -> str:
    name = _text(value)
    if not _SAFE_IDENTIFIER.fullmatch(name):
        raise DatabaseObserverRuntimeError(
            f"database_observer_identifier_shape_refused:{name[:40]}"
        )
    return name


def _validate_contract(contract: dict[str, Any]) -> dict[str, Any]:
    row = deepcopy(_dict(contract))
    if _text(row.get("schema")) != "qualibug.database-observer-contract.v1":
        raise DatabaseObserverRuntimeError(
            "database_observer_contract_schema_invalid"
        )
    if _text(row.get("status")) != "READY_FOR_RUNTIME_CONNECTION_BINDING":
        raise DatabaseObserverRuntimeError(
            "database_observer_contract_not_runtime_bindable"
        )
    if not bool(row.get("runtime_observer_authoritative")):
        raise DatabaseObserverRuntimeError(
            "database_observer_runtime_authority_missing"
        )
    if not bool(row.get("read_only")) or bool(row.get("mutation_allowed")):
        raise DatabaseObserverRuntimeError(
            "database_observer_contract_not_read_only"
        )
    if bool(row.get("write_target_allowed")) or bool(
        row.get("oracle_authority_allowed")
    ):
        raise DatabaseObserverRuntimeError(
            "database_observer_contract_authority_scope_invalid"
        )
    if bool(row.get("connection_secret_embedded")):
        raise DatabaseObserverRuntimeError(
            "database_observer_contract_embedded_secret_refused"
        )

    table = _validate_identifier_shape(_text(row.get("database_table_name")))
    schema_name = _text(row.get("database_schema_name"))
    if schema_name:
        _validate_identifier_shape(schema_name)
    plan = _dict(row.get("query_plan"))
    if (
        _text(plan.get("operation")) != "SELECT_ONE"
        or not bool(plan.get("parameterized"))
        or _text(plan.get("raw_sql"))
        or int(plan.get("maximum_rows") or 0) != _MAX_ROWS
    ):
        raise DatabaseObserverRuntimeError(
            "database_observer_query_plan_invalid"
        )

    projection = [
        _validate_identifier_shape(_text(value))
        for value in _list(plan.get("projection"))
        if _text(value)
    ]
    predicates = [
        dict(value)
        for value in _list(row.get("identity_predicates"))
        if isinstance(value, dict)
    ]
    selected_key = [
        _validate_identifier_shape(_text(value))
        for value in _list(row.get("selected_identity_key"))
        if _text(value)
    ]
    predicate_names = [
        _validate_identifier_shape(_text(item.get("database_field_name")))
        for item in predicates
    ]
    if (
        not table
        or not projection
        or len(set(projection)) != len(projection)
        or not predicates
        or not selected_key
        or predicate_names != selected_key
        or any(_text(item.get("operator")) != "=" for item in predicates)
        or any(not _text(item.get("value_source")) for item in predicates)
    ):
        raise DatabaseObserverRuntimeError(
            "database_observer_query_contract_incomplete"
        )
    sensitive = sorted(
        name
        for name in {*projection, *predicate_names}
        if _sensitive_field(name)
    )
    if sensitive:
        raise DatabaseObserverRuntimeError(
            "database_observer_secret_field_refused:" + ",".join(sensitive)
        )
    row["database_table_name"] = table
    row["database_schema_name"] = schema_name
    row["identity_predicates"] = predicates
    row["selected_identity_key"] = selected_key
    row["query_plan"] = {**plan, "projection": projection}
    return row


def _validated_live_identifier(name: str, allowed: Iterable[str]) -> str:
    candidate = _validate_identifier_shape(name)
    if candidate not in set(allowed):
        raise DatabaseObserverRuntimeError(
            f"database_observer_identifier_not_introspected:{candidate}"
        )
    return candidate


def _quote_identifier(name: str, dialect: str) -> str:
    return f"`{name}`" if dialect == "mysql" else f'"{name}"'


def _connect(profile: dict[str, Any]) -> Any:
    dialect = _text(profile.get("dialect"))
    if dialect == "sqlite":
        path = Path(_text(profile.get("path")))
        uri = "file:" + quote(path.as_posix(), safe="/:") + "?mode=ro"
        conn = sqlite3.connect(
            uri,
            uri=True,
            timeout=float(
                profile.get("connect_timeout_seconds")
                or _DEFAULT_CONNECT_TIMEOUT_SECONDS
            ),
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
                "SELECT set_config('statement_timeout', %s, true)",
                (str(profile.get("statement_timeout_ms") or _DEFAULT_STATEMENT_TIMEOUT_MS),),
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
        timeout_seconds = max(
            1,
            int(
                profile.get("statement_timeout_ms")
                or _DEFAULT_STATEMENT_TIMEOUT_MS
            )
            // 1000,
        )
        conn = pymysql.connect(
            host=profile.get("host"),
            port=int(profile.get("port") or 3306),
            database=profile.get("database"),
            user=profile.get("user"),
            password=profile.get("password"),
            connect_timeout=int(
                profile.get("connect_timeout_seconds")
                or _DEFAULT_CONNECT_TIMEOUT_SECONDS
            ),
            read_timeout=timeout_seconds,
            write_timeout=timeout_seconds,
            autocommit=False,
        )
        cursor = conn.cursor()
        try:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
        finally:
            cursor.close()
        return conn
    raise DatabaseObserverRuntimeError(
        f"database_observer_dialect_unsupported:{dialect}"
    )


def _introspect(
    conn: Any,
    profile: dict[str, Any],
    schema_name: str,
) -> dict[str, set[str]]:
    dialect = _text(profile.get("dialect"))
    cursor = conn.cursor()
    try:
        if dialect == "sqlite":
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            result: dict[str, set[str]] = {}
            for raw in cursor.fetchall():
                table = _text(raw[0] if raw else "")
                if not _SAFE_IDENTIFIER.fullmatch(table):
                    continue
                cursor.execute(f'PRAGMA table_info("{table}")')
                result[table] = {
                    _text(row[1])
                    for row in cursor.fetchall()
                    if len(row) > 1 and _text(row[1])
                }
            return result
        if dialect == "postgresql":
            cursor.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema=%s ORDER BY table_name, ordinal_position",
                (schema_name or "public",),
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


def _merge_parameters(target: dict[str, Any], source: Any) -> None:
    if isinstance(source, dict):
        target.update(source)
        return
    for raw in _list(source):
        row = _dict(raw)
        name = _text(row.get("field") or row.get("name"))
        if not name:
            continue
        if "materialized_value" in row:
            target[name] = row.get("materialized_value")
        elif "value" in row:
            target[name] = row.get("value")


def _runtime_context(
    runtime_values: dict[str, Any] | None,
    envelope: dict[str, Any] | None,
) -> dict[str, Any]:
    explicit = deepcopy(_dict(runtime_values))
    env = _dict(envelope)
    observations = _dict(env.get("observations"))
    treatment = _dict(
        env.get("treatment_observation")
        or observations.get("treatment_observation")
    )
    experiment = _dict(env.get("experiment"))
    materialized = _dict(
        experiment.get("materialized_request")
        or observations.get("materialized_request")
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
        _merge_parameters(request_parameters, source)
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


def _get_path(value: Any, parts: list[str]) -> Any:
    current = value
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _resolve_value(source: str, context: dict[str, Any]) -> Any:
    direct = _dict(context.get("direct"))
    if source in direct:
        return direct[source]
    parts = [_text(value) for value in source.split(".") if _text(value)]
    return _get_path(context, parts) if parts else None


def _execute_query(
    *,
    approved: dict[str, Any],
    profile: dict[str, Any],
    values: list[Any],
    connection_factory: Callable[[dict[str, Any]], Any] | None,
) -> tuple[list[dict[str, Any]], list[str], bool]:
    conn = None
    cursor = None
    rows: list[dict[str, Any]] = []
    projection: list[str] = []
    failure: DatabaseObserverRuntimeError | None = None
    rollback_ok = False
    try:
        conn = (connection_factory or _connect)(profile)
        schema_name = _text(approved.get("database_schema_name"))
        schema = _introspect(conn, profile, schema_name)
        table = _validated_live_identifier(
            _text(approved.get("database_table_name")), schema.keys()
        )
        columns = schema.get(table, set())
        projection = [
            _validated_live_identifier(_text(value), columns)
            for value in _list(_dict(approved.get("query_plan")).get("projection"))
        ]
        predicates = _list(approved.get("identity_predicates"))
        predicate_columns = [
            _validated_live_identifier(
                _text(_dict(row).get("database_field_name")), columns
            )
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
        projection_sql = ", ".join(
            _quote_identifier(name, dialect) for name in projection
        )
        predicate_sql = " AND ".join(
            f"{_quote_identifier(name, dialect)} = {placeholder}"
            for name in predicate_columns
        )
        statement = (
            f"SELECT {projection_sql} FROM {qualified} "
            f"WHERE {predicate_sql} LIMIT {_MAX_ROWS}"
        )
        cursor = conn.cursor()
        cursor.execute(statement, tuple(values))
        rows = [
            {
                projection[index]: record[index]
                for index in range(min(len(projection), len(record)))
            }
            for record in cursor.fetchall()
        ]
    except DatabaseObserverRuntimeError as exc:
        failure = exc
    except Exception as exc:  # noqa: BLE001 - never expose driver text or credentials
        failure = DatabaseObserverRuntimeError(
            f"database_observer_driver_failure:{type(exc).__name__}"
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
        raise DatabaseObserverRuntimeError(
            "database_observer_transaction_rollback_failed"
        )
    if failure is not None:
        raise failure
    return rows, projection, rollback_ok


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
    """Execute one approved identity-bound read and return a typed Observer receipt."""

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
    except DatabaseObserverRuntimeError as exc:
        return refuse("DATABASE_OBSERVER_CONTRACT_REFUSED", detail=str(exc))

    allowed, detail = persistence_read_allowed(
        Path(root), project, runtime_contract
    )
    if not allowed:
        return refuse("DATABASE_OBSERVER_READ_NOT_PERMITTED", detail=detail)

    try:
        profiles = resolve_declared_read_only_database_profiles(root, project)
        profile = _select_profile(profiles, _text(connection_ref))
    except DatabaseObserverRuntimeError as exc:
        return refuse(
            "DATABASE_OBSERVER_CONNECTION_BINDING_FAILED", detail=str(exc)
        )

    context = _runtime_context(runtime_values, envelope)
    values: list[Any] = []
    missing_sources: list[str] = []
    for raw in _list(approved.get("identity_predicates")):
        source = _text(_dict(raw).get("value_source"))
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

    try:
        rows, projection, rolled_back = _execute_query(
            approved=approved,
            profile=profile,
            values=values,
            connection_factory=connection_factory,
        )
    except DatabaseObserverRuntimeError as exc:
        return refuse(
            "DATABASE_OBSERVER_QUERY_FAILED",
            detail=str(exc),
            observer_contract_ref=approved.get("observer_id"),
            connection_profile=_public_profile(profile),
            transaction_rolled_back=(
                "transaction_rollback_failed" not in str(exc)
            ),
        )

    safe_rows = _json_safe(rows)
    match_status = (
        "NOT_FOUND"
        if not safe_rows
        else "MATCHED_ONE"
        if len(safe_rows) == 1
        else "NON_UNIQUE_IDENTITY"
    )
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
        "identity_key": deepcopy(
            _list(approved.get("selected_identity_key"))
        ),
        "identity_parameter_fingerprints": [
            _fingerprint(value)[:20] for value in values
        ],
        "row_count": len(safe_rows),
        "match_status": match_status,
        "rows": safe_rows,
        "row_fingerprint": _fingerprint(safe_rows),
        "maximum_rows": _MAX_ROWS,
        "parameterized": True,
        "read_only": True,
        "write_attempted": False,
        "transaction_rolled_back": rolled_back,
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


def observe_approved_database_contract(
    envelope: dict[str, Any]
) -> dict[str, Any]:
    """Registered handler for one formal database Observer contract."""
    env = _dict(envelope)
    observations = _dict(env.get("observations"))
    prop = _dict(
        env.get("property") or _dict(env.get("assertion")).get("property")
    )
    experiment = _dict(env.get("experiment"))
    contract = _dict(
        prop.get("database_observer_contract")
        or observations.get("database_observer_contract")
        or experiment.get("database_observer_contract")
    )
    root = _text(
        prop.get("persistence_root") or experiment.get("persistence_root")
    )
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
                "dsn_retained": False,
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
    """Register the read-only Observer; registration opens no connection."""
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
