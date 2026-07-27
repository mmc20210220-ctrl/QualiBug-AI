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
REASON_NOT_APPROVED = "CLEANUP_TARGET_NOT_APPROVED_FOR_WRITE"

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
    table = _text(entity_row.get("name"))
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

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

EXECUTION_SCHEMA = "qualibug.cleanup-adapter-execution.v1"


def execute_declared_adapter_cleanup(
    step: dict[str, Any],
    *,
    identity_value: Any,
    dsn: str = "",
    creation_receipts: Any = None,
    connect: Any = None,
) -> dict[str, Any]:
    """Run one data-layer cleanup step, refusing anything this run did not create.

    Returns a receipt in every case, including refusal: a cleanup that did not happen
    must be as visible as one that did, or residue accumulates silently in a customer
    system.

    ``connect`` is injectable so the guard logic is testable without a database. It must
    return a DB-API connection.
    """
    plan = _dict(step)
    receipt: dict[str, Any] = {
        "schema_version": EXECUTION_SCHEMA,
        "adapter": _text(plan.get("adapter")),
        "table": _text(plan.get("table")),
        "identity_column": _text(plan.get("identity_column")),
        "identity_value": _text(identity_value),
        "status": "REFUSED",
        "reason_code": "",
        "rows_deleted": 0,
    }

    if _text(plan.get("adapter")) != "db_sql":
        receipt["reason_code"] = "CLEANUP_ADAPTER_NOT_SUPPORTED"
        return receipt

    # The ownership proof is the whole safety argument and is checked before anything
    # else touches the database.
    if plan.get("requires_ownership_proof") is not False:
        owned, basis = row_was_created_by_this_run(
            identity_value,
            creation_receipts=creation_receipts,
            table=_text(plan.get("table")),
        )
        receipt["ownership_basis"] = basis
        if not owned:
            receipt["reason_code"] = REASON_NOT_RUN_OWNED
            return receipt

    table = _text(plan.get("table"))
    column = _text(plan.get("identity_column"))
    if not _IDENTIFIER_RE.match(table) or not _IDENTIFIER_RE.match(column):
        # Identifiers cannot be parameterised, so they are whitelisted by shape rather
        # than quoted. A name that is not a bare identifier does not reach SQL.
        receipt["reason_code"] = "CLEANUP_IDENTIFIER_NOT_SAFE"
        return receipt
    if not _text(identity_value):
        receipt["reason_code"] = REASON_NO_IDENTITY
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

    try:
        cursor = connection.cursor()
        cursor.execute(
            f'DELETE FROM "{table}" WHERE "{column}" = %s',  # noqa: S608 - identifiers whitelisted above
            (_text(identity_value),),
        )
        deleted = int(getattr(cursor, "rowcount", 0) or 0)
        connection.commit()
        receipt["status"] = "CLEANED"
        receipt["rows_deleted"] = deleted
        if deleted == 0:
            # Not a failure -- the row may already be gone -- but it must not read as a
            # successful deletion of something.
            receipt["reason_code"] = "CLEANUP_ROW_ALREADY_ABSENT"
    except Exception as exc:
        try:
            connection.rollback()
        except Exception:
            pass
        receipt["status"] = "FAILED"
        receipt["reason_code"] = f"CLEANUP_DB_DELETE_FAILED:{type(exc).__name__}"
        receipt["detail"] = str(exc)[:200]
    finally:
        try:
            connection.close()
        except Exception:
            pass
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


def build_ordered_delete_plan(
    *,
    table: str,
    identity_column: str,
    identity_value: Any,
    entities: Any,
) -> list[dict[str, Any]]:
    """Delete steps for one row, dependents first, owner last.

    Every step carries the same ownership requirement as a single-table delete: the
    executor re-checks the identity and refuses a row this run did not create.
    """
    steps: list[dict[str, Any]] = []
    for dependent in dependent_tables_for(table, identity_column, entities):
        steps.append({
            "adapter": "db_sql",
            "mode": "row_delete",
            "table": dependent,
            "identity_column": identity_column,
            "identity_value": _text(identity_value),
            "scope": "run_created_only",
            "requires_ownership_proof": True,
            "delete_order": "dependent",
            "owner_table": _text(table),
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
