# -*- coding: utf-8 -*-
"""Task 17: cleanup chain root-cause regression tests.

Covers the four systematic cleanup-chain gaps measured in run9
(548 cleanup-attributable failures):

1. Identity tracking must flow execution -> cleanup. An accepted create whose
   response names the created row with a conventional primary identity key
   (case variants ``Id``, ``_id``, nested envelopes, uuid) must resolve at
   cleanup time — not just when the literal declared column spelling appears
   (CLEANUP_ROW_IDENTITY_NOT_RESOLVABLE, 175 traces).
2. Foreign-key fields (orderId/userId/…) must NEVER masquerade as the created
   row identity — the resolution stays fail-closed (existing test contract).
3. Restoration proof for collection-observed deletes: a 2xx DELETE whose
   governed before observation contained the created row and whose after
   observation no longer contains it restores the environment even when the
   after status is 200 with concurrent collection drift
   (cleanup_statuses_succeeded_but_restoration_or_audit_incomplete, 33;
   fixture_cleanup_receipt_not_completed, 78). A wrong-target delete stays
   fail-closed.
4. Schema-driven dependent delete plan: dependents declared in the target's
   own schema carry their real FK column and delete multi-row references, and
   dependent rows of a run-created owner are run-owned by FK transitivity —
   eliminating ForeignKeyViolation on the owner row
   (CLEANUP_DB_DELETE_FAILED:ForeignKeyViolation, 16).
"""
from __future__ import annotations

from ai_test_asset_center import experiment_cleanup
from ai_test_asset_center import experiment_cleanup_executor as cleanup
from ai_test_asset_center import experiment_cleanup_executor_core as cleanup_core
from ai_test_asset_center.cleanup_adapter_ladder import (
    build_ordered_delete_plan,
    execute_declared_adapter_cleanup,
    observed_resource_identity,
    schema_fk_dependents,
)

_DECLARED_SCHEMA = """
CREATE TABLE orders (
  id UUID PRIMARY KEY,
  order_no TEXT UNIQUE NOT NULL,
  user_id UUID NOT NULL REFERENCES users(id),
  status TEXT NOT NULL
);
CREATE TABLE order_items (
  id UUID PRIMARY KEY,
  order_id UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  sku TEXT NOT NULL REFERENCES products(sku)
);
CREATE TABLE payments (
  id UUID PRIMARY KEY,
  payment_no TEXT UNIQUE NOT NULL,
  order_id UUID NOT NULL REFERENCES orders(id),
  user_id UUID NOT NULL REFERENCES users(id)
);
CREATE TABLE refunds (
  id UUID PRIMARY KEY,
  order_id UUID NOT NULL REFERENCES orders(id)
);
CREATE TABLE products (
  sku TEXT PRIMARY KEY,
  title TEXT NOT NULL
);
CREATE TABLE inventory_locks (
  id UUID PRIMARY KEY,
  sku TEXT NOT NULL REFERENCES products(sku)
);
"""


# ── 1. Identity tracking: execution -> cleanup ──────────────────────────────


def test_observed_resource_identity_resolves_case_variant_primary() -> None:
    """A conventional primary identity in any spelling/envelope resolves."""
    assert observed_resource_identity({"Id": "A1"}, "id") == "A1"
    assert observed_resource_identity({"_ID": "A2"}, "id") == "A2"
    assert observed_resource_identity(
        {"data": {"row": {"uuid": "U1"}}}, "id"
    ) == "U1"
    assert observed_resource_identity({"data": {"id": "N1"}}, "id") == "N1"
    # Declared business column wins when present.
    assert observed_resource_identity({"sku": "SKU-1"}, "sku") == "SKU-1"


def test_observed_resource_identity_never_guesses_foreign_keys() -> None:
    """Foreign-key fields name another row and must stay fail-closed."""
    assert observed_resource_identity(
        {"orderId": "O1", "status": "PAID"}, "id"
    ) == ""
    assert observed_resource_identity(
        {"userId": "U9", "addressId": "A9"}, "id"
    ) == ""
    # Child collections are never the created row's own identity.
    assert observed_resource_identity({"items": [{"id": "C1"}]}, "id") == ""


def test_observed_identity_tracked_at_write_time_flows_to_cleanup() -> None:
    """The write-time observed identity stamp is authoritative for cleanup."""
    identity = cleanup_core._adapter_cleanup_identity(
        {"identity_column": "id", "source_step_id": "treatment_1"},
        runtime_bindings={},
        steps_out=[
            {
                "step_id": "treatment_1",
                "phase": "treatment",
                "operation_ref": "create_order",
                "governance_receipt": {
                    "accepted": True,
                    # Response names the row Id (case variant) — declared
                    # column re-extraction alone would miss it.
                    "write": {"status": 201, "body": {"Id": "ORD-42"}},
                    "observed_created_identity": "ORD-42",
                },
            }
        ],
    )
    assert identity == "ORD-42"


def test_cleanup_identity_resolves_generic_primary_without_stamp() -> None:
    """No stamp (legacy receipts): declared column then primary-key fallback."""
    identity = cleanup_core._adapter_cleanup_identity(
        {"identity_column": "id", "source_step_id": "treatment_1"},
        runtime_bindings={},
        steps_out=[
            {
                "step_id": "treatment_1",
                "phase": "treatment",
                "operation_ref": "create_order",
                "governance_receipt": {
                    "accepted": True,
                    "write": {"status": 201, "body": {"Id": "ORD-43"}},
                },
            }
        ],
    )
    assert identity == "ORD-43"


def test_creation_receipt_resolves_generic_primary_identity() -> None:
    """Ownership creation receipts bind whenever the response carried an id."""
    receipts = cleanup_core._creation_receipts_from_accepted_writes(
        cleanup_plan=[
            {
                "adapter": "db_sql",
                "table": "orders",
                "identity_column": "id",
                "source_step_id": "treatment_1",
            }
        ],
        accepted_governed_writes=[
            {
                "step_id": "treatment_1",
                "operation_ref": "create_order",
                "write": {"status": 201, "body": {"Id": "ORD-44"}},
            }
        ],
    )
    assert len(receipts) == 1
    assert receipts[0]["identity_value"] == "ORD-44"
    assert receipts[0]["table"] == "orders"


def test_creation_receipt_stays_fail_closed_without_identity() -> None:
    """A response with no conventional identity yields no creation receipt."""
    receipts = cleanup_core._creation_receipts_from_accepted_writes(
        cleanup_plan=[
            {
                "adapter": "db_sql",
                "table": "orders",
                "identity_column": "id",
                "source_step_id": "treatment_1",
            }
        ],
        accepted_governed_writes=[
            {
                "step_id": "treatment_1",
                "operation_ref": "create_order",
                "write": {"status": 201, "body": {"ok": True}},
            }
        ],
    )
    assert receipts == []


def test_projection_identity_uses_tracked_or_generic_identity() -> None:
    """Cleanup contract projection resolves tracked/generic identities too."""
    identity = cleanup._identity_from_governed_write(
        {"identity_column": "id"},
        {"accepted": True, "write": {"status": 201, "body": {"Uuid": "ORD-45"}}},
    )
    assert identity == "ORD-45"


# ── 3. Restoration proof: presence removal for collection-observed deletes ──


def _create_receipt(identity: str = "42") -> dict:
    return {
        "accepted": True,
        "method": "POST",
        "path": "/api/orders",
        "before": {"status": 200, "body": {"items": [{"id": 1, "status": "PAID"}]}},
        "write": {
            "status": 201,
            "body": {"id": identity, "order_no": "ON-42", "status": "PENDING_PAYMENT"},
        },
        "after": {
            "status": 200,
            "body": {
                "items": [
                    {"id": identity, "status": "PENDING_PAYMENT"},
                    {"id": 1, "status": "PAID"},
                ]
            },
        },
        "audit_path": "/audit/a1",
        "audit_record": {"x": 1},
        "before_ref": "b1",
        "after_ref": "a1",
    }


def _delete_receipt(
    *,
    after_body: dict,
    after_status: int = 200,
    method: str = "DELETE",
    path: str = "/api/orders/42",
) -> dict:
    return {
        "accepted": True,
        "method": method,
        "path": path,
        "before": {
            "status": 200,
            "body": {
                "items": [
                    {"id": "42", "status": "PENDING_PAYMENT"},
                    {"id": 1, "status": "PAID"},
                ]
            },
        },
        "write": {"status": 200, "body": {"ok": True}},
        "after": {"status": after_status, "body": after_body},
        "audit_path": "/audit/a2",
        "audit_record": {"x": 2},
        "before_ref": "b2",
        "after_ref": "a2",
    }


def test_collection_observed_delete_proves_restoration_by_presence_removal() -> None:
    """Concurrent collection drift must not erase a proven removal."""
    original = _create_receipt()
    cleanup_write = _delete_receipt(
        # The created row is gone; a concurrent row (7) appeared; after=200.
        after_body={
            "items": [
                {"id": 1, "status": "PAID"},
                {"id": 7, "status": "PENDING_PAYMENT"},
            ]
        },
    )
    assert experiment_cleanup._cleanup_restores_governed_write(
        original, cleanup_write
    ) is True


def test_wrong_target_delete_stays_fail_closed() -> None:
    """The created row still present after cleanup => restoration NOT proven."""
    original = _create_receipt()
    cleanup_write = _delete_receipt(
        path="/api/orders/999",
        after_body={
            "items": [
                {"id": "42", "status": "PENDING_PAYMENT"},
                {"id": 1, "status": "PAID"},
            ]
        },
    )
    assert experiment_cleanup._cleanup_restores_governed_write(
        original, cleanup_write
    ) is False


def test_delete_with_404_after_still_restores() -> None:
    original = _create_receipt()
    cleanup_write = _delete_receipt(
        after_body={"error": "not found"}, after_status=404
    )
    assert experiment_cleanup._cleanup_restores_governed_write(
        original, cleanup_write
    ) is True


def test_rejected_cleanup_never_restores() -> None:
    original = _create_receipt()
    cleanup_write = _delete_receipt(
        after_body={
            "items": [
                {"id": "42", "status": "PENDING_PAYMENT"},
                {"id": 1, "status": "PAID"},
            ]
        }
    )
    cleanup_write["accepted"] = False
    assert experiment_cleanup._cleanup_restores_governed_write(
        original, cleanup_write
    ) is False


# ── 4. Schema-driven dependent delete plan (FK root cause) ──────────────────


def test_schema_fk_dependents_names_real_fk_columns() -> None:
    deps = schema_fk_dependents(_DECLARED_SCHEMA, "orders")
    by_table = {row["table"]: row["fk_column"] for row in deps}
    assert by_table == {
        "order_items": "order_id",
        "payments": "order_id",
        "refunds": "order_id",
    }
    prod_deps = {
        row["table"]: row["fk_column"]
        for row in schema_fk_dependents(_DECLARED_SCHEMA, "products")
    }
    assert prod_deps == {"inventory_locks": "sku", "order_items": "sku"}


def test_build_ordered_delete_plan_orders_dependents_with_fk_columns() -> None:
    plan = build_ordered_delete_plan(
        table="orders",
        identity_column="id",
        identity_value="ORD-1",
        entities=[],
        declared_schema=_DECLARED_SCHEMA,
    )
    assert [row["delete_order"] for row in plan] == [
        "dependent",
        "dependent",
        "dependent",
        "owner",
    ]
    dependent_columns = {
        row["table"]: row["identity_column"]
        for row in plan
        if row["delete_order"] == "dependent"
    }
    assert dependent_columns == {
        "order_items": "order_id",
        "payments": "order_id",
        "refunds": "order_id",
    }
    assert plan[-1]["table"] == "orders"
    assert plan[-1]["identity_column"] == "id"


def test_plan_without_schema_keeps_legacy_shapes() -> None:
    plan = build_ordered_delete_plan(
        table="orders",
        identity_column="id",
        identity_value="ORD-1",
        entities=[],
        declared_schema="",
    )
    assert plan == [
        {
            "adapter": "db_sql",
            "mode": "row_delete",
            "table": "orders",
            "identity_column": "id",
            "identity_value": "ORD-1",
            "scope": "run_created_only",
            "requires_ownership_proof": True,
            "delete_order": "owner",
        }
    ]


class _FakeCursor:
    def __init__(self, rowcount: int) -> None:
        self._rowcount = rowcount

    @property
    def rowcount(self) -> int:
        return self._rowcount

    def execute(self, sql: str, params: tuple) -> None:  # pragma: no cover
        self._sql = sql
        self._params = params


class _FakeConnection:
    def __init__(self, rowcount: int) -> None:
        self._rowcount = rowcount
        self.committed = False
        self.rolled_back = False

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._rowcount)

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        pass


def test_dependent_delete_uses_fk_column_and_accepts_multi_row() -> None:
    """Dependent rows delete by their FK column; >1 rows is legit, not a
    cardinality failure (multiple line items reference one order)."""
    conn = _FakeConnection(rowcount=3)
    receipt = execute_declared_adapter_cleanup(
        {
            "adapter": "db_sql",
            "table": "payments",
            "identity_column": "order_id",
            "identity_value": "ORD-1",
            "scope": "run_created_only",
            "requires_ownership_proof": True,
            "delete_order": "dependent",
            "owner_table": "orders",
            "dependent_fk_column": "order_id",
        },
        identity_value="ORD-1",
        creation_receipts=[
            {
                "status": "created",
                "table": "orders",
                "identity_value": "ORD-1",
            }
        ],
        policy_decision={"write_allowed": True},
        connect=lambda: conn,
    )
    assert receipt["status"] == "CLEANED"
    assert receipt["rows_deleted"] == 3
    assert conn.committed is True


def test_dependent_delete_ownership_is_transitive_through_owner() -> None:
    """A dependent row referencing the run-created owner row is run-owned."""
    conn = _FakeConnection(rowcount=1)
    receipt = execute_declared_adapter_cleanup(
        {
            "adapter": "db_sql",
            "table": "payments",
            "identity_column": "order_id",
            "identity_value": "ORD-2",
            "scope": "run_created_only",
            "requires_ownership_proof": True,
            "delete_order": "dependent",
            "owner_table": "orders",
            "dependent_fk_column": "order_id",
        },
        identity_value="ORD-2",
        creation_receipts=[
            {
                "status": "created",
                "table": "orders",
                "identity_value": "ORD-2",
            }
        ],
        policy_decision={"write_allowed": True},
        connect=lambda: conn,
    )
    assert receipt["status"] == "CLEANED"
    assert receipt["ownership_basis"].startswith("transitive_owner_creation:")


def test_dependent_delete_refused_without_run_created_owner() -> None:
    """No owner proof, no marker => dependent rows are not run-owned."""
    receipt = execute_declared_adapter_cleanup(
        {
            "adapter": "db_sql",
            "table": "payments",
            "identity_column": "order_id",
            "identity_value": "ORD-3",
            "scope": "run_created_only",
            "requires_ownership_proof": True,
            "delete_order": "dependent",
            "owner_table": "orders",
            "dependent_fk_column": "order_id",
        },
        identity_value="ORD-3",
        creation_receipts=[],
        policy_decision={"write_allowed": True},
        connect=lambda: _FakeConnection(1),
    )
    assert receipt["status"] == "REFUSED"
    assert receipt["reason_code"] == "CLEANUP_ROW_NOT_CREATED_BY_THIS_RUN"


def test_owner_delete_cardinality_mismatch_still_fails_closed() -> None:
    """The owner row must be addressed exactly once — unchanged behavior."""
    conn = _FakeConnection(rowcount=2)
    receipt = execute_declared_adapter_cleanup(
        {
            "adapter": "db_sql",
            "table": "orders",
            "identity_column": "id",
            "identity_value": "ORD-4",
            "scope": "run_created_only",
            "requires_ownership_proof": True,
            "delete_order": "owner",
        },
        identity_value="ORD-4",
        creation_receipts=[
            {
                "status": "created",
                "table": "orders",
                "identity_value": "ORD-4",
            }
        ],
        policy_decision={"write_allowed": True},
        connect=lambda: conn,
    )
    assert receipt["status"] == "FAILED"
    assert receipt["reason_code"] == "CLEANUP_DB_DELETE_CARDINALITY_MISMATCH"
    assert conn.rolled_back is True
