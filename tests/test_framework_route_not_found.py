"""Regression for framework-level "route not implemented" 404 detection.

A documented interface that is NOT implemented on the deployed target returns
a framework-level 404 (content-type text/html, body "Cannot POST /path") — a
real, reproducible documentation/implementation-drift defect. It must be
distinguished from a business 404 (application/json), which means the resource
genuinely does not exist and remains a control-arm setup gap.
"""
from __future__ import annotations

from ai_test_asset_center.sandbox_write_executor_base import (
    framework_route_not_found,
)


def test_framework_404_with_html_content_type_is_route_not_found() -> None:
    assert framework_route_not_found(404, "text/html; charset=utf-8") is True


def test_framework_404_with_html_body_fallback_is_route_not_found() -> None:
    # A gateway/proxy may strip the header yet keep the HTML body marker.
    assert (
        framework_route_not_found(404, "", {"_raw": "Cannot POST /recount/ABC"})
        is True
    )


def test_business_404_with_json_content_type_is_not_route_not_found() -> None:
    assert framework_route_not_found(404, "application/json") is False
    assert (
        framework_route_not_found(404, "application/json", {"error": "sku not found"})
        is False
    )


def test_non_404_is_never_route_not_found() -> None:
    assert framework_route_not_found(200, "text/html") is False
    assert framework_route_not_found(500, "text/html") is False
    assert framework_route_not_found(0, "text/html") is False
