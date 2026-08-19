"""Unit tests for the required-request-body materialization guard.

A write whose source declares the request body as required
(requestBody.required=true with a permissive object schema, or a required
field list) must go out as ``{}`` when no step body and no example exist —
sending NO body makes the framework reject it 422 "Field required" before
any business handler runs, a harness artifact that was previously
adjudicated as a defect.
"""
from __future__ import annotations

from ai_test_asset_center.experiment_plan_step_executor_core import (
    _request_body_required,
)


def test_boolean_required_on_request_schema_is_required() -> None:
    op = {
        "request_schema": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"type": "object", "additionalProperties": True}
                }
            },
        }
    }
    assert _request_body_required(op) is True


def test_required_field_list_is_required() -> None:
    op = {
        "request_schema": {
            "required": ["sku"],
            "content": {"application/json": {"schema": {"properties": {"sku": {}}}}},
        }
    }
    assert _request_body_required(op) is True


def test_top_level_request_body_required_flag() -> None:
    op = {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"type": "object", "additionalProperties": True}
                }
            },
        }
    }
    assert _request_body_required(op) is True


def test_optional_body_is_not_required() -> None:
    assert _request_body_required({"request_schema": {"required": False}}) is False
    assert _request_body_required({"request_schema": {}}) is False
    assert _request_body_required({}) is False
    assert _request_body_required({"request_schema": {"required": []}}) is False
