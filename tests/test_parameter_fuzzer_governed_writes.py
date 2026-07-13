from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


def _write_route() -> dict[str, Any]:
    return {
        "method": "POST",
        "path": "/api/cart/items",
        "request_template": {"sku": "SKU-1", "qty": 1},
        "body_properties": {"qty": {"type": "integer"}},
        "execution_policy": "disposable_sandbox_required",
        "disposable_sandbox": {"approved": True},
        "source_refs": [{"source_type": "api_document", "path": "/api/cart/items"}],
    }


def test_write_fuzzer_fails_closed_without_governed_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    from ai_test_asset_center.parameter_fuzzer import ParameterFuzzer

    fuzzer = ParameterFuzzer("http://example.test", allow_write=True)

    def direct_write_call(*args: Any, **kwargs: Any) -> tuple[int, Any, float]:
        raise AssertionError("write fuzzer must not send direct HTTP mutations")

    monkeypatch.setattr(fuzzer, "_call", direct_write_call)

    with pytest.raises(RuntimeError, match="governed_write_executor_required"):
        fuzzer.fuzz_all([_write_route()], max_variants=1)


def test_write_fuzzer_uses_governed_executor_and_records_receipt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from ai_test_asset_center.parameter_fuzzer import ParameterFuzzer

    calls: list[dict[str, Any]] = []

    def governed_executor(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "status": 500,
            "response": {"error": "boom"},
            "duration_ms": 12.5,
            "audit_path": str(tmp_path / "sandbox_write_audit.jsonl"),
            "trace": {
                "steps": [
                    {
                        "method": kwargs["method"],
                        "path": kwargs["path"],
                        "response": {"status_code": 500, "body": {"error": "boom"}},
                    }
                ],
                "sandbox_write": {
                    "status": "completed",
                    "audit_path": str(tmp_path / "sandbox_write_audit.jsonl"),
                },
            },
        }

    fuzzer = ParameterFuzzer(
        "http://example.test",
        allow_write=True,
        governed_write_executor=governed_executor,
    )
    fuzzer._token = "actor-token"

    def direct_write_call(*args: Any, **kwargs: Any) -> tuple[int, Any, float]:
        raise AssertionError("write fuzzer must not send direct HTTP mutations")

    monkeypatch.setattr(fuzzer, "_call", direct_write_call)

    findings = fuzzer.fuzz_all([_write_route()], max_variants=1)

    assert len(calls) == 1
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == "/api/cart/items"
    assert calls[0]["body"] == {"sku": "SKU-1", "qty": "-1"}
    assert calls[0]["token"] == "actor-token"
    assert calls[0]["route"]["path"] == "/api/cart/items"
    assert fuzzer.execution_receipts == [
        {
            "method": "POST",
            "path": "/api/cart/items",
            "status": 500,
            "duration_ms": 12.5,
            "governed": True,
            "audit_path": str(tmp_path / "sandbox_write_audit.jsonl"),
            "sandbox_status": "completed",
        }
    ]
    assert len(findings) == 1
    assert findings[0]["evidence"]["sandbox_write"]["status"] == "completed"
    assert findings[0]["evidence"]["audit_path"] == str(tmp_path / "sandbox_write_audit.jsonl")
    assert findings[0]["raw_evidence"]["has_real_evidence"] is True
    assert findings[0]["raw_evidence"]["request_raw"]["method"] == "POST"
    assert findings[0]["raw_evidence"]["request_raw"]["path"] == "/api/cart/items"
    assert findings[0]["raw_evidence"]["request_raw"]["body"] == {"sku": "SKU-1", "qty": "-1"}
    assert findings[0]["raw_evidence"]["response_raw"]["status_code"] == 500
    assert findings[0]["reproduction"]["is_synthetic"] is False
    assert findings[0]["reproduction"]["method"] == "POST"
    assert findings[0]["reproduction"]["path"] == "/api/cart/items"
    assert findings[0]["reproduction"]["har_evidence"]["status_code"] == 500


def test_write_fuzzer_materializes_unmutated_identity_fields_for_disposable_sandbox() -> None:
    from ai_test_asset_center.parameter_fuzzer import ParameterFuzzer

    calls: list[dict[str, Any]] = []

    def governed_executor(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "status": 400,
            "response": {"error": "validation failed"},
            "duration_ms": 8.0,
            "trace": {
                "steps": [
                    {
                        "method": kwargs["method"],
                        "path": kwargs["path"],
                        "response": {"status_code": 400, "body": {"error": "validation failed"}},
                    }
                ],
                "sandbox_write": {"status": "completed"},
            },
        }

    fuzzer = ParameterFuzzer(
        "http://example.test",
        allow_write=True,
        governed_write_executor=governed_executor,
    )
    route = {
        "method": "POST",
        "path": "/api/auth/register",
        "request_template": {
            "email": "new@example.com",
            "password": "Test@123456",
            "phone": "13900000000",
            "displayName": "Disposable User",
        },
        "body_properties": {"password": {"type": "string"}},
        "execution_policy": "disposable_sandbox_required",
        "disposable_sandbox": {"approved": True},
    }

    findings = fuzzer.fuzz_all([route], max_variants=1)

    assert findings == []
    assert len(calls) == 1
    body = calls[0]["body"]
    assert body["password"] == ""
    assert body["email"] != "new@example.com"
    assert body["email"].startswith("qb-auto-")
    assert body["email"].endswith("@qualibug.local")
    assert body["phone"] != "13900000000"
    assert str(body["displayName"]).startswith("qb_auto_")


def test_pipeline_prepares_write_routes_with_source_body_and_sandbox_contract() -> None:
    from ai_test_asset_center.v12_pipeline import _prepare_parameter_fuzzer_catalog

    api_doc = """
### POST /api/cart/items
Request Body:
```json
{"sku":"SKU-1","qty":1}
```
### GET /api/cart/items
"""
    catalog = [
        {"method": "POST", "path": "/api/cart/items", "body_properties": {"qty": {"type": "integer"}}},
        {"method": "GET", "path": "/api/cart/items"},
    ]

    prepared = _prepare_parameter_fuzzer_catalog(
        catalog,
        selected_paths={"/api/cart/items"},
        api_doc=api_doc,
        runtime_contract={
            "status": "approved",
            "approved_base_url": "http://example.test",
            "environment_ref": "declared-test",
            "environment_type": "test",
            "execution_mode": "approved_sandbox_write",
        },
    )

    post_route = next(item for item in prepared if item["method"] == "POST")
    assert post_route["request_template"] == {"sku": "SKU-1", "qty": 1}
    assert post_route["body_template_provenance"] == "documented_example"
    assert post_route["execution_policy"] == "disposable_sandbox_required"
    assert post_route["disposable_sandbox"] == {"approved": True}


def test_pipeline_governed_write_executor_wraps_sandbox_write(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from ai_test_asset_center.v12_pipeline import _build_parameter_fuzzer_governed_write_executor

    captured: dict[str, Any] = {}

    def fake_execute_with_sandbox_write(scenario: Any, base_url: str, **kwargs: Any) -> dict[str, Any]:
        captured["scenario"] = scenario
        captured["base_url"] = base_url
        captured["kwargs"] = kwargs
        return {
            "steps": [
                {
                    "method": "POST",
                    "path": "/api/cart/items",
                    "status": 500,
                    "response": {"status_code": 500, "body": {"error": "boom"}},
                }
            ],
            "sandbox_write": {
                "status": "completed",
                "audit_path": str(tmp_path / "sandbox_write_audit.jsonl"),
            },
        }

    monkeypatch.setattr(
        "ai_test_asset_center.sandbox_write_executor.execute_with_sandbox_write",
        fake_execute_with_sandbox_write,
    )

    executor = _build_parameter_fuzzer_governed_write_executor(
        approved_base_url="http://target.test",
        root=tmp_path,
        project="demo",
        runtime_contract={
            "status": "approved",
            "approved_base_url": "http://target.test",
            "environment_ref": "declared-test",
            "environment_type": "test",
            "execution_mode": "approved_sandbox_write",
        },
        campaign_id="camp-1",
        round_number=2,
        documented_routes=[{"method": "GET", "path": "/api/cart/items"}],
        safety_boundary={},
        selected_slice_by_path={"/api/cart/items": {"slice_id": "BHV_cart_qty"}},
    )

    result = executor(
        method="POST",
        path="/api/cart/items",
        body={"sku": "SKU-1", "qty": "-1"},
        route={"source_refs": [{"source_type": "api_document"}]},
        token="actor-token",
    )

    scenario = captured["scenario"]
    assert captured["base_url"] == "http://target.test"
    assert captured["kwargs"]["campaign_id"] == "camp-1"
    assert captured["kwargs"]["documented_routes"] == [{"method": "GET", "path": "/api/cart/items"}]
    assert scenario.actor_token == "actor-token"
    assert scenario.behavior_slice_id == "BHV_cart_qty"
    assert scenario.discovery_round == 2
    assert scenario.steps[0].api_method == "POST"
    assert scenario.steps[0].api_path == "/api/cart/items"
    assert scenario.steps[0].body_template == {"sku": "SKU-1", "qty": "-1"}
    assert result["status"] == 500
    assert result["response"] == {"error": "boom"}
    assert result["audit_path"] == str(tmp_path / "sandbox_write_audit.jsonl")
