"""Sibling collection POST examples may fill empty PATCH/PUT bodies."""
from __future__ import annotations

from ai_test_asset_center.experiment_compiler_support import _source_request_example


def test_patch_inherits_unique_collection_post_example() -> None:
    patch = {
        "id": "op_patch_cart",
        "method": "PATCH",
        "path": "/api/cart/items/:id",
        "request_example": {},
    }
    post = {
        "id": "op_post_cart",
        "method": "POST",
        "path": "/api/cart/items",
        "request_example": {"sku": "SKU-1", "qty": 1},
    }
    example = _source_request_example(patch, sibling_operations=[patch, post])
    assert example == {"sku": "SKU-1", "qty": 1}


def test_patch_stays_empty_when_sibling_post_has_no_example() -> None:
    patch = {
        "id": "op_patch_product",
        "method": "PATCH",
        "path": "/api/products/admin/:sku",
    }
    post = {
        "id": "op_post_product",
        "method": "POST",
        "path": "/api/products",
        "request_example": {},
    }
    assert _source_request_example(patch, sibling_operations=[patch, post]) == {}


def test_ambiguous_sibling_posts_fail_closed() -> None:
    patch = {
        "id": "op_patch",
        "method": "PATCH",
        "path": "/api/items/{id}",
    }
    siblings = [
        patch,
        {
            "id": "op_a",
            "method": "POST",
            "path": "/api/items",
            "request_example": {"a": 1},
        },
        {
            "id": "op_b",
            "method": "POST",
            "path": "/api/items",
            "request_example": {"b": 2},
        },
    ]
    assert _source_request_example(patch, sibling_operations=siblings) == {}
