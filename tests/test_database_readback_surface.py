"""An entity the API never exposes for reading is still observable through its table.

``SURFACE_DATABASE_OBSERVER`` was in the resolver's allowed surface set and carried a
cost weight, and no candidate finder ever emitted it. So the data-layer readback existed
in the vocabulary and never in a result.

On the live benchmark target the refund endpoints are exactly the case it is for: the
source declares no GET for refunds anywhere, while ``refunds`` is a real table with a
real primary key. Without this readback the assertion cannot be observed at all, so the
alternative is not a weaker observation -- it is none.

Every leg is source-declared, which is what this surface requires and why it is not
inference:

* the table and its identity column arrive as IR entities built from the ingested DDL,
  carrying ``fields`` and ``identity_fields``;
* the connection is the operator's own declaration, read from the IR's
  ``observation_surfaces`` so this answers the same question the observer gate does;
* the identity is a parameter the write itself declares, so it is known before the
  write runs.

Take any one of the three away and the candidate is refused.
"""

from __future__ import annotations

import pytest

from ai_test_asset_center.source_declared_readback_resolver import (
    IDENTITY_REQUEST_BODY,
    IDENTITY_REQUEST_PATH,
    SURFACE_DATABASE_OBSERVER,
    SURFACE_IDENTITY_GET,
    STATUS_RESOLVED,
    _db_surface_available,
    _find_database_readback_candidates,
    resolve_readback_contract,
)


def _surfaces(db_available: bool):
    return [
        {"surface": "http_api", "available": True},
        {"surface": "db_snapshot", "available": db_available},
        {"surface": "ui_browser", "available": False},
    ]


def _entity(name, fields, identity_fields):
    return {
        "id": "bir_" + name,
        "name": name,
        "kind": "resource",
        "fields": list(fields),
        "identity_fields": list(identity_fields),
    }


REFUND_APPROVE = {
    "id": "w_approve", "operation_id": "post_refund_approve",
    "method": "POST", "path": "/api/refunds/:id/approve",
    "parameters": [], "read_write": "write",
}
REFUNDS_TABLE = _entity("refunds", ["id", "order_id", "amount", "status"], ["id"])


def _ir(db_available=True, entities=(REFUNDS_TABLE,), operations=(REFUND_APPROVE,)):
    return {
        "operations": list(operations),
        "entities": list(entities),
        "observation_surfaces": _surfaces(db_available),
    }


# ── the declaration gate ────────────────────────────────────────────────────

def test_db_surface_availability_is_read_from_the_ir() -> None:
    assert _db_surface_available(_ir(db_available=True)) is True
    assert _db_surface_available(_ir(db_available=False)) is False
    assert _db_surface_available({}) is False, "no declaration means no database"


def test_no_declared_database_yields_no_candidate() -> None:
    """Inferring a connection would produce an observer that cannot connect."""
    assert _find_database_readback_candidates(REFUND_APPROVE, _ir(db_available=False)) == []


# ── the schema gate ─────────────────────────────────────────────────────────

def test_a_table_without_a_declared_key_is_refused() -> None:
    """No declared key means no way to read one row back."""
    keyless = _entity("refunds", ["order_id", "amount"], [])
    assert _find_database_readback_candidates(REFUND_APPROVE, _ir(entities=(keyless,))) == []


def test_an_unrelated_table_is_not_a_candidate() -> None:
    """A refund write must not be observed through the products table."""
    products = _entity("products", ["sku", "price"], ["sku"])
    assert _find_database_readback_candidates(REFUND_APPROVE, _ir(entities=(products,))) == []


# ── a real candidate ────────────────────────────────────────────────────────

def test_refund_approve_resolves_through_its_own_table() -> None:
    candidates = _find_database_readback_candidates(REFUND_APPROVE, _ir())
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["surface_type"] == SURFACE_DATABASE_OBSERVER
    assert candidate["table"] == "refunds"
    assert candidate["identity_column"] == "id"
    assert candidate["path"] == "db://refunds"
    assert candidate["evidence_type"] == "database_schema_observer"


def test_the_identity_comes_from_the_write_path_param() -> None:
    candidate = _find_database_readback_candidates(REFUND_APPROVE, _ir())[0]
    identity = candidate["_identity_strategy"]
    assert identity["type"] == IDENTITY_REQUEST_PATH
    assert identity["target_parameter"] == "id"
    assert identity["status"] == "RESOLVED"


def test_a_referenced_table_uses_the_declared_foreign_key() -> None:
    """A payment with no payments table is still observable through orders."""
    pay = {
        "id": "w_pay", "method": "POST", "path": "/api/payments/pay",
        "parameters": ["orderId", "amount"], "read_write": "write",
    }
    orders = _entity("orders", ["id", "status", "total"], ["id"])
    candidates = _find_database_readback_candidates(
        pay, _ir(entities=(orders,), operations=(pay,))
    )
    assert len(candidates) == 1
    identity = candidates[0]["_identity_strategy"]
    assert identity["type"] == IDENTITY_REQUEST_BODY
    assert identity["canonical_field_id"] == "orderId"


def test_a_create_with_no_identity_yet_is_refused() -> None:
    """A collection POST has no id before it runs; supplying one is the write
    response's job, not this surface's."""
    create = {
        "id": "w_create", "method": "POST", "path": "/api/refunds",
        "parameters": ["amount"], "read_write": "write",
    }
    assert _find_database_readback_candidates(create, _ir(operations=(create,))) == []


# ── ranking and end to end ──────────────────────────────────────────────────

def test_the_contract_resolves_end_to_end() -> None:
    result = resolve_readback_contract(REFUND_APPROVE, behavior_ir=_ir())
    assert result["status"] == STATUS_RESOLVED
    contract = result["contract"]
    assert contract["readback_surface_type"] == SURFACE_DATABASE_OBSERVER
    assert contract["endpoint_template"] == "db://refunds"


def test_an_http_read_outranks_the_database() -> None:
    """The database is the last resort. Its cost weight already says so, and reading
    the API when the API can answer keeps the evidence on the customer's own surface.
    """
    refund_get = {"id": "r_refund", "method": "GET", "path": "/api/refunds/{id}"}
    ir = _ir(operations=(REFUND_APPROVE, refund_get))
    contract = resolve_readback_contract(REFUND_APPROVE, behavior_ir=ir)["contract"]
    assert contract["readback_surface_type"] == SURFACE_IDENTITY_GET
    assert contract["endpoint_template"] == "/api/refunds/{id}"


def test_a_pre_resolved_identity_is_not_re_derived() -> None:
    """Schema candidates carry their own identity.

    Re-deriving it through the HTTP path strategies fails and would discard a viable
    readback, which is how this surface stayed empty in practice.
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "ai_test_asset_center" / "source_declared_readback_resolver.py"
    ).read_text(encoding="utf-8")
    assert 'pre_resolved = _dict(cand.get("_identity_strategy"))' in source
    assert 'if pre_resolved.get("status") == "RESOLVED":' in source


def test_the_evidence_type_uses_the_declared_vocabulary() -> None:
    """Inventing a second name for one evidence class is how halves start disagreeing."""
    from ai_test_asset_center.source_declared_readback_resolver import (
        EVIDENCE_PRIORITY,
        _RESOLVABLE_EVIDENCE,
    )

    candidate = _find_database_readback_candidates(REFUND_APPROVE, _ir())[0]
    assert candidate["evidence_type"] in EVIDENCE_PRIORITY
    assert candidate["evidence_type"] in _RESOLVABLE_EVIDENCE
