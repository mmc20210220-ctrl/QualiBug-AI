from __future__ import annotations

"""Resolve a cleanup compensator across every adapter the operator declared.

A governed write needs a way back. Until now the only candidate was a source-declared
API compensator, so a target whose API offers no undo blocked every write obligation:
on a live 11-service benchmark, 680 of 1189 obligations ended at
``BLOCKED_NON_REVERSIBLE_WRITE`` and ``_declared_cleanup_operations`` resolved a
compensator for 2 of 17 writes. Checked against that API, the absence is real -- no
DELETE for products, none for cart items, no reversal for a payment.

But "the API has no undo" is not the same as "this cannot be cleaned up". A test that
creates its own row can delete that row through the database, or drive the admin UI, if
the operator declared those surfaces. Refusing to test at all because one adapter is
missing turns a capability gap into a coverage gap.

This is NOT a relaxation of the reversibility requirement. The gate asks for a real,
executable compensator that leaves a receipt, and every tier here provides one. What
changes is that the question is asked of every declared adapter instead of only HTTP.

The line that does not move: **a data-layer cleanup may only touch rows this run
created.** Deleting a row the customer already had is not cleanup, it is damage, and no
declaration makes it acceptable. Ownership is proven by the run's own creation marker or
a recorded creation receipt -- never inferred from a row merely looking test-shaped.

KNOWN LIMIT, found by running this against the live target: a single-table row delete is
not sufficient for an entity with dependents. Deleting 16 run-created products failed
with ForeignKeyViolation on every one, because inventory and order_items reference them;
the 25 cart_items rows deleted cleanly because nothing references those. Dependency-
ordered deletion, derived from the schema's foreign keys, is required before this tier
can be relied on -- and until then the executor's loud FAILED receipt is the correct
outcome, not a silent partial cleanup.
"""

import re
import uuid
from typing import Any

LADDER_SCHEMA = "qualibug.cleanup-adapter-ladder.v1"

# Preference order. An API compensator the source declared is the highest-fidelity
# undo: it is the vendor's own reversal path and exercises the same code the customer
# would. The data layer is last because it bypasses application logic -- it removes the
# row without running whatever the application would have run.
ADAPTER_PREFERENCE: tuple[str, ...] = ("http_api", "ui_browser", "db_sql")

TIER_API = "source_declared_api_compensator"
TIER_UI = "declared_ui_cleanup"
TIER_DB = "declared_database_row_delete"

# Reason codes, one per condition, so a block says which leg was missing.
REASON_NO_ADAPTER = "CLEANUP_NO_DECLARED_ADAPTER"
REASON_NO_TABLE = "CLEANUP_TABLE_NOT_SOURCE_DECLARED"
REASON_NO_IDENTITY = "CLEANUP_ROW_IDENTITY_NOT_RESOLVABLE"
REASON_NOT_RUN_OWNED = "CLEANUP_ROW_NOT_CREATED_BY_THIS_RUN"
REASON_MUTATION_NOT_ATTESTED = "CLEANUP_MUTATION_NOT_ATTESTED"
REASON_NOT_APPROVED = "CLEANUP_TARGET_NOT_APPROVED_FOR_WRITE"

# Industry-neutral primary-key aliases. Domain field names (orderId, sku, …)
# must never be hard-coded here — callers supply the declared identity_column.
_GENERIC_PRIMARY_KEY_ALIASES: tuple[str, ...] = ("id", "uuid", "guid", "key")

# Common response envelopes. Same vocabulary as sandbox write identity
# extraction — never domain entity names.
_RESPONSE_ENTITY_ENVELOPES: tuple[str, ...] = ("data", "result", "item", "resource")

# SQL identifiers must be bare names — never interpolated free text.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def identity_body_keys(identity_column: str) -> tuple[str, ...]:
    """Keys allowed when reading an identity from a body or binding map.

    Always includes the declared column. When that column itself is a generic
    primary key, also accept the other generic aliases. Never invents domain
    field names.
    """
    column = _text(identity_column) or "id"
    keys: list[str] = [column]
    if column.lower() in _GENERIC_PRIMARY_KEY_ALIASES:
        for alias in _GENERIC_PRIMARY_KEY_ALIASES:
            if alias not in keys and alias.lower() != column.lower():
                keys.append(alias)
    return tuple(keys)


def _identity_value_from_flat_body(body: Any, identity_column: str) -> str:
    """One-level identity extract; no envelope descent."""
    row = _dict(body)
    allowed_keys = identity_body_keys(identity_column)
    generic = (_text(identity_column) or "id").lower() in _GENERIC_PRIMARY_KEY_ALIASES
    matches: list[str] = []
    for key, raw_value in row.items():
        if generic:
            if _text(key).lower() not in {item.lower() for item in allowed_keys}:
                continue
        elif key not in allowed_keys:
            continue
        if isinstance(raw_value, bool) or not isinstance(
            raw_value,
            (str, int, float),
        ):
            continue
        value = _text(raw_value)
        if value:
            matches.append(value)
    unique = list(dict.fromkeys(matches))
    return unique[0] if len(unique) == 1 else ""


def identity_value_from_body(body: Any, identity_column: str) -> str:
    """Extract a scalar identity from a body using only declared/generic keys.

    Prefer top-level keys. When those are absent, accept exactly one match
    under a generic response envelope (``data`` / ``result`` / ``item`` /
    ``resource``). Conflicting nested identities remain unbound.
    """
    row = _dict(body)
    direct = _identity_value_from_flat_body(row, identity_column)
    if direct:
        return direct
    nested_matches: list[str] = []
    for envelope in _RESPONSE_ENTITY_ENVELOPES:
        nested = row.get(envelope)
        if isinstance(nested, dict):
            value = _identity_value_from_flat_body(nested, identity_column)
            if value:
                nested_matches.append(value)
    unique = list(dict.fromkeys(nested_matches))
    return unique[0] if len(unique) == 1 else ""


def _identity_scalar_values(body: Any) -> list[tuple[str, str]]:
    """Conventional primary-identity scalars in a body as (normalized_key, value).

    Walks top-level fields, standard response envelopes, then deeper nested
    dicts. Only primary resource identity keys (id/uuid/guid/key by normalized
    name) qualify: foreign-key fields (orderId, userId, addressId) name another
    row, never the created row itself, and must never masquerade as the
    resource identity. Child collections are never descended into.
    """
    values: list[tuple[str, str]] = []
    seen_values: set[str] = set()
    stack: list[Any] = [body]

    def _primary_key(normalized: str) -> bool:
        return normalized in {"id", "uuid", "guid", "key"}

    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, child in node.items():
                normalized = "".join(ch for ch in str(key).lower() if ch.isalnum())
                if isinstance(child, dict):
                    stack.append(child)
                    continue
                if isinstance(child, list):
                    continue
                if isinstance(child, bool) or not isinstance(
                    child,
                    (str, int, float),
                ):
                    continue
                value = _text(child)
                if not value or value in seen_values:
                    continue
                seen_values.add(value)
                if _primary_key(normalized):
                    values.append((normalized, value))
        elif isinstance(node, list):
            for child in node:
                if isinstance(child, (dict, list)):
                    stack.append(child)
    return values


def observed_resource_identity(body: Any, identity_column: str = "") -> str:
    """Resolve the created resource identity from an observed response body.

    Resolution order, each level fail-closed to the next:
    1. the declared identity column (``identity_value_from_body`` semantics);
    2. a conventional primary identity key (id/uuid/guid/key by normalized
       name — case-insensitive, envelope and nesting tolerant) observed in the
       same body.

    Returns "" when nothing resolves or the body is ambiguous, so callers
    (adapter cleanup identity, creation receipts) stay fail-closed. Only
    conventional primary resource identity shape is used — never foreign-key
    or domain field names — so a created row is resolvable no matter which
    conventional spelling the target's create response used (``id``, ``Id``,
    ``_id``, ``uuid``, …) as long as the run observed it.
    """
    if identity_column:
        declared = identity_value_from_body(body, identity_column)
        if declared:
            return declared
    scalars = _identity_scalar_values(body)
    if not scalars:
        return ""
    unique = list(dict.fromkeys(value for _, value in scalars))
    return unique[0] if len(unique) == 1 else ""


def entity_storage_table(entity: Any) -> str:
    """Source-declared physical table for an entity, if any.

    Prefers an explicit table/storage field. Falls back to the entity name.
    Never invents a plural form — schema-derived aliases stay in
    ``source_entity_names`` for runtime catalog resolution.
    """
    row = _dict(entity)
    return _text(
        row.get("table")
        or row.get("storage_table")
        or row.get("db_table")
        or row.get("name")
    )


def storage_table_candidates(
    declared_table: Any,
    *,
    entities: Any = None,
) -> list[str]:
    """Ordered storage-table candidates from the plan and source entity aliases."""
    declared = _text(declared_table)
    candidates: list[str] = []
    if declared:
        candidates.append(declared)
    for raw in _list(entities):
        row = _dict(raw)
        names = {
            _text(row.get("name")).lower(),
            *{_text(alias).lower() for alias in _list(row.get("source_entity_names"))},
            _text(row.get("table")).lower(),
        }
        names.discard("")
        if declared and declared.lower() not in names:
            continue
        for value in (
            entity_storage_table(row),
            *[_text(alias) for alias in _list(row.get("source_entity_names"))],
            _text(row.get("name")),
        ):
            if value and value not in candidates and _IDENTIFIER_RE.match(value):
                candidates.append(value)
    return candidates


def resolve_storage_table_against_catalog(
    declared_table: Any,
    *,
    entities: Any = None,
    connection: Any = None,
) -> tuple[str, str]:
    """Bind a cleanup table to one catalog-present, source-declared name.

    Returns ``(table, reason_code)``. ``reason_code`` is empty on success.
    Uses only plan/entity aliases — never invents plural spellings. When a
    live connection is available, information_schema is the authority for
    which candidate exists.
    """
    candidates = storage_table_candidates(declared_table, entities=entities)
    if not candidates:
        return "", REASON_NO_TABLE
    if connection is None:
        # Compile/plan time: prefer explicit entity.table via candidates order.
        return candidates[0], ""

    existing: set[str] = set()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = current_schema()
              AND table_type = 'BASE TABLE'
              AND lower(table_name) = ANY(%s)
            """,
            ([name.lower() for name in candidates],),
        )
        for row in cursor.fetchall() or []:
            if row and row[0]:
                existing.add(str(row[0]))
    except Exception as exc:
        return "", f"CLEANUP_DB_TABLE_CATALOG_FAILED:{type(exc).__name__}:{exc}"[:180]

    # Preserve candidate order; keep catalog's exact spelling when case differs.
    existing_lower = {name.lower(): name for name in existing}
    matched = [
        existing_lower[name.lower()]
        for name in candidates
        if name.lower() in existing_lower
    ]
    unique = list(dict.fromkeys(matched))
    if len(unique) == 1:
        return unique[0], ""
    if not unique:
        return "", (
            "CLEANUP_TABLE_NOT_IN_SCHEMA:"
            + ",".join(candidates[:6])
        )
    return "", (
        "CLEANUP_TABLE_AMBIGUOUS_IN_SCHEMA:"
        + ",".join(unique[:6])
    )


def mutation_attested_for_identity(
    *,
    identity_value: Any,
    identity_column: str = "id",
    mutation_attestation: Any = None,
) -> tuple[bool, str]:
    """Whether this run attested a governed mutation of the named row.

    Field restore UPDATEs pre-existing customer rows. That is only safe when
    governed before/after snapshots prove this write mutated that exact identity
    and an accepted write receipt owns the mutation scope. Anything less is
    refusal — never an unbound UPDATE guess.
    """
    identity = _text(identity_value)
    if not identity:
        return False, "identity_empty"
    att = _dict(mutation_attestation)
    if not att:
        return False, "mutation_attestation_missing"
    if _text(att.get("identity_value")) != identity:
        return False, "attestation_identity_mismatch"
    if att.get("accepted_write") is not True:
        return False, "attestation_write_not_accepted"
    if not _text(att.get("write_receipt_ref")):
        return False, "attestation_write_receipt_missing"
    before_body = _dict(att.get("before_body"))
    after_body = _dict(att.get("after_body"))
    if not before_body or not after_body:
        return False, "attestation_snapshots_missing"
    column = _text(identity_column) or "id"
    before_id = identity_value_from_body(before_body, column)
    after_id = identity_value_from_body(after_body, column)
    if before_id != identity or after_id != identity:
        return False, "attestation_entity_id_mismatch"
    restore_fields = _dict(att.get("restore_fields"))
    if not restore_fields:
        return False, "attestation_restore_fields_empty"
    for field, restore_value in restore_fields.items():
        if field not in before_body or field not in after_body:
            return False, "attestation_restore_field_unobserved"
        before_value = before_body[field]
        if restore_value != before_value:
            return False, "attestation_restore_value_mismatch"
        if before_value == after_body[field]:
            return False, "attestation_field_not_mutated"
    return True, "governed_mutation_attested"

# The marker this product stamps on data it creates. A row carrying it was written by a
# QualiBug run; a row without it is the customer's.
RUN_CREATED_MARKERS: tuple[str, ...] = ("qb_auto", "qb_test", "qbbootstrap")
_MARKER_RE = re.compile("|".join(re.escape(m) for m in RUN_CREATED_MARKERS), re.I)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def row_was_created_by_this_run(
    identity_value: Any,
    *,
    creation_receipts: Any = None,
    table: str = "",
) -> tuple[bool, str]:
    """Whether this run created the row, and the evidence for saying so.

    Two proofs are accepted and nothing else. A recorded creation receipt naming the
    same table and identity is the strong one. The run's own marker embedded in the
    identity is the weaker one, and it is still a proof rather than a guess: the value
    contains a string only this product writes.

    Anything else is the customer's data.
    """
    value = _text(identity_value)
    if not value:
        return False, "identity_empty"

    for receipt in _list(creation_receipts):
        row = _dict(receipt)
        if _text(row.get("status")).lower() not in ("", "created", "ok", "success"):
            continue
        receipt_table = _text(row.get("table") or row.get("entity") or row.get("target"))
        if table and receipt_table and receipt_table.lower() != _text(table).lower():
            continue
        for key in ("identity_value", "resource_id", "entity_id", "id", "created_id"):
            if _text(row.get(key)) and _text(row.get(key)) == value:
                return True, "creation_receipt"

    if _MARKER_RE.search(value):
        return True, "run_created_marker"
    return False, "no_ownership_evidence"


def resolve_cleanup_adapter(
    *,
    available_adapters: Any,
    api_compensator: Any = None,
    ui_cleanup_declared: bool = False,
    entity: Any = None,
    identity_value: Any = None,
    creation_receipts: Any = None,
    target_write_approved: bool = True,
    availability_only: bool = False,
) -> dict[str, Any]:
    """Pick the highest-fidelity cleanup this target actually supports.

    ``availability_only`` answers "is this tier reachable for this entity" without a
    concrete row. Compile time has no row identity -- it is bound at runtime -- so the
    ownership check cannot run there, and a probe that supplied a placeholder identity
    would simply always fail it. A plan produced this way carries
    ``requires_ownership_proof`` and is never executable on its own: the executor
    re-runs the ownership check against the real value and refuses without it.

    Returns ``{schema_version, tier, plan, reason_code, detail}``. ``tier`` is empty when
    nothing resolved, and ``reason_code`` then names the missing leg rather than a
    generic failure -- an operator reading it should learn what to declare.
    """
    declared = {_text(name) for name in _list(list(available_adapters or []))}
    result: dict[str, Any] = {
        "schema_version": LADDER_SCHEMA,
        "tier": "",
        "plan": {},
        "reason_code": "",
        "detail": "",
        "considered": [],
    }

    # Tier 1 — the vendor's own reversal path.
    compensator = _dict(api_compensator)
    if compensator.get("operation_ref") or compensator.get("path"):
        result.update({
            "tier": TIER_API,
            "plan": {
                "adapter": "http_api",
                "operation_ref": _text(compensator.get("operation_ref")),
                "method": _text(compensator.get("method")).upper() or "DELETE",
                "path": _text(compensator.get("path")),
                "mode": "reverse_order",
            },
        })
        return result
    result["considered"].append(TIER_API)

    # Tier 2 — drive the interface a human would use.
    if ui_cleanup_declared and "ui_browser" in declared:
        result.update({
            "tier": TIER_UI,
            "plan": {
                "adapter": "ui_browser",
                "mode": "ui_directed_cleanup",
                "entity": _text(_dict(entity).get("name")),
                "identity_value": _text(identity_value),
            },
        })
        return result
    result["considered"].append(TIER_UI)

    # Tier 3 — the data layer, under every condition.
    if "db_sql" not in declared:
        result["reason_code"] = REASON_NO_ADAPTER
        result["detail"] = "no cleanup adapter declared beyond http_api"
        return result
    if not target_write_approved:
        # A read-only or unapproved target must never be written to, and a delete is a
        # write. This is the same gate the governed write executor applies.
        result["reason_code"] = REASON_NOT_APPROVED
        result["detail"] = "target policy does not approve writes to this environment"
        return result

    entity_row = _dict(entity)
    table = entity_storage_table(entity_row)
    identity_fields = [_text(f) for f in _list(entity_row.get("identity_fields")) if _text(f)]
    if not table or not identity_fields:
        result["reason_code"] = REASON_NO_TABLE
        result["detail"] = f"table={table or '?'} identity_fields={identity_fields}"
        return result

    if availability_only:
        result.update({
            "tier": TIER_DB,
            "availability_only": True,
            "plan": {
                "adapter": "db_sql",
                "mode": "row_delete",
                "table": table,
                "identity_column": identity_fields[0],
                "scope": "run_created_only",
                # Not optional. Without a proven owner the executor refuses, so this
                # plan cannot delete anything on its own.
                "requires_ownership_proof": True,
            },
        })
        return result

    value = _text(identity_value)
    if not value:
        result["reason_code"] = REASON_NO_IDENTITY
        result["detail"] = f"no concrete value for {table}.{identity_fields[0]}"
        return result

    owned, basis = row_was_created_by_this_run(
        value, creation_receipts=creation_receipts, table=table
    )
    if not owned:
        # The line that does not move. Deleting a row the customer already had is not
        # cleanup, and no declaration makes it acceptable.
        result["reason_code"] = REASON_NOT_RUN_OWNED
        result["detail"] = f"{table}.{identity_fields[0]}={value[:60]} ({basis})"
        return result

    result.update({
        "tier": TIER_DB,
        "plan": {
            "adapter": "db_sql",
            "mode": "row_delete",
            "table": table,
            "identity_column": identity_fields[0],
            "identity_value": value,
            "scope": "run_created_only",
            "ownership_basis": basis,
        },
    })
    return result


def prefer_constructed_data(
    *,
    constructed: Any = None,
    existing: Any = None,
) -> dict[str, Any]:
    """Choose the subject a write experiment should act on.

    Data the run constructs is preferred: it is disposable, its cleanup is the run's own
    responsibility, and a failed assertion against it cannot damage anything the customer
    depends on. Existing test-system data is the fallback, used only when nothing can be
    constructed -- and it is flagged, because a write against it is not disposable and the
    cleanup ladder must find a real compensator before it may proceed.
    """
    if constructed not in (None, "", [], {}):
        return {
            "schema_version": LADDER_SCHEMA,
            "source": "run_constructed",
            "subject": constructed,
            "disposable": True,
            "cleanup_required": True,
        }
    if existing not in (None, "", [], {}):
        return {
            "schema_version": LADDER_SCHEMA,
            "source": "existing_test_system_data",
            "subject": existing,
            "disposable": False,
            "cleanup_required": True,
            "note": (
                "not run-created: a data-layer delete is refused for this subject and a "
                "declared compensator is required before any write"
            ),
        }
    return {
        "schema_version": LADDER_SCHEMA,
        "source": "",
        "subject": None,
        "disposable": False,
        "cleanup_required": True,
        "reason_code": "NO_SUBJECT_AVAILABLE",
    }


# ── execution ───────────────────────────────────────────────────────────────

EXECUTION_SCHEMA = "qualibug.cleanup-adapter-execution.v1"

# Reason codes for the target-policy gate, mirroring the HTTP governed-write gate
# (``target_policy.build_target_policy_decision`` / ``primary_write_block``) so an
# operator sees the identical vocabulary regardless of which adapter refused.
REASON_TARGET_POLICY_UNAVAILABLE = "CLEANUP_TARGET_POLICY_UNAVAILABLE"


def _adapter_write_policy_allowed(
    *,
    root: Any,
    project: str,
    runtime_contract: dict[str, Any] | None,
    policy_decision: dict[str, Any] | None,
) -> tuple[bool, str]:
    """Gate a data-layer write behind the identical authority as governed HTTP writes.

    A database DELETE is a write like any other -- it has no HTTP status code to fail
    closed on, so the same non-production/approval decision must be computed and checked
    before a connection is even opened.  ``policy_decision`` lets a caller that already
    holds a ``TargetPolicyDecision`` (or a test) reuse it directly instead of forcing a
    re-read of project config from disk.
    """
    from .target_policy import primary_write_block

    if policy_decision is not None:
        decision = _dict(policy_decision)
    elif root is not None and _text(project):
        from pathlib import Path

        from .sandbox_write_executor_base import target_policy_decision as _tpd

        decision = _tpd(
            root=Path(root), project=str(project), runtime_contract=dict(runtime_contract or {})
        )
    else:
        # Neither a precomputed decision nor enough identity to compute one.
        # Fail closed rather than assume an unevaluated target is safe.
        return False, REASON_TARGET_POLICY_UNAVAILABLE

    if not bool(decision.get("write_allowed")):
        return False, primary_write_block(decision)
    return True, ""


def execute_declared_adapter_field_restore(
    step: dict[str, Any],
    *,
    identity_value: Any,
    restore_fields: dict[str, Any],
    dsn: str = "",
    connect: Any = None,
    root: Any = None,
    project: str = "",
    runtime_contract: dict[str, Any] | None = None,
    policy_decision: dict[str, Any] | None = None,
    mutation_attestation: dict[str, Any] | None = None,
    entities: Any = None,
) -> dict[str, Any]:
    """Restore source-observed scalar fields on an existing row this run mutated.

    Identity-bound action POSTs (ship/confirm/…) mutate customer rows. Row delete
    is the wrong compensator: ownership refuses them, and deleting would destroy
    pre-existing data. The honest undo is an UPDATE of exactly the scalar fields
    the governed before/after snapshots prove this write changed.

    Fail-closed: refuse UPDATE unless ``mutation_attestation`` proves an accepted
    governed write mutated this exact identity (before/after + ownership scope).
    """
    plan = _dict(step)
    receipt: dict[str, Any] = {
        "schema_version": EXECUTION_SCHEMA,
        "receipt_id": f"cleanup_adapter_{uuid.uuid4().hex}",
        "adapter": _text(plan.get("adapter")) or "db_sql",
        "table": _text(plan.get("table")),
        "identity_column": _text(plan.get("identity_column")) or "id",
        "identity_value": _text(identity_value),
        "status": "REFUSED",
        "reason_code": "",
        "rows_deleted": 0,
        "rows_updated": 0,
        "mode": "field_restore",
        "restored_fields": sorted(str(k) for k in _dict(restore_fields).keys()),
        "ownership_basis": "",
    }

    if _text(plan.get("adapter")) not in {"", "db_sql"}:
        receipt["reason_code"] = "CLEANUP_ADAPTER_NOT_SUPPORTED"
        return receipt

    policy_allowed, policy_reason_code = _adapter_write_policy_allowed(
        root=root,
        project=project,
        runtime_contract=runtime_contract,
        policy_decision=policy_decision,
    )
    if not policy_allowed:
        receipt["reason_code"] = policy_reason_code
        return receipt

    table = _text(plan.get("table"))
    column = _text(plan.get("identity_column")) or "id"
    if not _IDENTIFIER_RE.match(table) or not _IDENTIFIER_RE.match(column):
        receipt["reason_code"] = "CLEANUP_IDENTIFIER_NOT_SAFE"
        return receipt
    if not _text(identity_value):
        receipt["reason_code"] = REASON_NO_IDENTITY
        return receipt

    # Mutation attestation is the ownership/scope proof for field restore —
    # analogous to row_was_created_by_this_run for DELETE. Never UPDATE without it.
    attested, basis = mutation_attested_for_identity(
        identity_value=identity_value,
        identity_column=column,
        mutation_attestation={
            **_dict(mutation_attestation),
            "restore_fields": _dict(
                _dict(mutation_attestation).get("restore_fields") or restore_fields
            ),
        },
    )
    receipt["ownership_basis"] = basis
    if not attested:
        receipt["reason_code"] = REASON_MUTATION_NOT_ATTESTED
        receipt["detail"] = basis
        return receipt

    safe_fields: dict[str, Any] = {}
    for key, value in _dict(restore_fields).items():
        field = _text(key)
        if not field or not _IDENTIFIER_RE.match(field):
            receipt["reason_code"] = "CLEANUP_IDENTIFIER_NOT_SAFE"
            receipt["detail"] = f"unsafe_restore_field:{field or '?'}"
            return receipt
        if isinstance(value, (dict, list)):
            continue
        safe_fields[field] = value
    if not safe_fields:
        receipt["reason_code"] = DB_RESTORE_FIELD_SET_EMPTY
        return receipt
    receipt["restored_fields"] = sorted(safe_fields.keys())

    opener = connect
    if opener is None:
        if not _text(dsn):
            receipt["reason_code"] = "CLEANUP_DB_CONNECTION_NOT_CONFIGURED"
            return receipt

        def opener():  # type: ignore[misc]
            import psycopg2

            return psycopg2.connect(_text(dsn))

    try:
        connection = opener()
    except Exception as exc:
        receipt["status"] = "FAILED"
        receipt["reason_code"] = f"CLEANUP_DB_CONNECT_FAILED:{type(exc).__name__}"
        receipt["detail"] = str(exc)[:200]
        return receipt

    if _list(entities):
        resolved_table, table_reason = resolve_storage_table_against_catalog(
            table,
            entities=entities,
            connection=connection,
        )
        if table_reason:
            try:
                connection.close()
            except Exception:
                pass
            receipt["reason_code"] = table_reason
            receipt["detail"] = f"declared_table={table}"
            return receipt
        if resolved_table:
            table = resolved_table
            receipt["table"] = table

    assignments = ", ".join(f'"{field}" = %s' for field in safe_fields)
    values = list(safe_fields.values()) + [_text(identity_value)]
    try:
        cursor = connection.cursor()
        cursor.execute(
            f'UPDATE "{table}" SET {assignments} WHERE "{column}" = %s',  # noqa: S608
            values,
        )
        updated = int(getattr(cursor, "rowcount", 0) or 0)
        receipt["rows_updated"] = updated
        # Identity must address exactly one row. Multi-row updates are a scope
        # failure — roll back rather than commit ambiguous customer mutations.
        if updated != 1:
            connection.rollback()
        if updated > 1:
            receipt["status"] = "FAILED"
            receipt["reason_code"] = "CLEANUP_DB_RESTORE_CARDINALITY_MISMATCH"
            receipt["detail"] = f"identity_matched_rows={updated}"
            return receipt
        if updated == 0:
            # Row already absent or fields already at target values — the
            # cleanup objective is satisfied.
            receipt["status"] = "CLEANED"
            receipt["reason_code"] = "CLEANUP_ROW_ALREADY_ABSENT"
            receipt["rows_updated"] = 0
            return receipt
        connection.commit()
        receipt["status"] = "CLEANED"
    except Exception as exc:
        try:
            connection.rollback()
        except Exception as rollback_exc:
            receipt["status"] = "FAILED"
            receipt["reason_code"] = (
                f"CLEANUP_DB_ROLLBACK_FAILED:{type(rollback_exc).__name__}"
            )
            receipt["detail"] = (
                f"restore_error={type(exc).__name__}:{exc};"
                f"rollback_error={type(rollback_exc).__name__}:{rollback_exc}"
            )[:400]
        else:
            receipt["status"] = "FAILED"
            receipt["reason_code"] = f"CLEANUP_DB_RESTORE_FAILED:{type(exc).__name__}"
            receipt["detail"] = str(exc)[:200]
    finally:
        try:
            connection.close()
        except Exception as close_exc:
            prior_reason = _text(receipt.get("reason_code"))
            receipt["status"] = "FAILED"
            receipt["reason_code"] = (
                f"CLEANUP_DB_CLOSE_FAILED:{type(close_exc).__name__}"
            )
            receipt["detail"] = (
                f"prior_reason={prior_reason or 'none'};"
                f"close_error={type(close_exc).__name__}:{close_exc}"
            )[:400]
    return receipt


def execute_declared_adapter_cleanup(
    step: dict[str, Any],
    *,
    identity_value: Any,
    dsn: str = "",
    creation_receipts: Any = None,
    connect: Any = None,
    root: Any = None,
    project: str = "",
    runtime_contract: dict[str, Any] | None = None,
    policy_decision: dict[str, Any] | None = None,
    entities: Any = None,
) -> dict[str, Any]:
    """Run one data-layer cleanup step, refusing anything this run did not create.

    Returns a receipt in every case, including refusal: a cleanup that did not happen
    must be as visible as one that did, or residue accumulates silently in a customer
    system.

    ``connect`` is injectable so the guard logic is testable without a database. It must
    return a DB-API connection.

    Before any connection is opened, the write must also clear the same non-production
    target-policy gate a governed HTTP write clears. Supply either ``policy_decision``
    (a precomputed/injected ``TargetPolicyDecision``) or ``root``/``project`` (plus
    optionally ``runtime_contract``) so the gate can compute one. Missing both fails
    closed -- an unevaluated target is never assumed safe.

    When ``entities`` carries source table aliases, the declared logical table is
    rebound to the single catalog-present alias before DELETE.
    """
    plan = _dict(step)
    receipt: dict[str, Any] = {
        "schema_version": EXECUTION_SCHEMA,
        "receipt_id": f"cleanup_adapter_{uuid.uuid4().hex}",
        "adapter": _text(plan.get("adapter")),
        "table": _text(plan.get("table")),
        "identity_column": _text(plan.get("identity_column")),
        "identity_value": _text(identity_value),
        "status": "REFUSED",
        "reason_code": "",
        "rows_deleted": 0,
        "mode": "row_delete",
    }

    if _text(plan.get("adapter")) != "db_sql":
        receipt["reason_code"] = "CLEANUP_ADAPTER_NOT_SUPPORTED"
        return receipt

    policy_allowed, policy_reason_code = _adapter_write_policy_allowed(
        root=root,
        project=project,
        runtime_contract=runtime_contract,
        policy_decision=policy_decision,
    )
    if not policy_allowed:
        receipt["reason_code"] = policy_reason_code
        return receipt

    table = _text(plan.get("table"))
    declared_table = table
    column = _text(plan.get("identity_column"))
    if not _IDENTIFIER_RE.match(table) or not _IDENTIFIER_RE.match(column):
        # Identifiers cannot be parameterised, so they are whitelisted by shape rather
        # than quoted. A name that is not a bare identifier does not reach SQL.
        receipt["reason_code"] = "CLEANUP_IDENTIFIER_NOT_SAFE"
        return receipt
    if not _text(identity_value):
        receipt["reason_code"] = REASON_NO_IDENTITY
        return receipt

    # Ownership before any connection. Accept receipts stamped with any
    # source-declared alias of the logical table (payment vs payments).
    if plan.get("requires_ownership_proof") is not False:
        owned = False
        basis = "no_ownership_evidence"
        for candidate in storage_table_candidates(table, entities=entities) or [table]:
            owned, basis = row_was_created_by_this_run(
                identity_value,
                creation_receipts=creation_receipts,
                table=candidate,
            )
            if owned:
                break
        if not owned and _text(plan.get("owner_table")):
            # Transitive run-ownership for dependent rows. A dependent row
            # whose FK references the run-created owner row (verified by the
            # owner-table ownership check below) cannot predate the owner —
            # FK integrity forbids a row referencing a value that did not
            # exist. The dependent is therefore run-created too. Without this,
            # dependents of run-created rows whose identity carries no marker
            # (UUID ids) are refused, survive the dependent delete, and trip
            # ForeignKeyViolation on the owner row.
            owner_owned, owner_basis = row_was_created_by_this_run(
                identity_value,
                creation_receipts=creation_receipts,
                table=_text(plan.get("owner_table")),
            )
            if owner_owned:
                owned = True
                basis = f"transitive_owner_creation:{owner_basis}"
        receipt["ownership_basis"] = basis
        if not owned:
            receipt["reason_code"] = REASON_NOT_RUN_OWNED
            return receipt

    opener = connect
    if opener is None:
        if not _text(dsn):
            receipt["reason_code"] = "CLEANUP_DB_CONNECTION_NOT_CONFIGURED"
            return receipt

        def opener():  # type: ignore[misc]
            import psycopg2

            return psycopg2.connect(_text(dsn))

    try:
        connection = opener()
    except Exception as exc:
        receipt["status"] = "FAILED"
        receipt["reason_code"] = f"CLEANUP_DB_CONNECT_FAILED:{type(exc).__name__}"
        receipt["detail"] = str(exc)[:200]
        return receipt

    # Logical entity names (payment) often diverge from schema tables (payments).
    # Rebind using source entity aliases + information_schema — never invent names.
    if _list(entities):
        resolved_table, table_reason = resolve_storage_table_against_catalog(
            declared_table,
            entities=entities,
            connection=connection,
        )
        if table_reason:
            try:
                connection.close()
            except Exception:
                pass
            receipt["reason_code"] = table_reason
            receipt["detail"] = f"declared_table={declared_table}"
            return receipt
        if resolved_table:
            table = resolved_table
            receipt["table"] = table

    try:
        cursor = connection.cursor()
        cursor.execute(
            f'DELETE FROM "{table}" WHERE "{column}" = %s',  # noqa: S608 - identifiers whitelisted above
            (_text(identity_value),),
        )
        deleted = int(getattr(cursor, "rowcount", 0) or 0)
        receipt["rows_deleted"] = deleted
        is_dependent_step = (
            _text(plan.get("delete_order")) == "dependent"
        )
        if deleted == 0:
            # Row already absent means the cleanup objective is satisfied —
            # the environment is clean. This happens when two cleanup steps
            # target the same row (control + treatment) or a prior run already
            # removed it. Treat as success, not refusal.
            receipt["status"] = "CLEANED"
            receipt["reason_code"] = "CLEANUP_ROW_ALREADY_ABSENT"
            receipt["rows_deleted"] = 0
            return receipt
        if deleted > 1 and not is_dependent_step:
            # The owner row must be addressed exactly once. Multi-row matches
            # on the owner identity are a scope failure — roll back rather
            # than commit an ambiguous deletion.
            connection.rollback()
            receipt["status"] = "FAILED"
            receipt["reason_code"] = "CLEANUP_DB_DELETE_CARDINALITY_MISMATCH"
            receipt["detail"] = f"identity_matched_rows={deleted}"
            return receipt
        connection.commit()
        receipt["status"] = "CLEANED"
    except Exception as exc:
        try:
            connection.rollback()
        except Exception as rollback_exc:
            receipt["status"] = "FAILED"
            receipt["reason_code"] = (
                f"CLEANUP_DB_ROLLBACK_FAILED:{type(rollback_exc).__name__}"
            )
            receipt["detail"] = (
                f"delete_error={type(exc).__name__}:{exc};"
                f"rollback_error={type(rollback_exc).__name__}:{rollback_exc}"
            )[:400]
        else:
            receipt["status"] = "FAILED"
            receipt["reason_code"] = f"CLEANUP_DB_DELETE_FAILED:{type(exc).__name__}"
            receipt["detail"] = str(exc)[:200]
    finally:
        try:
            connection.close()
        except Exception as close_exc:
            prior_reason = _text(receipt.get("reason_code"))
            receipt["status"] = "FAILED"
            receipt["reason_code"] = (
                f"CLEANUP_DB_CLOSE_FAILED:{type(close_exc).__name__}"
            )
            receipt["detail"] = (
                f"prior_reason={prior_reason or 'none'};"
                f"close_error={type(close_exc).__name__}:{close_exc}"
            )[:400]
    return receipt


def dependent_tables_for(
    table: str,
    identity_column: str,
    entities: Any,
) -> list[str]:
    """Tables that reference *table* through the same identity column.

    Derived from the source-declared schema: an entity whose declared fields include the
    identity column of another table references it. Running this against the live target,
    products.sku is referenced by inventory, cart_items, inventory_locks and order_items,
    which is why deleting a product directly raised ForeignKeyViolation on every attempt
    while cart_items deleted cleanly.

    Returns dependents only -- never *table* itself -- so a caller deletes these first and
    the owning row last.
    """
    target = _text(table).lower()
    column = _text(identity_column).lower()
    if not target or not column:
        return []
    singular = target[:-1] if target.endswith("s") and not target.endswith("ss") else target
    # The two ways a dependent names its reference: the owner's own column verbatim
    # (products.sku -> inventory.sku) or the conventional foreign key
    # (orders.id -> order_items.order_id). "id" alone is too common to be evidence, so
    # it is only honoured in the qualified form.
    reference_names = {f"{singular}_{column}", f"{singular}{column}"}
    if column != "id":
        reference_names.add(column)

    out: list[str] = []
    for node in _list(entities):
        row = _dict(node)
        name = _text(row.get("name"))
        if not name or name.lower() == target:
            continue
        fields = {_text(f).lower() for f in _list(row.get("fields"))}
        if fields & reference_names:
            out.append(name)
    return sorted(out)


_SCHEMA_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(\w+)\s*\((.*?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)
_SCHEMA_FK_RE = re.compile(
    r"(\w+)\s+[^,()]*?REFERENCES\s+(\w+)(?:\s*\(\s*(\w+)\s*\))?",
    re.IGNORECASE,
)


def schema_fk_dependents(
    declared_schema: str,
    table: str,
) -> list[dict[str, str]]:
    """Dependents of *table* declared in the target's own schema, with FK columns.

    Parses ``CREATE TABLE ... REFERENCES <table>(<column>)`` from the declared
    schema text (visible source material the product already consumes). The FK
    column is authoritative for the dependent delete: orders.id is referenced by
    order_items.order_id / payments.order_id / refunds.order_id, so those rows
    must be removed ``WHERE order_id = <value>``, never by the owner's id column.
    The IR entity-graph heuristic cannot see tables the source docs do not model
    as entities, and cannot know the FK column when it differs from the owner's
    identity column — both gaps surface as ForeignKeyViolation on the owner row.

    Returns only tables that reference *table* — never *table* itself.
    """
    target = _text(table).lower()
    if not target or not _text(declared_schema):
        return []
    dependents: dict[str, str] = {}
    for table_match in _SCHEMA_TABLE_RE.finditer(declared_schema):
        dependent = _text(table_match.group(1)).lower()
        if not dependent or dependent == target:
            continue
        body = table_match.group(2)
        for fk_match in _SCHEMA_FK_RE.finditer(body):
            fk_column = _text(fk_match.group(1))
            referenced = _text(fk_match.group(2)).lower()
            if referenced == target and fk_column:
                dependents.setdefault(dependent, fk_column)
    return [
        {"table": name, "fk_column": column}
        for name, column in sorted(dependents.items())
    ]


def build_ordered_delete_plan(
    *,
    table: str,
    identity_column: str,
    identity_value: Any,
    entities: Any,
    declared_schema: str = "",
) -> list[dict[str, Any]]:
    """Delete steps for one row, dependents first, owner last.

    Every step carries the same ownership requirement as a single-table delete: the
    executor re-checks the identity and refuses a row this run did not create.

    Dependents come from two sources: the IR entity graph (legacy heuristic) and the
    target's declared schema ``REFERENCES`` clauses. Schema-derived dependents carry
    their real FK column (``order_items.order_id`` for an orders owner), so the
    dependent delete addresses the referencing rows instead of deleting zero rows by
    the owner's id column and then tripping ForeignKeyViolation on the owner. Each
    schema dependent also records the owner table so the executor can prove the
    dependent rows are run-owned through the run-created owner row (FK integrity).
    """
    steps: list[dict[str, Any]] = []
    schema_dependents = schema_fk_dependents(declared_schema, table)
    schema_tables = {
        _text(row.get("table")).lower(): row for row in schema_dependents
    }
    for dependent in dependent_tables_for(table, identity_column, entities):
        name = _text(dependent).lower()
        schema_row = schema_tables.get(name)
        fk_column = (
            _text(schema_row.get("fk_column"))
            if schema_row and _text(schema_row.get("fk_column"))
            else _text(identity_column)
        )
        steps.append({
            "adapter": "db_sql",
            "mode": "row_delete",
            "table": _text(dependent),
            "identity_column": fk_column,
            "identity_value": _text(identity_value),
            "scope": "run_created_only",
            "requires_ownership_proof": True,
            "delete_order": "dependent",
            "owner_table": _text(table),
            "dependent_fk_column": fk_column,
        })
    for schema_row in schema_dependents:
        name = _text(schema_row.get("table")).lower()
        if name in {
            _text(step.get("table")).lower() for step in steps
        }:
            continue
        fk_column = _text(schema_row.get("fk_column")) or _text(identity_column)
        steps.append({
            "adapter": "db_sql",
            "mode": "row_delete",
            "table": _text(schema_row.get("table")),
            "identity_column": fk_column,
            "identity_value": _text(identity_value),
            "scope": "run_created_only",
            "requires_ownership_proof": True,
            "delete_order": "dependent",
            "owner_table": _text(table),
            "dependent_fk_column": fk_column,
        })
    steps.append({
        "adapter": "db_sql",
        "mode": "row_delete",
        "table": _text(table),
        "identity_column": _text(identity_column),
        "identity_value": _text(identity_value),
        "scope": "run_created_only",
        "requires_ownership_proof": True,
        "delete_order": "owner",
    })
    return steps


# ═══════════════════════════════════════════════════════════════════════════════
# V1.3.0-A: Database Cleanup Contract, Dependency Graph, Authority, Pre-image
# ═══════════════════════════════════════════════════════════════════════════════

import hashlib as _hashlib
import json as _json
import re as _re

CONTRACT_SCHEMA = "qualibug.database-cleanup-contract.v1"

# Contract status constants
CONTRACT_RESOLVED = "RESOLVED"
CONTRACT_INCOMPLETE = "INCOMPLETE"
CONTRACT_AMBIGUOUS = "AMBIGUOUS"
CONTRACT_UNSAFE = "UNSAFE"
CONTRACT_NOT_DECLARED = "NOT_DECLARED"

# Cleanup strategy types (SPEC §3 — the ONLY legal strategies)
STRATEGY_API_COMPENSATION = "SOURCE_DECLARED_API_COMPENSATION"
STRATEGY_DB_DELETE = "SOURCE_DECLARED_DB_DELETE"
STRATEGY_DB_RESTORE = "SOURCE_DECLARED_DB_RESTORE"
STRATEGY_TRANSACTION_ROLLBACK = "TRANSACTION_ROLLBACK"
STRATEGY_SNAPSHOT_RESTORE = "SNAPSHOT_RESTORE"
STRATEGY_ENVIRONMENT_RESET = "ENVIRONMENT_RESET_CONTRACT"
STRATEGY_DISPOSABLE_FIXTURE_DELETE = "CAMPAIGN_OWNED_DISPOSABLE_FIXTURE_DELETE"

_LEGAL_STRATEGIES = frozenset({
    STRATEGY_API_COMPENSATION, STRATEGY_DB_DELETE, STRATEGY_DB_RESTORE,
    STRATEGY_TRANSACTION_ROLLBACK, STRATEGY_SNAPSHOT_RESTORE,
    STRATEGY_ENVIRONMENT_RESET, STRATEGY_DISPOSABLE_FIXTURE_DELETE,
})

# Mutation types
MUTATION_CREATE = "CREATE"
MUTATION_UPDATE = "UPDATE"
MUTATION_DELETE = "DELETE"
MUTATION_MULTI_TABLE = "MULTI_TABLE"

# Breakpoint codes (SPEC §17)
DB_CLEANUP_AUTHORITY_NOT_DECLARED = "DB_CLEANUP_AUTHORITY_NOT_DECLARED"
DB_ROW_IDENTITY_NOT_BOUND = "DB_ROW_IDENTITY_NOT_BOUND"
DB_SCOPE_NOT_BOUND = "DB_SCOPE_NOT_BOUND"
DB_DEPENDENCY_GRAPH_INCOMPLETE = "DB_DEPENDENCY_GRAPH_INCOMPLETE"
DB_PREIMAGE_NOT_CAPTURED = "DB_PREIMAGE_NOT_CAPTURED"
DB_RESTORE_FIELD_SET_EMPTY = "DB_RESTORE_FIELD_SET_EMPTY"
DB_RESTORE_CONCURRENT_CHANGE_DETECTED = "DB_RESTORE_CONCURRENT_CHANGE_DETECTED"
DB_DELETE_NOT_REVERSIBLE = "DB_DELETE_NOT_REVERSIBLE"
DB_CLEANUP_PARTIAL = "DB_CLEANUP_PARTIAL"
DB_CLEANUP_FAILED = "DB_CLEANUP_FAILED"
DB_CLEANUP_VERIFICATION_FAILED = "DB_CLEANUP_VERIFICATION_FAILED"
DB_ENVIRONMENT_DIRTY = "DB_ENVIRONMENT_DIRTY"
CROSS_DATASTORE_CLEANUP_INCOMPLETE = "CROSS_DATASTORE_CLEANUP_INCOMPLETE"
ENVIRONMENT_RESTORATION_NOT_PROVEN = "ENVIRONMENT_RESTORATION_NOT_PROVEN"
PARENT_BREAKPOINT = "DB_CLEANUP_AND_ENVIRONMENT_RESTORATION_NOT_CLOSED"

ALL_DB_BREAKPOINT_CODES = frozenset({
    DB_CLEANUP_AUTHORITY_NOT_DECLARED, DB_ROW_IDENTITY_NOT_BOUND,
    DB_SCOPE_NOT_BOUND, DB_DEPENDENCY_GRAPH_INCOMPLETE,
    DB_PREIMAGE_NOT_CAPTURED, DB_RESTORE_FIELD_SET_EMPTY,
    DB_RESTORE_CONCURRENT_CHANGE_DETECTED, DB_DELETE_NOT_REVERSIBLE,
    DB_CLEANUP_PARTIAL, DB_CLEANUP_FAILED,
    DB_CLEANUP_VERIFICATION_FAILED, DB_ENVIRONMENT_DIRTY,
    CROSS_DATASTORE_CLEANUP_INCOMPLETE, ENVIRONMENT_RESTORATION_NOT_PROVEN,
})

# Prohibited pattern detection
_PROHIBITED_SQL_RE = _re.compile(
    r"(TRUNCATE|DROP\s+TABLE|SET\s+FOREIGN_KEY_CHECKS\s*=\s*0"
    r"|DELETE\s+FROM\s+\w+\s*(?:;|$)"
    r"|MAX\s*\(\s*id\s*\)"
    r"|ORDER\s+BY\s+(?:created_at|id)\s+DESC\s+LIMIT\s+1)",
    _re.I,
)


def _contract_id(*parts: Any) -> str:
    raw = "|".join(str(p) for p in parts)
    return "dbc_" + _hashlib.sha256(raw.encode()).hexdigest()[:24]


def _infer_mutation_type(method: str, path: str) -> str:
    """Infer mutation type from HTTP method and path semantics."""
    m = _text(method).upper()
    if m == "POST":
        return MUTATION_CREATE
    if m in ("PUT", "PATCH"):
        return MUTATION_UPDATE
    if m == "DELETE":
        return MUTATION_DELETE
    return MUTATION_CREATE


def _entity_for_operation(
    operation: dict[str, Any],
    entities: list[dict[str, Any]],
) -> dict[str, Any]:
    """Find the primary entity an operation acts on, from path segment matching."""
    path = _text(operation.get("path") or operation.get("raw_path"))
    if not path:
        return {}
    segments = [s for s in path.split("/") if s and not s.startswith("{")]
    if not segments:
        return {}
    # Last non-placeholder segment is typically the entity collection
    target_seg = segments[-1].lower()
    for entity in entities:
        name = _text(entity.get("name")).lower()
        if not name:
            continue
        if name == target_seg or name.rstrip("s") == target_seg.rstrip("s"):
            return entity
    # Fallback: first segment match
    for seg in reversed(segments):
        seg_l = seg.lower()
        for entity in entities:
            name = _text(entity.get("name")).lower()
            if name and (name == seg_l or name.rstrip("s") == seg_l.rstrip("s")):
                return entity
    return {}


# ─── Database Dependency Graph ────────────────────────────────────────────────

def build_database_dependency_graph(
    entities: Any,
    *,
    schema_text: str = "",
) -> dict[str, Any]:
    """Build a dependency graph from source-declared schema and entity metadata.

    Priority:
    1. Explicit REFERENCES in DDL schema_text (confidence 1.0)
    2. foreign_keys declared on entity dicts (confidence 0.95)
    3. Naming convention <singular>_id (confidence 0.6, marked inferred)

    Returns {nodes, edges, topological_order, incomplete, reason_codes}.
    """
    entity_list = _list(entities)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    node_names: set[str] = set()

    for entity in entity_list:
        name = _text(entity.get("name"))
        if not name:
            continue
        node_names.add(name.lower())
        nodes.append({
            "table": name,
            "identity_fields": _list(entity.get("identity_fields")),
            "fields": _list(entity.get("fields")),
        })

    # Priority 1: DDL REFERENCES
    if schema_text:
        for match in _re.finditer(
            r"(?is)CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`]?([A-Za-z_][A-Za-z0-9_]*)[\"`]?\s*\((.*?)\);",
            schema_text,
        ):
            child_table = match.group(1)
            body = match.group(2)
            for parent in _re.findall(r"(?i)REFERENCES\s+[\"`]?([A-Za-z_][A-Za-z0-9_]*)", body):
                edges.append({
                    "child_table": child_table,
                    "parent_table": parent,
                    "source": "ddl_references",
                    "confidence": 1.0,
                })

    # Priority 2: declared foreign_keys on entity dicts
    for entity in entity_list:
        name = _text(entity.get("name"))
        if not name:
            continue
        for fk in _list(entity.get("foreign_keys")):
            target = _text(fk) if isinstance(fk, str) else _text(_dict(fk).get("ref_table") or _dict(fk).get("to"))
            if target and target.lower() != name.lower():
                # Avoid duplicates
                exists = any(
                    e["child_table"].lower() == name.lower() and e["parent_table"].lower() == target.lower()
                    for e in edges
                )
                if not exists:
                    edges.append({
                        "child_table": name,
                        "parent_table": target,
                        "source": "declared_foreign_key",
                        "confidence": 0.95,
                    })

    # Priority 3: naming convention (<singular>_id pattern)
    _ID_COL_RE = _re.compile(r"^([a-z][a-z0-9]*?)_id$")
    for entity in entity_list:
        child_name = _text(entity.get("name"))
        if not child_name:
            continue
        for field in _list(entity.get("fields")):
            fname = _text(field).lower() if isinstance(field, str) else _text(_dict(field).get("name")).lower()
            m = _ID_COL_RE.match(fname)
            if not m:
                continue
            ref_base = m.group(1)
            # Find parent table
            for candidate in (ref_base, ref_base + "s", ref_base + "es"):
                if candidate in node_names and candidate != child_name.lower():
                    exists = any(
                        e["child_table"].lower() == child_name.lower()
                        and e["parent_table"].lower() == candidate
                        for e in edges
                    )
                    if not exists:
                        edges.append({
                            "child_table": child_name,
                            "parent_table": candidate,
                            "source": "naming_convention",
                            "confidence": 0.6,
                        })
                    break

    # Topological order (children before parents for cleanup)
    topo = _topological_sort(nodes, edges)
    incomplete = len(topo) < len(nodes)

    return {
        "schema_version": "qualibug.database-dependency-graph.v1",
        "nodes": nodes,
        "edges": edges,
        "topological_order": topo,
        "incomplete": incomplete,
        "reason_codes": [DB_DEPENDENCY_GRAPH_INCOMPLETE] if incomplete else [],
        "node_count": len(nodes),
        "edge_count": len(edges),
    }


def _topological_sort(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> list[str]:
    """Kahn's algorithm: returns tables in cleanup order (children first)."""
    all_tables = [_text(n.get("table")).lower() for n in nodes]
    table_display = {_text(n.get("table")).lower(): _text(n.get("table")) for n in nodes}
    # Build adjacency: parent -> children (parent must be cleaned AFTER children)
    in_degree: dict[str, int] = {t: 0 for t in all_tables}
    children_of: dict[str, list[str]] = {t: [] for t in all_tables}
    for edge in edges:
        child = _text(edge.get("child_table")).lower()
        parent = _text(edge.get("parent_table")).lower()
        if child in in_degree and parent in in_degree and child != parent:
            children_of[parent].append(child)
            in_degree[child] = in_degree.get(child, 0) + 1

    # Start from leaves (no incoming edges = no parent references them)
    # For cleanup: children first. Reverse the edge direction:
    # child depends on parent, so parent has higher topo order.
    # We want children FIRST, so sort by reverse dependency.
    queue = sorted([t for t in all_tables if in_degree.get(t, 0) == 0])
    order: list[str] = []
    visited: set[str] = set()
    while queue:
        node = queue.pop(0)
        if node in visited:
            continue
        visited.add(node)
        order.append(table_display.get(node, node))
        for child in sorted(children_of.get(node, [])):
            in_degree[child] -= 1
            if in_degree[child] <= 0 and child not in visited:
                queue.append(child)
    # Add remaining (cycles or disconnected)
    for t in all_tables:
        if t not in visited:
            order.append(table_display.get(t, t))
    # Reverse: children (dependents) first, parents last
    order.reverse()
    return order


# ─── Cleanup Authority Resolution ─────────────────────────────────────────────

def resolve_cleanup_authority(
    write_operation: dict[str, Any],
    *,
    cleanup_plan: Any,
    behavior_ir: Any = None,
    available_adapters: Any = None,
) -> dict[str, Any]:
    """Resolve the cleanup authority for a write operation.

    Returns {strategy_type, authority_source, status, reason_code}.
    Only legal strategies from SPEC §3 are accepted.
    """
    plan_list = _list(cleanup_plan)
    adapters = {_text(a) for a in _list(available_adapters)} or {"http_api"}
    method = _text(write_operation.get("method")).upper()

    # Check declared cleanup plan for authority
    if plan_list:
        first = _dict(plan_list[0])
        action = _text(first.get("action"))
        adapter = _text(first.get("adapter"))

        if action == "source_declared_compensation":
            return {
                "strategy_type": STRATEGY_API_COMPENSATION,
                "authority_source": _text(first.get("operation_ref")),
                "status": CONTRACT_RESOLVED,
                "reason_code": "",
            }
        if action == "restore_before_snapshot":
            return {
                "strategy_type": STRATEGY_SNAPSHOT_RESTORE,
                "authority_source": _text(first.get("operation_ref")),
                "status": CONTRACT_RESOLVED,
                "reason_code": "",
            }
        if action == "inverse_delta_compensation":
            return {
                "strategy_type": STRATEGY_DB_RESTORE,
                "authority_source": _text(first.get("operation_ref")),
                "status": CONTRACT_RESOLVED,
                "reason_code": "",
            }
        if action == "reverse_order_compensation":
            return {
                "strategy_type": STRATEGY_API_COMPENSATION,
                "authority_source": _text(first.get("operation_ref")),
                "status": CONTRACT_RESOLVED,
                "reason_code": "",
            }
        if adapter == "db_sql" or action == "declared_adapter_cleanup":
            return {
                "strategy_type": STRATEGY_DB_DELETE,
                "authority_source": _text(first.get("table")) or _text(first.get("operation_ref")),
                "status": CONTRACT_RESOLVED,
                "reason_code": "",
            }
    # No plan: check if adapters can provide authority
    if "db_sql" in adapters:
        return {
            "strategy_type": STRATEGY_DB_DELETE,
            "authority_source": "declared_adapter:db_sql",
            "status": CONTRACT_INCOMPLETE,
            "reason_code": DB_ROW_IDENTITY_NOT_BOUND,
        }

    # No authority at all
    return {
        "strategy_type": "",
        "authority_source": "",
        "status": CONTRACT_NOT_DECLARED,
        "reason_code": DB_CLEANUP_AUTHORITY_NOT_DECLARED,
    }


# ─── Pre-image Plan ───────────────────────────────────────────────────────────

def resolve_preimage_plan(
    write_operation: dict[str, Any],
    *,
    entities: Any = None,
    behavior_ir: Any = None,
) -> dict[str, Any]:
    """Determine whether a pre-image must be captured before the write.

    PUT/PATCH/DELETE → required (modifying or removing existing data).
    POST create → not required (use Fixture Row Lineage instead).
    """
    method = _text(write_operation.get("method")).upper()
    entity = _entity_for_operation(write_operation, _list(entities))
    fields = _list(entity.get("fields"))
    identity_fields = _list(entity.get("identity_fields"))

    if method in ("PUT", "PATCH"):
        return {
            "required": True,
            "fields": [f for f in fields if isinstance(f, str)][:50],
            "observer_id": "before_state",
            "capture_timing": "before_write",
            "reason": "modification_requires_preimage",
        }
    if method == "DELETE":
        return {
            "required": True,
            "fields": [f for f in fields if isinstance(f, str)][:50],
            "observer_id": "before_state",
            "capture_timing": "before_write",
            "reason": "deletion_requires_full_snapshot",
        }
    # POST create: no pre-image needed
    return {
        "required": False,
        "fields": [],
        "observer_id": "",
        "capture_timing": "",
        "reason": "create_uses_row_lineage",
    }


# ─── Compile-Time Database Cleanup Contract ───────────────────────────────────

def build_database_cleanup_contract(
    *,
    experiment_id: str,
    campaign_id: str,
    write_operation: dict[str, Any],
    behavior_ir: Any = None,
    entities: Any = None,
    cleanup_plan: Any = None,
    environment_type: str = "",
    available_adapters: Any = None,
) -> dict[str, Any]:
    """Generate the compile-time Database Cleanup Contract (SPEC §5).

    Only status=RESOLVED allows the write experiment to proceed.
    """
    ir = _dict(behavior_ir)
    entity_list = _list(entities) or _list(ir.get("entities"))
    method = _text(write_operation.get("method")).upper()
    path = _text(write_operation.get("path") or write_operation.get("raw_path"))
    op_id = _text(write_operation.get("id") or write_operation.get("operation_id"))

    # Identify target entity
    target_entity = _entity_for_operation(write_operation, entity_list)
    table_name = entity_storage_table(target_entity)
    identity_fields = _list(target_entity.get("identity_fields"))
    if not identity_fields:
        identity_fields = ["id"]

    # Mutation type
    mutation_type = _infer_mutation_type(method, path)

    # Dependency graph
    dep_graph = build_database_dependency_graph(entity_list)
    dependency_order = _list(dep_graph.get("topological_order"))

    # Authority resolution
    authority = resolve_cleanup_authority(
        write_operation,
        cleanup_plan=cleanup_plan,
        behavior_ir=behavior_ir,
        available_adapters=available_adapters,
    )

    # Pre-image plan
    preimage = resolve_preimage_plan(
        write_operation,
        entities=entity_list,
        behavior_ir=behavior_ir,
    )

    # Determine overall contract status
    status = _text(authority.get("status"))
    reason_codes: list[str] = []
    if _text(authority.get("reason_code")):
        reason_codes.append(_text(authority.get("reason_code")))
    if dep_graph.get("incomplete"):
        reason_codes.append(DB_DEPENDENCY_GRAPH_INCOMPLETE)
        if status == CONTRACT_RESOLVED:
            status = CONTRACT_INCOMPLETE
    if not table_name:
        reason_codes.append(DB_ROW_IDENTITY_NOT_BOUND)
        if status == CONTRACT_RESOLVED:
            status = CONTRACT_INCOMPLETE
    if preimage.get("required") and not preimage.get("fields"):
        reason_codes.append(DB_RESTORE_FIELD_SET_EMPTY)

    # Affected rows projection
    affected_rows = [{
        "table": table_name,
        "primary_key_binding": identity_fields[0] if identity_fields else "id",
        "foreign_key_bindings": [
            e["child_table"] for e in _list(dep_graph.get("edges"))
            if _text(e.get("parent_table")).lower() == table_name.lower()
        ],
        "correlation_keys": [],
    }]

    # Cleanup strategy
    strategy_type = _text(authority.get("strategy_type"))
    cleanup_strategy = {
        "strategy_type": strategy_type,
        "authority_source": _text(authority.get("authority_source")),
        "operation_or_sql_template_ref": op_id,
    }

    # Verification plan
    verification_plan = {
        "observers": ["after_cleanup_state"],
        "expected_final_state": "pre_experiment_baseline",
        "scope": "affected_entities_only",
    }

    contract = {
        "schema_version": CONTRACT_SCHEMA,
        "contract_id": _contract_id(experiment_id, campaign_id, op_id, table_name),
        "experiment_id": experiment_id,
        "campaign_id": campaign_id,
        "datastore_id": _text(ir.get("datastore_id")) or "primary",
        "database_type": _text(ir.get("database_type")) or "postgresql",
        "environment_id": environment_type or "non_production",
        "target_entities": [{
            "canonical_entity_id": _text(target_entity.get("id")) or table_name,
            "table": table_name,
            "identity_columns": identity_fields,
            "scope_columns": [],
        }] if table_name else [],
        "mutation_type": mutation_type,
        "owned_by_campaign": True,
        "pre_existing_customer_data": False,
        "affected_rows": affected_rows,
        "preimage_plan": preimage,
        "cleanup_strategy": cleanup_strategy,
        "dependency_order": dependency_order,
        "verification_plan": verification_plan,
        "status": status,
        "reason_codes": sorted(set(reason_codes)),
        "dependency_graph_id": _text(dep_graph.get("schema_version")),
        "dependency_graph_incomplete": bool(dep_graph.get("incomplete")),
    }
    return contract
