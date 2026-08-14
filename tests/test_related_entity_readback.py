"""A write is observable through the entity it references, not only the one it is named after.

``POST /api/payments/pay`` declares an ``orderId`` parameter and moves that order to
PAID. The payment resource itself has no GET, so the only readback the source declares
is ``GET /api/orders/{id}``. The identity-GET finder rejects it on purpose -- it
requires the read's resource domain to equal the write's -- so every cross-entity write
sat at ``READBACK_SOURCE_NOT_DECLARED`` while the source declared both halves of the
relationship.

Measured on the live 131-defect target: 9 of 17 writes already had declared observers.
This adds ``POST /api/payments/pay`` and ``POST /api/refunds``, both resolving to
``GET /api/orders/:id``. The refund approve/reject endpoints stay unresolved, correctly:
the source declares no GET for refunds and their paths carry the refund's own id, so
there is no declared read to bind. Login, register and coupon-validate stay unresolved
too -- they produce no durable entity to read back.

This is not a relaxation of the readback contract. All three conditions still hold: the
read is a source-declared identity GET, the identity comes from a parameter the write
itself declares (so it exists before the write runs, which is what makes a real
before/after comparison possible), and the read's domain is the entity that key names.
A write and a read merely sharing a word remains not a relationship.
"""

from __future__ import annotations

import pytest

from ai_test_asset_center.source_declared_readback_resolver import (
    SURFACE_IDENTITY_GET,
    SURFACE_RELATED_ENTITY_GET,
    IDENTITY_REQUEST_BODY,
    STATUS_RESOLVED,
    _domain_matches_entity,
    _find_related_entity_get_candidates,
    _referenced_entity_params,
    resolve_readback_contract,
)


def _op(op_id, method, path, params=(), read_write=""):
    node = {"id": op_id, "operation_id": op_id, "method": method, "path": path,
            "parameters": list(params)}
    if read_write:
        node["read_write"] = read_write
    return node


PAY = _op("w_pay", "POST", "/api/payments/pay",
          ["amount", "channel", "idempotencyKey", "orderId"], "write")
ORDER_GET = _op("r_order", "GET", "/api/orders/{id}")
ORDER_LIST = _op("r_orders", "GET", "/api/orders")
PRODUCT_GET = _op("r_product", "GET", "/api/products/{sku}")


# ── foreign-key extraction ──────────────────────────────────────────────────

def test_declared_foreign_keys_are_recognised() -> None:
    referenced = _referenced_entity_params(PAY)
    assert referenced.get("orderId") == "order"
    assert "amount" not in referenced, "a plain value is not a foreign key"
    assert "channel" not in referenced


def test_nested_and_indexed_fields_are_ignored() -> None:
    """items[].sku and user.id are not top-level identities the write can supply."""
    op = _op("w", "POST", "/api/orders", ["items[].sku", "user.id", "couponCode"])
    referenced = _referenced_entity_params(op)
    assert "items[].sku" not in referenced
    assert "user.id" not in referenced
    assert referenced.get("couponCode") == "coupon"


def test_a_bare_id_parameter_is_not_an_entity_reference() -> None:
    """"id" names no entity, so it cannot pick a target domain."""
    assert _referenced_entity_params(_op("w", "POST", "/api/x", ["id"])) == {}


def test_domain_singularisation() -> None:
    assert _domain_matches_entity("orders", "order") is True
    assert _domain_matches_entity("order", "order") is True
    assert _domain_matches_entity("orders", "product") is False
    assert _domain_matches_entity("", "order") is False


# ── candidate discovery ─────────────────────────────────────────────────────

def test_payment_finds_the_order_identity_get() -> None:
    ir = {"operations": [PAY, ORDER_GET, ORDER_LIST, PRODUCT_GET]}
    candidates = _find_related_entity_get_candidates(PAY, ir)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["surface_type"] == SURFACE_RELATED_ENTITY_GET
    assert candidate["path"] == "/api/orders/{id}"
    assert candidate["write_parameter"] == "orderId"
    assert candidate["related_entity"] == "order"


def test_a_collection_get_is_not_an_identity_read() -> None:
    """Only single-placeholder reads identify one entity."""
    ir = {"operations": [PAY, ORDER_LIST]}
    assert _find_related_entity_get_candidates(PAY, ir) == []


def test_an_unreferenced_entity_is_not_a_candidate() -> None:
    """The payment declares no productId, so products are not observable from it."""
    ir = {"operations": [PAY, PRODUCT_GET]}
    assert _find_related_entity_get_candidates(PAY, ir) == []


def test_same_domain_reads_are_left_to_the_identity_get_finder() -> None:
    """This source exists only for the cross-entity case; overlapping would produce
    duplicate candidates for the same read."""
    order_cancel = _op("w_cancel", "POST", "/api/orders/{id}/cancel", ["orderId"], "write")
    ir = {"operations": [order_cancel, ORDER_GET]}
    assert _find_related_entity_get_candidates(order_cancel, ir) == []


def test_a_write_with_no_declared_parameters_yields_nothing() -> None:
    approve = _op("w_approve", "POST", "/api/refunds/{id}/approve", [], "write")
    ir = {"operations": [approve, ORDER_GET]}
    assert _find_related_entity_get_candidates(approve, ir) == []


# ── the contract that comes out ─────────────────────────────────────────────

def test_payment_resolves_to_a_readback_contract() -> None:
    ir = {"operations": [PAY, ORDER_GET, ORDER_LIST]}
    result = resolve_readback_contract(PAY, behavior_ir=ir)
    assert result["status"] == STATUS_RESOLVED
    contract = result["contract"]
    assert contract["readback_surface_type"] == SURFACE_RELATED_ENTITY_GET
    assert contract["endpoint_template"] == "/api/orders/{id}"


def test_the_identity_comes_from_the_declared_request_parameter() -> None:
    """The value must be known BEFORE the write runs, or there is no "before" read."""
    ir = {"operations": [PAY, ORDER_GET]}
    contract = resolve_readback_contract(PAY, behavior_ir=ir)["contract"]
    identity = contract["identity_strategy"]
    assert identity["type"] == IDENTITY_REQUEST_BODY
    assert identity["source_path"] == "request.orderId"
    assert identity["canonical_field_id"] == "orderId"


def test_a_same_domain_readback_still_wins() -> None:
    """A write with both options must prefer reading its own entity.

    Cross-entity is a fallback, not a replacement -- observing the order when the
    payment itself is readable would report against the wrong subject.
    """
    payment_get = _op("r_payment", "GET", "/api/payments/{id}")
    ir = {"operations": [PAY, payment_get, ORDER_GET]}
    contract = resolve_readback_contract(PAY, behavior_ir=ir)["contract"]
    assert contract["readback_surface_type"] == SURFACE_IDENTITY_GET
    assert contract["endpoint_template"] == "/api/payments/{id}"


def test_a_write_with_no_declared_read_stays_unresolved() -> None:
    """The refund approve case. No GET for refunds, no foreign key to anything that
    has one -- so it must stay blocked rather than bind to something unrelated."""
    approve = _op("w_approve", "POST", "/api/refunds/{id}/approve", [], "write")
    ir = {"operations": [approve, PRODUCT_GET]}
    assert resolve_readback_contract(approve, behavior_ir=ir)["status"] != STATUS_RESOLVED


# ── the resolver failure is no longer silent ─────────────────────────────────

def test_resolver_exceptions_are_logged_and_blocked() -> None:
    """A bare `except Exception: pass` hid every resolver defect behind
    BLOCKED_MISSING_OBSERVER -- the one code that already explains most blocks."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "ai_test_asset_center" / "experiment_compiler_obligation_core.py"
    ).read_text(encoding="utf-8")
    marker = "from .source_declared_readback_resolver import"
    assert marker in source
    region = source[source.index(marker): source.index(marker) + 2200]
    assert "pass  # Fail-safe: resolver failure does not change existing behavior" not in region
    assert "readback resolver raised for obligation" in region
    assert "BLOCKED_OBSERVER_RESOLUTION_FAILED" in region
