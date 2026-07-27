"""Cleanup must be sought from every declared adapter, and only on run-created rows.

A governed write needs a way back, and until now the only candidate was a
source-declared API compensator. On the live 131-defect target that resolved for 2 of
17 writes -- correctly, because the API genuinely offers no DELETE for products, none
for cart items, and no reversal for a payment -- and 680 of 1189 obligations ended at
BLOCKED_NON_REVERSIBLE_WRITE.

"The API has no undo" is not "this cannot be cleaned up". A run that creates its own row
can delete that row through the database, or drive the admin UI, when the operator
declared those surfaces. Refusing to test because one adapter is missing turns a
capability gap into a coverage gap.

This does not relax the requirement. The gate asks for a real, executable compensator
that leaves a receipt; every tier provides one. The question is now asked of every
declared adapter instead of only HTTP.

The line these tests defend: a data-layer cleanup may only touch rows this run created.
Deleting a row the customer already had is not cleanup, it is damage, and no declaration
makes it acceptable.
"""

from __future__ import annotations

import pytest

from ai_test_asset_center.cleanup_adapter_ladder import (
    REASON_NOT_APPROVED,
    REASON_NOT_RUN_OWNED,
    REASON_NO_ADAPTER,
    REASON_NO_IDENTITY,
    REASON_NO_TABLE,
    TIER_API,
    TIER_DB,
    TIER_UI,
    prefer_constructed_data,
    resolve_cleanup_adapter,
    row_was_created_by_this_run,
)

ORDERS = {"name": "orders", "fields": ["id", "status"], "identity_fields": ["id"]}


def _resolve(**kw):
    base = dict(
        available_adapters={"http_api", "db_sql"},
        entity=ORDERS,
        identity_value="qb_auto_order_20260727_abc",
        target_write_approved=True,
    )
    base.update(kw)
    return resolve_cleanup_adapter(**base)


# ── ownership: the line that does not move ──────────────────────────────────

def test_a_customer_row_is_never_deleted() -> None:
    """The whole safety argument. A real order id carries no run marker."""
    result = _resolve(identity_value="982ab14f-c699-40fb-aaf9-adce75b6968c")
    assert result["tier"] == ""
    assert result["reason_code"] == REASON_NOT_RUN_OWNED


def test_the_run_marker_proves_ownership() -> None:
    owned, basis = row_was_created_by_this_run("qb_auto_sku_QBBOOTSTRAP_123")
    assert owned is True
    assert basis == "run_created_marker"


def test_a_creation_receipt_proves_ownership() -> None:
    """The strong proof: this run recorded creating exactly this row."""
    owned, basis = row_was_created_by_this_run(
        "982ab14f",
        creation_receipts=[{"status": "created", "table": "orders", "identity_value": "982ab14f"}],
        table="orders",
    )
    assert owned is True
    assert basis == "creation_receipt"


def test_a_receipt_for_a_different_table_does_not_transfer() -> None:
    owned, _ = row_was_created_by_this_run(
        "982ab14f",
        creation_receipts=[{"status": "created", "table": "products", "identity_value": "982ab14f"}],
        table="orders",
    )
    assert owned is False


def test_a_receipt_for_a_different_row_does_not_transfer() -> None:
    owned, _ = row_was_created_by_this_run(
        "other-id",
        creation_receipts=[{"status": "created", "table": "orders", "identity_value": "982ab14f"}],
        table="orders",
    )
    assert owned is False


def test_an_empty_identity_is_never_owned() -> None:
    assert row_was_created_by_this_run("")[0] is False
    assert row_was_created_by_this_run(None)[0] is False


# ── the ladder order ────────────────────────────────────────────────────────

def test_a_declared_api_compensator_wins() -> None:
    """The vendor's own reversal path exercises the code a customer would run."""
    result = _resolve(api_compensator={"operation_ref": "bir_cancel", "path": "/api/orders/{id}/cancel",
                                       "method": "POST"})
    assert result["tier"] == TIER_API
    assert result["plan"]["adapter"] == "http_api"


def test_ui_is_preferred_over_the_database() -> None:
    result = _resolve(available_adapters={"http_api", "ui_browser", "db_sql"},
                      ui_cleanup_declared=True)
    assert result["tier"] == TIER_UI


def test_the_database_is_the_last_resort() -> None:
    result = _resolve()
    assert result["tier"] == TIER_DB
    plan = result["plan"]
    assert plan == {
        "adapter": "db_sql",
        "mode": "row_delete",
        "table": "orders",
        "identity_column": "id",
        "identity_value": "qb_auto_order_20260727_abc",
        "scope": "run_created_only",
        "ownership_basis": "run_created_marker",
    }


def test_an_undeclared_ui_adapter_is_not_used_even_if_requested() -> None:
    """A declaration flag without the adapter is not a declaration."""
    result = _resolve(available_adapters={"http_api", "db_sql"}, ui_cleanup_declared=True)
    assert result["tier"] == TIER_DB


# ── every refusal names its missing leg ─────────────────────────────────────

def test_no_declared_adapter_beyond_http() -> None:
    result = _resolve(available_adapters={"http_api"})
    assert result["reason_code"] == REASON_NO_ADAPTER


def test_an_unapproved_target_refuses_the_delete() -> None:
    """A delete is a write; the same gate the governed write executor applies."""
    result = _resolve(target_write_approved=False)
    assert result["reason_code"] == REASON_NOT_APPROVED
    assert result["tier"] == ""


def test_a_table_without_a_declared_key_refuses() -> None:
    result = _resolve(entity={"name": "orders", "identity_fields": []})
    assert result["reason_code"] == REASON_NO_TABLE


def test_an_entity_that_is_not_source_declared_refuses() -> None:
    result = _resolve(entity={})
    assert result["reason_code"] == REASON_NO_TABLE


def test_a_missing_identity_value_refuses() -> None:
    result = _resolve(identity_value="")
    assert result["reason_code"] == REASON_NO_IDENTITY


def test_a_refusal_records_which_tiers_were_considered() -> None:
    """An operator reading the block should learn what to declare."""
    result = _resolve(available_adapters={"http_api"})
    assert TIER_API in result["considered"]
    assert TIER_UI in result["considered"]


# ── data preference ─────────────────────────────────────────────────────────

def test_constructed_data_is_preferred() -> None:
    choice = prefer_constructed_data(constructed={"id": "qb_auto_1"}, existing={"id": "real"})
    assert choice["source"] == "run_constructed"
    assert choice["disposable"] is True


def test_existing_data_is_the_fallback_and_is_flagged() -> None:
    """Usable, but a write against it is not disposable and must find a compensator."""
    choice = prefer_constructed_data(constructed=None, existing={"id": "real-order"})
    assert choice["source"] == "existing_test_system_data"
    assert choice["disposable"] is False
    assert choice["cleanup_required"] is True
    assert "refused" in choice["note"]


def test_no_subject_is_reported_not_guessed() -> None:
    choice = prefer_constructed_data()
    assert choice["subject"] is None
    assert choice["reason_code"] == "NO_SUBJECT_AVAILABLE"


def test_existing_data_still_cannot_be_deleted_from_the_database() -> None:
    """The two rules compose: existing data may be USED, never row-deleted."""
    choice = prefer_constructed_data(existing={"id": "982ab14f-real"})
    result = _resolve(identity_value=choice["subject"]["id"])
    assert result["reason_code"] == REASON_NOT_RUN_OWNED


# ── execution: the guard runs before the database is touched ────────────────

class _FakeCursor:
    def __init__(self, sink, rowcount=1):
        self.sink = sink
        self.rowcount = rowcount

    def execute(self, sql, params=None):
        self.sink.append((sql, params))


class _FakeConn:
    def __init__(self, sink, rowcount=1):
        self.sink = sink
        self._rowcount = rowcount
        self.committed = False

    def cursor(self):
        return _FakeCursor(self.sink, self._rowcount)

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


DB_STEP = {
    "adapter": "db_sql",
    "mode": "row_delete",
    "table": "orders",
    "identity_column": "id",
    "requires_ownership_proof": True,
}


def test_a_customer_row_never_reaches_sql() -> None:
    """The guard runs BEFORE any connection is opened."""
    from ai_test_asset_center.cleanup_adapter_ladder import execute_declared_adapter_cleanup

    sink = []
    opened = []

    def _connect():
        opened.append(True)
        return _FakeConn(sink)

    receipt = execute_declared_adapter_cleanup(
        DB_STEP, identity_value="982ab14f-real-order", connect=_connect
    )
    assert receipt["status"] == "REFUSED"
    assert receipt["reason_code"] == REASON_NOT_RUN_OWNED
    assert opened == [], "no connection may be opened for a row we do not own"
    assert sink == [], "no SQL may be issued"


def test_a_run_created_row_is_deleted_with_a_parameterised_query() -> None:
    from ai_test_asset_center.cleanup_adapter_ladder import execute_declared_adapter_cleanup

    sink = []
    receipt = execute_declared_adapter_cleanup(
        DB_STEP, identity_value="qb_auto_order_1", connect=lambda: _FakeConn(sink)
    )
    assert receipt["status"] == "CLEANED"
    assert receipt["rows_deleted"] == 1
    sql, params = sink[0]
    assert sql == 'DELETE FROM "orders" WHERE "id" = %s'
    assert params == ("qb_auto_order_1",), "the value must be bound, never interpolated"


def test_an_unsafe_identifier_never_reaches_sql() -> None:
    """Identifiers cannot be parameterised, so they are whitelisted by shape."""
    from ai_test_asset_center.cleanup_adapter_ladder import execute_declared_adapter_cleanup

    sink = []
    step = dict(DB_STEP, table='orders"; DROP TABLE users; --')
    receipt = execute_declared_adapter_cleanup(
        step, identity_value="qb_auto_1", connect=lambda: _FakeConn(sink)
    )
    assert receipt["reason_code"] == "CLEANUP_IDENTIFIER_NOT_SAFE"
    assert sink == []


def test_a_missing_row_is_reported_not_claimed_as_cleaned() -> None:
    from ai_test_asset_center.cleanup_adapter_ladder import execute_declared_adapter_cleanup

    receipt = execute_declared_adapter_cleanup(
        DB_STEP, identity_value="qb_auto_gone", connect=lambda: _FakeConn([], rowcount=0)
    )
    assert receipt["rows_deleted"] == 0
    assert receipt["reason_code"] == "CLEANUP_ROW_ALREADY_ABSENT"


def test_a_connection_failure_is_a_receipt_not_a_crash() -> None:
    """A cleanup that did not happen must be as visible as one that did."""
    from ai_test_asset_center.cleanup_adapter_ladder import execute_declared_adapter_cleanup

    def _boom():
        raise OSError("refused")

    receipt = execute_declared_adapter_cleanup(
        DB_STEP, identity_value="qb_auto_1", connect=_boom
    )
    assert receipt["status"] == "FAILED"
    assert receipt["reason_code"].startswith("CLEANUP_DB_CONNECT_FAILED")


def test_no_dsn_and_no_connector_is_refused() -> None:
    from ai_test_asset_center.cleanup_adapter_ladder import execute_declared_adapter_cleanup

    receipt = execute_declared_adapter_cleanup(DB_STEP, identity_value="qb_auto_1")
    assert receipt["reason_code"] == "CLEANUP_DB_CONNECTION_NOT_CONFIGURED"


def test_a_non_database_step_is_not_executed_here() -> None:
    from ai_test_asset_center.cleanup_adapter_ladder import execute_declared_adapter_cleanup

    receipt = execute_declared_adapter_cleanup(
        {"adapter": "http_api"}, identity_value="qb_auto_1"
    )
    assert receipt["reason_code"] == "CLEANUP_ADAPTER_NOT_SUPPORTED"


# ── compile-time availability probing ───────────────────────────────────────

def test_availability_probe_needs_no_row_identity() -> None:
    """Compile has no row identity; a placeholder would just always fail ownership.

    That is exactly why the tier never resolved when the probe supplied one.
    """
    result = resolve_cleanup_adapter(
        available_adapters={"http_api", "db_sql"},
        entity=ORDERS,
        identity_value="",
        availability_only=True,
    )
    assert result["tier"] == TIER_DB
    assert result["availability_only"] is True


def test_an_availability_plan_can_never_delete_on_its_own() -> None:
    """It carries the ownership requirement, and the executor enforces it."""
    from ai_test_asset_center.cleanup_adapter_ladder import execute_declared_adapter_cleanup

    plan = resolve_cleanup_adapter(
        available_adapters={"db_sql"}, entity=ORDERS, availability_only=True
    )["plan"]
    assert plan["requires_ownership_proof"] is True
    assert "identity_value" not in plan, "an availability plan names no row"

    receipt = execute_declared_adapter_cleanup(
        plan, identity_value="982ab14f-real", connect=lambda: None
    )
    assert receipt["reason_code"] == REASON_NOT_RUN_OWNED


def test_availability_still_requires_every_other_leg() -> None:
    """Skipping ownership must not skip the adapter, table or approval checks."""
    assert resolve_cleanup_adapter(
        available_adapters={"http_api"}, entity=ORDERS, availability_only=True
    )["reason_code"] == REASON_NO_ADAPTER
    assert resolve_cleanup_adapter(
        available_adapters={"db_sql"}, entity=ORDERS,
        target_write_approved=False, availability_only=True
    )["reason_code"] == REASON_NOT_APPROVED
    assert resolve_cleanup_adapter(
        available_adapters={"db_sql"}, entity={"name": "orders", "identity_fields": []},
        availability_only=True
    )["reason_code"] == REASON_NO_TABLE


def test_a_path_and_a_table_name_match_across_separators() -> None:
    """/api/cart/items must reach the cart_items table.

    A table joins words with "_" and a path with "/", so they never matched literally
    and cart writes were left with no cleanup tier.
    """
    from ai_test_asset_center.experiment_compiler_obligation import _entity_for_operation

    ir = {"entities": [
        {"name": "cart_items", "identity_fields": ["id"]},
        {"name": "items", "identity_fields": ["id"]},
    ]}
    entity = _entity_for_operation({"path": "/api/cart/items"}, ir)
    assert entity.get("name") == "cart_items", "the longer, more specific table wins"


def test_an_unrelated_path_matches_no_table() -> None:
    from ai_test_asset_center.experiment_compiler_obligation import _entity_for_operation

    ir = {"entities": [{"name": "orders", "identity_fields": ["id"]}]}
    assert _entity_for_operation({"path": "/api/auth/login"}, ir) == {}


def test_a_foreign_key_violation_is_reported_not_swallowed() -> None:
    """Found by running this for real: 16 product deletes all hit ForeignKeyViolation.

    A single-table row delete is not enough for an entity with dependents. Until
    dependency-ordered deletion exists, a loud FAILED receipt is the correct outcome --
    a silent partial cleanup would leave the customer to find the rest.
    """
    from ai_test_asset_center.cleanup_adapter_ladder import execute_declared_adapter_cleanup

    class _FKCursor:
        rowcount = 0

        def execute(self, sql, params=None):
            raise RuntimeError("ForeignKeyViolation: still referenced")

    class _FKConn:
        def cursor(self):
            return _FKCursor()

        def commit(self):
            raise AssertionError("must not commit after a failed delete")

        def rollback(self):
            self.rolled_back = True

        def close(self):
            pass

    receipt = execute_declared_adapter_cleanup(
        DB_STEP, identity_value="qb_auto_product_1", connect=_FKConn
    )
    assert receipt["status"] == "FAILED"
    assert "ForeignKey" in receipt["detail"] or "CLEANUP_DB_DELETE_FAILED" in receipt["reason_code"]
    assert receipt["rows_deleted"] == 0


# ── dependency-ordered deletion ─────────────────────────────────────────────

SCHEMA_ENTITIES = [
    {"name": "products", "fields": ["sku", "price", "status"], "identity_fields": ["sku"]},
    {"name": "inventory", "fields": ["sku", "available_qty"], "identity_fields": ["sku"]},
    {"name": "cart_items", "fields": ["id", "sku", "user_id"], "identity_fields": ["id"]},
    {"name": "order_items", "fields": ["id", "sku", "order_id"], "identity_fields": ["id"]},
    {"name": "orders", "fields": ["id", "user_id", "status"], "identity_fields": ["id"]},
    {"name": "refunds", "fields": ["id", "order_id"], "identity_fields": ["id"]},
]


def test_dependents_are_found_by_the_owners_own_column() -> None:
    """products.sku is carried verbatim by every table that references it."""
    from ai_test_asset_center.cleanup_adapter_ladder import dependent_tables_for

    assert dependent_tables_for("products", "sku", SCHEMA_ENTITIES) == [
        "cart_items", "inventory", "order_items",
    ]


def test_dependents_are_found_by_conventional_foreign_key_naming() -> None:
    """orders.id is carried as order_id, which is how most schemas name it."""
    from ai_test_asset_center.cleanup_adapter_ladder import dependent_tables_for

    assert dependent_tables_for("orders", "id", SCHEMA_ENTITIES) == ["order_items", "refunds"]


def test_a_bare_id_column_is_not_treated_as_a_reference() -> None:
    """Every table has an "id"; matching on it alone would make everything a dependent."""
    from ai_test_asset_center.cleanup_adapter_ladder import dependent_tables_for

    dependents = dependent_tables_for("refunds", "id", SCHEMA_ENTITIES)
    assert "cart_items" not in dependents
    assert "orders" not in dependents


def test_the_owner_is_never_its_own_dependent() -> None:
    from ai_test_asset_center.cleanup_adapter_ladder import dependent_tables_for

    assert "products" not in dependent_tables_for("products", "sku", SCHEMA_ENTITIES)


def test_the_plan_deletes_dependents_first_and_the_owner_last() -> None:
    """The order the ForeignKeyViolations demanded: 16 product deletes all failed until
    inventory and order_items went first."""
    from ai_test_asset_center.cleanup_adapter_ladder import build_ordered_delete_plan

    plan = build_ordered_delete_plan(
        table="products", identity_column="sku",
        identity_value="qb_auto_sku_1", entities=SCHEMA_ENTITIES,
    )
    assert [s["table"] for s in plan][-1] == "products"
    assert all(s["delete_order"] == "dependent" for s in plan[:-1])
    assert plan[-1]["delete_order"] == "owner"


def test_every_step_carries_the_ownership_requirement() -> None:
    """A dependent delete is as dangerous as an owner delete and gets the same guard."""
    from ai_test_asset_center.cleanup_adapter_ladder import build_ordered_delete_plan

    plan = build_ordered_delete_plan(
        table="products", identity_column="sku",
        identity_value="qb_auto_sku_1", entities=SCHEMA_ENTITIES,
    )
    assert all(s["requires_ownership_proof"] is True for s in plan)
    assert all(s["scope"] == "run_created_only" for s in plan)


def test_an_entity_with_no_dependents_yields_a_single_step() -> None:
    from ai_test_asset_center.cleanup_adapter_ladder import build_ordered_delete_plan

    plan = build_ordered_delete_plan(
        table="refunds", identity_column="id",
        identity_value="qb_auto_r1", entities=SCHEMA_ENTITIES,
    )
    assert len(plan) == 1
    assert plan[0]["table"] == "refunds"
