"""Reasoner documented-path inventory must cover heterogeneous API docs.

The historical extractor only matched JSON double-quoted paths, so markdown
API specs produced an empty ``EXACT API PATHS AVAILABLE`` hint and the
reasoner engines never saw a documented-surface inventory. Express-style
``:param`` segments were also truncated away.
"""

from __future__ import annotations

from ai_test_asset_center.stage_reason_all_v2 import _extract_api_paths


def test_json_quoted_paths_are_extracted() -> None:
    text = '{"paths": {"/api/orders/{id}": {}, "/api/cart/items/{id}": {}}}'
    assert _extract_api_paths(text) == ["/api/orders/{id}", "/api/cart/items/{id}"]


def test_markdown_backtick_paths_are_extracted() -> None:
    text = "List carts: `GET /api/cart/items` and `DELETE /api/cart/items/:id`"
    assert _extract_api_paths(text) == ["/api/cart/items", "/api/cart/items/:id"]


def test_bare_method_path_lines_are_extracted() -> None:
    text = "POST /api/users/addresses\nGET /api/users/admin/search\n"
    assert _extract_api_paths(text) == ["/api/users/addresses", "/api/users/admin/search"]


def test_express_param_segments_survive() -> None:
    text = "PATCH /api/products/admin/:sku"
    assert _extract_api_paths(text) == ["/api/products/admin/:sku"]


def test_duplicates_and_trailing_slashes_collapse() -> None:
    text = "GET /api/orders/  GET /api/orders  GET /api/orders"
    assert _extract_api_paths(text) == ["/api/orders"]


def test_non_api_paths_are_ignored() -> None:
    text = "GET /health  GET /version  GET /api/metrics"
    assert _extract_api_paths(text) == ["/api/metrics"]
