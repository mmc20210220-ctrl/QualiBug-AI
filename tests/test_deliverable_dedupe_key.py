"""Regression: one operation-level property must be delivered once.

E2E finding (2026-08-07, benchmark run 3): one authorization/validation
property ("this endpoint has no role gate") is compiled into multiple
obligations — one per (control, treatment) actor pair — and each pair
independently executes and reaches the delivery gate. N pairs produced N
deliverable findings for one real defect; the evaluator's GT dedupe kept a
single true positive and scored the other N-1 as false positives, halving
precision without adding discovery value.

The batch executor now folds repeated deliveries of the same
(method, path, assertion kind) into the first finding as
``duplicate_variants`` (and a visible description note), so the delivered
report still proves the property for every tried actor pair while only one
copy enters the findings list.
"""
from __future__ import annotations

import pytest

from ai_test_asset_center._experiment_batch_executor_single_finding_mechanics import (
    _deliverable_dedupe_key,
)


def test_dedupe_key_groups_same_property_actor_variants() -> None:
    owner = {
        "title": (
            "[ContractOracle] owner_tenant_visibility: seller "
            "POST /api/payments/refund-to-balance"
        )
    }
    buyer = {
        "title": (
            "[ContractOracle] owner_tenant_visibility: buyer "
            "POST /api/payments/refund-to-balance"
        )
    }
    assert _deliverable_dedupe_key(owner) == _deliverable_dedupe_key(buyer)
    assert _deliverable_dedupe_key(owner) == (
        "owner_tenant_visibility:POST:/api/payments/refund-to-balance"
    )


def test_dedupe_key_distinguishes_property_kind() -> None:
    visibility = {
        "title": (
            "[ContractOracle] owner_tenant_visibility: buyer "
            "POST /api/coupons/admin/create"
        )
    }
    validation = {
        "title": (
            "[ContractOracle] validation_rejection: finance "
            "POST /api/coupons/admin/create"
        )
    }
    assert _deliverable_dedupe_key(visibility) != _deliverable_dedupe_key(validation)


def test_dedupe_key_distinguishes_method_and_path() -> None:
    read = {
        "title": (
            "[ContractOracle] owner_tenant_visibility: buyer "
            "GET /api/orders"
        )
    }
    write = {
        "title": (
            "[ContractOracle] owner_tenant_visibility: buyer "
            "POST /api/orders"
        )
    }
    other_path = {
        "title": (
            "[ContractOracle] owner_tenant_visibility: buyer "
            "GET /api/refunds"
        )
    }
    assert _deliverable_dedupe_key(read) != _deliverable_dedupe_key(write)
    assert _deliverable_dedupe_key(read) != _deliverable_dedupe_key(other_path)


def test_dedupe_key_normalizes_query_and_trailing_slash() -> None:
    with_query = {
        "title": (
            "[ContractOracle] owner_tenant_visibility: buyer "
            "GET /api/orders?status=PAID"
        )
    }
    plain = {
        "title": (
            "[ContractOracle] owner_tenant_visibility: buyer "
            "GET /api/orders/"
        )
    }
    assert _deliverable_dedupe_key(with_query) == _deliverable_dedupe_key(plain)


def test_dedupe_key_fail_closed_on_unparseable_title() -> None:
    """Titles outside the canonical format must not group (conservative)."""
    assert _deliverable_dedupe_key({"title": "unrelated text"}) == ""
    assert _deliverable_dedupe_key({}) == ""
    assert _deliverable_dedupe_key({"title": ""}) == ""
