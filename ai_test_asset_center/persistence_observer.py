"""Persistence-surface observer (adapter ``db_sql``).

Every built-in observer declares adapter ``http_api``, which made database-level defects
structurally unreachable: dangling references, aggregate-vs-detail divergence, state
values outside a declared enumeration and cross-store inconsistency all live in the
persistence layer, and nothing could measure them. An independent audit put the largest
single block of unreachable defect classes at that ceiling.

This module is the first non-http observer. It is deliberately narrow and read-only.

WHY IT IS NOT db_state_audit
============================
``db_state_audit.py`` already implements real checks (state enumeration, non-negative,
referential integrity, cross-table and cross-database amount reconciliation, sharding).
Its existing caller, ``private_pilot_db_audit_patch``, is installed nowhere and appends
raw finding dicts straight into ``result["findings"]`` — bypassing the observer →
assertion → contract-oracle → delivery-gate chain, so those findings carry no receipts
and could never pass the v2 gate. This module reuses the connection and introspection
helpers but produces a receipted OBSERVATION instead, which is what makes a persistence
defect deliverable rather than merely printed.

GOVERNANCE
==========
Three constraints, all fail-closed:

1. READ ONLY. Only SELECT is issued. There is no write path in this module, so the
   governed-write executor is not involved and cannot be bypassed.
2. CUSTOMER-DECLARED SOURCES ONLY. The DSN is assembled from
   ``platform_workspace/<project>/multi_service_config.json`` ``services[].db``, the same
   customer-declared file ``load_project_environment_kind`` reads the environment from.
   ``BENCHMARK_MANIFEST.json`` is deliberately NOT consulted: it is evaluator-owned, and
   AGENTS.md classifies the benchmark's database as evaluator-profile data that must never
   enter runtime context. The existing ``_discover_dsn`` reads it first, which is one
   reason that path is unfit for the product chain.
3. DECLARED NON-PRODUCTION ONLY. Reads require the same explicitly declared
   non-production environment as writes. An unknown, empty or production environment kind
   is refused. A hostname or a localhost DSN never implies safety.

IDENTIFIER SAFETY
=================
Table and column names are interpolated into SQL because no driver parameterizes
identifiers. Every identifier is therefore validated against the INTROSPECTED schema
before use — a name the database itself did not report is refused, not quoted and hoped
for. Values are never interpolated.

REGISTRATION IS OPT-IN
======================
``install_persistence_observer()`` must be called explicitly. Registering at import time
would mean any process importing the package could open connections to a customer
database, and AGENTS.md requires importing ``ai_test_asset_center`` to be side-effect free.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

OBSERVER_ID = "persistence_state_reader"
SURFACE = "persistence_state"
ADAPTER = "db_sql"
EVIDENCE_KEY = "persisted_entity_state"

# A conservative identifier shape. Anything outside it is refused before the schema check,
# so a malformed name cannot reach a query even if introspection somehow reported it.
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

# Row cap per observation. A persistence observation is evidence, not an export; an
# unbounded read against a customer database is an operational hazard.
MAX_OBSERVED_ROWS = 200


class PersistenceObserverError(RuntimeError):
    """A persistence observation cannot be performed or cannot be trusted."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _fingerprint(value: Any) -> str:
    blob = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _config_candidates(root: Path, project: str) -> list[Path]:
    """Customer-declared config only, in the same order the environment gate reads."""
    return [
        root / "platform_workspace" / project / "multi_service_config.json",
        root / "platform_inputs" / project / "multi_service_config.json",
    ]


def resolve_declared_data_sources(root: Path, project: str) -> list[dict[str, str]]:
    """Assemble DSNs from customer-declared per-service database blocks.

    Returns ``[{"service", "module", "dsn"}]``. Multi-service and multi-database
    topologies fall out naturally: one entry per service that declares a ``db`` block.

    A malformed config raises rather than returning an empty list, so a typo surfaces as
    an error instead of looking like "this project has no database".
    """
    for path in _config_candidates(root, project):
        if not path.is_file():
            continue
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PersistenceObserverError(
                f"declared_db_config_unreadable:{path.name}:{type(exc).__name__}"
            ) from exc
        sources: list[dict[str, str]] = []
        for service in _list(_dict(config).get("services")):
            block = _dict(_dict(service).get("db"))
            if not block:
                continue
            host = _text(block.get("host"))
            name = _text(block.get("name"))
            user = _text(block.get("user"))
            if not (host and name and user):
                # Partially declared is not "declared". Refuse rather than guess a
                # default host, port or database name.
                raise PersistenceObserverError(
                    "declared_db_config_incomplete:"
                    f"{_text(_dict(service).get('name')) or 'unnamed_service'}"
                )
            port = _text(block.get("port")) or "5432"
            password = _text(block.get("password"))
            # Credentials are stored at-rest encrypted (enc$v1$...) like every
            # other credential in this product. Feed the DSN the DECRYPTED value
            # — an encrypted envelope in a DSN corrupts libpq's URL parsing
            # (an enc$ string lands in the port option) and would fail with a
            # misleading error even when the database is healthy.
            if password.startswith("enc$v1$"):
                from .credential_crypto import decrypt as _decrypt_cred

                try:
                    password = _decrypt_cred(password)
                except Exception as exc:  # noqa: BLE001 - surfaced, never guessed
                    raise PersistenceObserverError(
                        "declared_db_password_decrypt_failed:"
                        f"{_text(_dict(service).get('name')) or 'unnamed_service'}:"
                        f"{type(exc).__name__}"
                    ) from exc
            sources.append({
                "service": _text(_dict(service).get("name")),
                "module": _text(_dict(service).get("name")) or "default",
                "dsn": f"postgresql://{user}:{password}@{host}:{port}/{name}",
            })
        return sources
    return []


def persistence_read_allowed(
    root: Path, project: str, runtime_contract: dict[str, Any] | None = None
) -> tuple[bool, str]:
    """Whether a persistence read is permitted. Returns ``(allowed, reason)``.

    Reads are gated on the same explicitly declared non-production environment as writes.
    Reading a production database is not harmless: it exposes real customer data to the
    observation record and to every downstream artifact.
    """
    from .sandbox_write_executor_base import resolve_environment_kind
    from .target_policy import is_nonproduction_environment, is_production_environment

    try:
        kind = _text(resolve_environment_kind(root, project, _dict(runtime_contract)))
    except Exception as exc:  # noqa: BLE001 - surfaced as a refusal, never swallowed
        return False, f"environment_kind_unreadable:{type(exc).__name__}"
    if not kind:
        return False, "environment_kind_undeclared"
    if is_production_environment(kind):
        return False, f"production_environment_blocked:{kind}"
    if not is_nonproduction_environment(kind):
        return False, f"environment_not_recognized_nonprod:{kind}"
    return True, "approved"


def _validated_identifier(name: str, allowed: "set[str] | frozenset[str]") -> str:
    """Return *name* only if the database itself reported it. Otherwise refuse."""
    candidate = _text(name)
    if not _SAFE_IDENTIFIER.match(candidate):
        raise PersistenceObserverError(f"identifier_shape_refused:{candidate[:40]}")
    if candidate not in allowed:
        raise PersistenceObserverError(f"identifier_not_in_introspected_schema:{candidate}")
    return candidate


def read_declared_entity_state(
    *,
    dsn: str,
    table: str,
    fields: list[str],
    max_rows: int = MAX_OBSERVED_ROWS,
) -> dict[str, Any]:
    """Read the declared fields of a declared table. SELECT only.

    Identifiers are validated against the introspected schema before interpolation, since
    no driver parameterizes identifiers. Values are never interpolated.
    """
    from .db_state_audit import DataSource, _close_data_source, _connect_data_source

    source = DataSource(dsn=dsn, module="observation")
    if not _connect_data_source(source):
        # Covers a missing driver, a refused connection and a failed introspection. The
        # helper logs the cause; the caller must treat this as "not observed", never as
        # "nothing was wrong".
        raise PersistenceObserverError("db_connection_or_introspection_failed")
    try:
        schema = _dict(source.schema)
        table_name = _validated_identifier(table, set(schema))
        # Introspection rows carry the column name under ``column_name``
        # (PostgreSQL information_schema) or ``name`` (other adapters).
        # Reading only one key silently empties the set and every declared
        # field fails as identifier_not_in_introspected_schema.
        columns = {
            _text(_dict(column).get("column_name") or _dict(column).get("name"))
            for column in _list(schema.get(table_name))
        }
        selected = [_validated_identifier(field, columns) for field in fields if _text(field)]
        if not selected:
            raise PersistenceObserverError("no_declared_field_present_in_schema")
        cursor = source.conn.cursor()
        try:
            column_list = ", ".join(selected)
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")  # noqa: S608 - validated
            row_count = int((cursor.fetchone() or [0])[0])
            cursor.execute(  # noqa: S608 - identifiers validated against introspection
                f"SELECT {column_list} FROM {table_name} LIMIT {int(max_rows)}"
            )
            rows = [
                {selected[index]: value for index, value in enumerate(record)}
                for record in cursor.fetchall()
            ]
        finally:
            cursor.close()
        return {
            "table": table_name,
            "fields": selected,
            "row_count": row_count,
            "rows": rows,
            "rows_truncated": row_count > len(rows),
            "max_rows": int(max_rows),
        }
    finally:
        _close_data_source(source)


def observe_persistence_state(envelope: dict[str, Any]) -> dict[str, Any]:
    """Observer handler. Emits a receipted persistence observation or a refusal.

    Every refusal carries a named reason code. A persistence observation that did not
    happen must never be indistinguishable from one that found nothing wrong.
    """
    from .observer_contracts_base import _receipt

    def refuse(reason_code: str, **evidence: Any) -> dict[str, Any]:
        return _receipt(
            observer_id=OBSERVER_ID,
            status="INDETERMINATE",
            reason_code=reason_code,
            evidence=dict(evidence),
        )

    spec = _dict(_dict(envelope.get("assertion")).get("property")) or _dict(
        envelope.get("property")
    )
    root_value = _text(spec.get("persistence_root"))
    project = _text(spec.get("project"))
    table = _text(spec.get("persistence_table"))
    fields = [_text(item) for item in _list(spec.get("persistence_fields")) if _text(item)]

    if not (root_value and project and table and fields):
        # Source-declared or nothing. The observer never guesses a table from an entity
        # name, because an inferred table name is an inferred business fact.
        return refuse(
            "PERSISTENCE_TARGET_NOT_SOURCE_DECLARED",
            missing=[
                key
                for key, value in (
                    ("persistence_root", root_value),
                    ("project", project),
                    ("persistence_table", table),
                    ("persistence_fields", fields),
                )
                if not value
            ],
        )

    root = Path(root_value)
    allowed, reason = persistence_read_allowed(
        root, project, _dict(_dict(envelope.get("experiment")).get("runtime_contract"))
    )
    if not allowed:
        return refuse("PERSISTENCE_READ_NOT_PERMITTED", detail=reason)

    try:
        sources = resolve_declared_data_sources(root, project)
    except PersistenceObserverError as exc:
        return refuse("PERSISTENCE_CONFIG_INVALID", detail=str(exc))
    if not sources:
        return refuse("PERSISTENCE_SOURCE_NOT_DECLARED", detail="no_service_declares_db")

    observations: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for source in sources:
        try:
            observed = read_declared_entity_state(
                dsn=source["dsn"], table=table, fields=fields
            )
        except PersistenceObserverError as exc:
            failures.append({"module": source["module"], "reason": str(exc)})
            continue
        observations.append({**observed, "module": source["module"]})

    if not observations:
        return refuse(
            "PERSISTENCE_NOT_OBSERVED",
            attempted_modules=[item["module"] for item in sources],
            failures=failures,
        )

    payload = {
        "surface": SURFACE,
        "adapter": ADAPTER,
        "table": table,
        "fields": fields,
        "modules_observed": [item["module"] for item in observations],
        "modules_failed": failures,
        "observations": observations,
    }
    return _receipt(
        observer_id=OBSERVER_ID,
        status="OBSERVED",
        reason_code="",
        evidence={
            EVIDENCE_KEY: payload,
            "observation_fingerprint": _fingerprint(payload),
            # A partially observed multi-database topology is reported as such rather
            # than presented as a complete reading.
            "coverage_complete": not failures,
        },
    )


def install_persistence_observer() -> str:
    """Register the persistence observer. Explicit, never at import time."""
    from .observer_contracts_base import register_observer

    return register_observer(
        OBSERVER_ID,
        surface=SURFACE,
        adapter=ADAPTER,
        handler=observe_persistence_state,
        evidence_keys=(EVIDENCE_KEY,),
    )
