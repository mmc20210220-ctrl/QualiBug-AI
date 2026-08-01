from __future__ import annotations

import hashlib
import json

import pytest

from ai_test_asset_center.auto_test_data_factory import build_auto_fixture_for_probe
from ai_test_asset_center.enterprise_source_registry import register_source_asset
from ai_test_asset_center.enterprise_test_data_receipts import issue_test_data_receipt, verify_test_data_receipt
from ai_test_asset_center.test_data_receipt_bootstrap import bootstrap_test_data_receipts_for_campaign
from tests.mainline_test_support import authoritative_v12_double


API_SPEC = json.dumps(
    {
        "openapi": "3.0.0",
        "paths": {
            "/api/auth/login": {
                "post": {
                    "summary": "login",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "email": {"type": "string"},
                                        "password": {"type": "string"},
                                    },
                                }
                            }
                        }
                    },
                }
            },
            "/api/orders": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}, "amount": {"type": "number"}},
                                }
                            }
                        }
                    }
                }
            },
            "/api/orders/{order_id}": {"get": {}, "delete": {}},
        },
    }
)

MARKDOWN_API_DOC = """# API 接口文档

### POST /api/orders

请求：

```json
{
  "items":[{"sku":"SKU-PHONE-001","qty":1}],
  "couponCode":"NEW100",
  "addressId":"<address_id>"
}
```

### GET /api/orders/:order_id

### POST /api/orders/:id/cancel
"""

SCHEMA_SQL = """
CREATE TABLE users (
  id UUID PRIMARY KEY
);

CREATE TABLE addresses (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id),
  receiver TEXT NOT NULL,
  phone TEXT NOT NULL,
  province TEXT NOT NULL,
  city TEXT NOT NULL,
  detail TEXT NOT NULL
);

CREATE TABLE orders (
  id UUID PRIMARY KEY,
  address_id UUID REFERENCES addresses(id),
  coupon_code TEXT
);
"""

PRODUCT_API_SPEC = json.dumps(
    {
        "openapi": "3.0.0",
        "paths": {
            "/api/products/admin": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "title": {"type": "string"},
                                        "category": {"type": "string"},
                                        "price": {"type": "number"},
                                        "status": {"type": "string", "enum": ["DRAFT", "ON_SALE", "OFF_SALE"]},
                                    },
                                }
                            }
                        }
                    }
                }
            },
            "/api/products/{sku}": {"get": {}},
            "/api/products/admin/{sku}": {
                "patch": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "status": {"type": "string", "enum": ["DRAFT", "ON_SALE", "OFF_SALE", "DELETED"]},
                                    },
                                }
                            }
                        }
                    }
                }
            },
        },
    }
)


def test_auto_fixture_prefers_markdown_request_example_for_setup_body() -> None:
    bundle = build_auto_fixture_for_probe(
        {
            "candidate_id": "QBBOOT-EXAMPLE",
            "risk_type": "anonymous_auth_boundary_probe",
            "execution_policy": "read_only_safe",
            "endpoint": {"method": "GET", "path": "/api/orders/:order_id"},
            "probe_plan": {"auth_boundary": {"actor": "anonymous"}},
        },
        config={"qualibug_auto_create_test_data": True, "api_doc_text": MARKDOWN_API_DOC},
    )

    setup_body = bundle["setup_requests"][0]["body"]
    assert setup_body["items"] == [{"sku": "SKU-PHONE-001", "qty": 1}]
    assert setup_body["couponCode"] == "NEW100"
    assert setup_body["addressId"] == "<address_id>"
    assert bundle["receipt"]["fixture_setup_blocked_reason"] == (
        "FIXTURE_REQUEST_BODY_PLACEHOLDER_UNRESOLVED:address_id"
    )


def test_auto_fixture_builds_fk_dependency_setup_chain_from_schema(tmp_path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "schema.sql").write_text(SCHEMA_SQL, encoding="utf-8")

    bundle = build_auto_fixture_for_probe(
        {
            "candidate_id": "QBBOOT-FK-CHAIN",
            "risk_type": "anonymous_auth_boundary_probe",
            "execution_policy": "read_only_safe",
            "endpoint": {"method": "GET", "path": "/api/orders/:order_id"},
            "probe_plan": {"auth_boundary": {"actor": "anonymous"}},
        },
        input_dir=input_dir,
        config={"qualibug_auto_create_test_data": True, "api_doc_text": MARKDOWN_API_DOC},
    )

    dependency_setup = bundle["setup_requests"][0]
    order_setup = bundle["setup_requests"][1]
    order_cleanup = bundle["cleanup_requests"][0]
    dependency_cleanup = bundle["cleanup_requests"][1]

    assert dependency_setup["purpose"] == "create_dependency_fixture_addresses"
    assert dependency_setup["path"] == "/api/users/addresses"
    assert dependency_setup["path_candidates"] == ["/api/users/addresses", "/api/addresses", "/users/addresses", "/addresses"]
    assert dependency_setup["bind_response_id_to"] == ["address_id"]
    assert dependency_setup["body"]["receiver"].startswith("qb_auto_receiver_")
    assert dependency_setup["body"]["province"] == "qb_auto_region"
    assert dependency_setup["body"]["city"] == "qb_auto_city"
    assert dependency_setup["body"]["detail"].startswith("qb_auto_detail_")

    placeholder = str(bundle["path_params"]["address_id"])
    assert placeholder.startswith("qb_auto_ref_address_id_")
    assert order_setup["purpose"] == "create_disposable_qb_auto_fixture"
    assert order_setup["body"]["addressId"] == placeholder
    assert len(bundle["cleanup_requests"]) == 2
    assert order_cleanup["purpose"] == "cleanup_qb_auto_fixture"
    assert order_cleanup["method"] == "POST"
    assert order_cleanup["path"] == "/api/orders/{id}/cancel"
    assert dependency_cleanup["purpose"] == "cleanup_dependency_fixture_addresses"
    assert dependency_cleanup["path"] == "/api/users/addresses/{id}"
    assert dependency_cleanup["path_candidates"] == ["/api/users/addresses/{id}", "/api/addresses/{id}", "/users/addresses/{id}", "/addresses/{id}"]
    assert dependency_cleanup["path_params"]["id"] == placeholder


def test_auto_fixture_infers_resource_identity_and_patch_cleanup_for_inventory_probe() -> None:
    bundle = build_auto_fixture_for_probe(
        {
            "candidate_id": "QBBOOT-PRODUCT",
            "risk_type": "anonymous_auth_boundary_probe",
            "execution_policy": "read_only_safe",
            "endpoint": {"method": "GET", "path": "/api/products/{sku}"},
            "probe_plan": {"auth_boundary": {"actor": "anonymous"}},
        },
        config={"qualibug_auto_create_test_data": True, "api_doc_text": PRODUCT_API_SPEC},
    )

    setup = bundle["setup_requests"][0]
    cleanup = bundle["cleanup_requests"][0]

    assert setup["path"] == "/api/products/admin"
    assert str(setup["body"]["sku"]).startswith("qb_auto_sku_")
    assert setup["body"]["status"] in {"DRAFT", "ON_SALE", "OFF_SALE"}
    assert cleanup["method"] == "PATCH"
    assert cleanup["path"] == "/api/products/admin/{sku}"
    assert cleanup["body"] == {"status": "DELETED"}


def test_auto_fixture_merges_structured_and_markdown_api_sources(tmp_path) -> None:
    (tmp_path / "openapi.json").write_text(
        json.dumps(
            {
                "openapi": "3.0.0",
                "paths": {
                    "/api/orders": {"post": {}},
                    "/api/orders/{id}": {"get": {}},
                },
            }
        ),
        encoding="utf-8",
    )
    markdown = """# API\n\n### POST /api/orders\n### GET /api/orders/{id}\n### POST /api/orders/{id}/cancel\n"""

    bundle = build_auto_fixture_for_probe(
        {
            "candidate_id": "QBBOOT-MERGED-SOURCES",
            "risk_type": "anonymous_auth_boundary_probe",
            "execution_policy": "read_only_safe",
            "endpoint": {"method": "GET", "path": "/api/orders/{id}"},
            "probe_plan": {"auth_boundary": {"actor": "anonymous"}},
        },
        input_dir=tmp_path,
        config={"qualibug_auto_create_test_data": True, "api_doc_text": markdown},
    )

    assert bundle["setup_requests"][0]["path"] == "/api/orders"
    assert bundle["cleanup_requests"][0]["method"] == "POST"
    assert bundle["cleanup_requests"][0]["path"] == "/api/orders/{id}/cancel"


def test_auto_fixture_keeps_probe_when_inline_api_document_parser_fails(monkeypatch) -> None:
    from ai_test_asset_center import universal_api_parser

    def fail_inline_document(value):
        raise RuntimeError("malformed-customer-api-document")

    monkeypatch.setattr(universal_api_parser, "parse_to_openapi", fail_inline_document)
    bundle = build_auto_fixture_for_probe(
        {
            "candidate_id": "QBBOOT-PARSE-DEGRADED",
            "risk_type": "anonymous_auth_boundary_probe",
            "execution_policy": "read_only_safe",
            "endpoint": {"method": "GET", "path": "/api/resources/{id}"},
            "probe_plan": {"auth_boundary": {"actor": "anonymous"}},
        },
        config={
            "qualibug_auto_create_test_data": True,
            "api_doc_text": "# API\n\n### GET /api/resources/{id}\n",
        },
    )

    receipt = bundle["receipt"]
    assert receipt["api_document_parse_status"] == "degraded"
    assert receipt["api_document_parse_diagnostics"] == [
        {
            "source": "inline_api_document",
            "code": "API_DOCUMENT_PARSE_FAILED",
            "error_type": "RuntimeError",
        }
    ]
    assert bundle["candidate_id"] == "QBBOOT-PARSE-DEGRADED"
    assert str(bundle["path_params"]["id"]).startswith("qb_auto_")


def test_openapi_input_loader_isolates_one_malformed_source(tmp_path, monkeypatch) -> None:
    from ai_test_asset_center import universal_api_parser
    from ai_test_asset_center.auto_test_data_factory import load_openapi_from_input

    bad = tmp_path / "api_broken.yaml"
    good = tmp_path / "openapi.json"
    bad.write_text("openapi: [broken", encoding="utf-8")
    good.write_text(API_SPEC, encoding="utf-8")
    real_parser = universal_api_parser.parse_to_openapi

    def parse_with_one_failure(value):
        if getattr(value, "name", "") == bad.name:
            raise RuntimeError("source-parser-failed")
        return real_parser(value)

    monkeypatch.setattr(universal_api_parser, "parse_to_openapi", parse_with_one_failure)
    spec = load_openapi_from_input(tmp_path)

    assert "/api/orders" in spec["paths"]
    assert spec["x-qualibug-diagnostics"]["api_source_parse_failures"] == [
        {
            "source": bad.name,
            "code": "API_SOURCE_PARSE_FAILED",
            "error_type": "RuntimeError",
        }
    ]


def test_auto_fixture_never_uses_another_resource_cleanup_route(tmp_path) -> None:
    (tmp_path / "openapi.json").write_text(
        json.dumps(
            {
                "openapi": "3.0.0",
                "paths": {
                    "/api/orders": {"post": {}},
                    "/api/orders/{id}": {"get": {}},
                    "/api/cart/items/{id}": {"delete": {}},
                },
            }
        ),
        encoding="utf-8",
    )

    bundle = build_auto_fixture_for_probe(
        {
            "candidate_id": "QBBOOT-CLEANUP-RESOURCE",
            "risk_type": "anonymous_auth_boundary_probe",
            "execution_policy": "read_only_safe",
            "endpoint": {"method": "GET", "path": "/api/orders/{id}"},
            "probe_plan": {"auth_boundary": {"actor": "anonymous"}},
        },
        input_dir=tmp_path,
        config={"qualibug_auto_create_test_data": True},
    )

    cleanup = bundle["cleanup_requests"][0]
    assert cleanup["method"] == "MANUAL"
    assert cleanup["reason"] == "documented_cleanup_endpoint_missing"
    assert cleanup["path"] == ""


def test_bootstrap_rejects_login_only_fixture_surface_before_authentication(tmp_path, monkeypatch) -> None:
    from ai_test_asset_center import test_data_receipt_bootstrap as bootstrap_module

    monkeypatch.setattr(
        bootstrap_module,
        "_login_control_header_candidates",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("login must not run")),
    )
    result = bootstrap_test_data_receipts_for_campaign(
        project="enterprise-project",
        root=tmp_path,
        base_url="http://sandbox.local",
        api_doc_text=json.dumps(
            {
                "openapi": "3.0.0",
                "paths": {
                    "/api/auth/login": {"post": {}},
                },
            }
        ),
        campaign={"campaign_id": "CMP_LOGIN_ONLY", "scope_id": "scope", "environment_ref": "test"},
        selected_slices=[],
        contract={"strategy": "create_disposable", "write_approved": True},
        environment_kind="test",
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "bootstrap_probe_not_found"


def test_auto_fixture_does_not_cross_resource_surface_for_action_probe() -> None:
    from ai_test_asset_center.auto_test_data_factory import build_auto_fixture_for_probe

    api_doc = """# API

### POST /api/auth/login
```json
{"email":"buyer@example.com","password":"secret"}
```

### POST /api/auth/admin/users/:id/status
```json
{"status":"DISABLED"}
```

### PATCH /api/users/admin/users/:id/balance
```json
{"delta":1,"reason":"adjustment"}
```

### POST /api/users/auth
```json
{"name":"unrelated"}
```
"""
    bundle = build_auto_fixture_for_probe(
        {
            "candidate_id": "QBBOOT-ACTION-SURFACE",
            "risk_type": "conservation_probe",
            "execution_policy": "disposable_sandbox_required",
            "endpoint": {
                "method": "POST",
                "path": "/api/auth/admin/users/{id}/status",
            },
            "probe_plan": {
                "mutation": {
                    "mutation_kind": "bootstrap_disposable_fixture",
                    "field_selector": "resource",
                    "value": 1,
                }
            },
        },
        config={"qualibug_auto_create_test_data": True, "api_doc_text": api_doc},
    )

    assert bundle["setup_requests"] == []
    assert bundle["cleanup_requests"] == []


def test_auto_fixture_generation_errors_are_not_swallowed(monkeypatch) -> None:
    from ai_test_asset_center.grounded_probe_executor import _auto_fixture_bundle

    def fail(*args, **kwargs):
        raise RuntimeError("source-contract-broken")

    monkeypatch.setattr("ai_test_asset_center.auto_test_data_factory.build_auto_fixture_for_probe", fail)
    with pytest.raises(RuntimeError, match="source-contract-broken"):
        _auto_fixture_bundle(
            {"qualibug_auto_create_test_data": True},
            {"candidate_id": "QBBOOT-FAIL-FAST", "endpoint": {"method": "GET", "path": "/api/resources/{id}"}},
        )


def test_bootstrap_receipts_issue_real_creation_and_cleanup_receipts(tmp_path, monkeypatch) -> None:
    from ai_test_asset_center import test_data_receipt_bootstrap as bootstrap_module

    def fake_login(self, email: str = "", password: str = "", login_path: str = "", body_template=None) -> bool:
        self._token = "sandbox-token"
        return bool(email and password and login_path)

    def fake_execute(config: dict, base_url: str, probe: dict, key: str, timeout: float):
        if key == "setup_requests":
            return [{"status": "executed", "accepted": True, "path": "/api/orders", "purpose": "create_disposable_qb_auto_fixture"}]
        return [{"status": "executed", "accepted": True, "path": "/api/orders/srv_1", "purpose": "cleanup_qb_auto_fixture"}]

    monkeypatch.setattr("ai_test_asset_center.parameter_fuzzer.ParameterFuzzer.login", fake_login)
    monkeypatch.setattr(bootstrap_module, "_execute_auto_fixture_requests", fake_execute)
    monkeypatch.setattr(
        "ai_test_asset_center.enterprise_pilot_runtime.load_connector_registry",
        lambda project, root: {
            "test_profile": {
                "test_credentials": {
                    "buyer": {"email": "buyer@example.com", "password": "Test@123456"},
                }
            }
        },
    )

    result = bootstrap_test_data_receipts_for_campaign(
        project="enterprise-project",
        root=tmp_path,
        base_url="http://sandbox.local",
        api_doc_text=API_SPEC,
        campaign={"campaign_id": "CMP_1", "scope_id": "scope-a", "environment_ref": "test-a"},
        selected_slices=[{"slice_id": "slice-1", "endpoints": ["/api/orders/:order_id"]}],
        contract={"strategy": "create_disposable", "write_approved": True, "disposable_scope_ref": "sandbox-a"},
        environment_kind="test",
    )

    creation_ref = str(result["contract"]["creation_receipt_ref"])
    cleanup_ref = str(result["contract"]["cleanup_receipt_ref"])

    assert result["status"] == "ready"
    assert result["probe"]["endpoint"]["path"] == "/api/orders/{order_id}"
    assert verify_test_data_receipt(
        "enterprise-project",
        creation_ref,
        root=tmp_path,
        kind="creation",
        campaign_id="CMP_1",
        scope_id="scope-a",
        environment_ref="test-a",
    )["valid"] is True
    assert verify_test_data_receipt(
        "enterprise-project",
        cleanup_ref,
        root=tmp_path,
        kind="cleanup",
        campaign_id="CMP_1",
        scope_id="scope-a",
        environment_ref="test-a",
    )["valid"] is True


def test_bootstrap_uses_project_test_accounts_when_registry_has_no_credentials(tmp_path) -> None:
    from ai_test_asset_center.test_data_receipt_bootstrap import _test_credentials

    path = tmp_path / "platform_inputs" / "enterprise-project" / "test_accounts.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "qa_buyer": {
                    "email": "buyer@example.test",
                    "password": "secret-ref-value",
                    "role": "buyer",
                }
            }
        ),
        encoding="utf-8",
    )

    credentials = _test_credentials("enterprise-project", tmp_path)

    assert len(credentials) == 1
    assert credentials[0]["profile"] == "qa_buyer"
    assert credentials[0]["email"] == "buyer@example.test"


def test_bootstrap_blocks_production_and_unknown_before_login_or_write(tmp_path, monkeypatch) -> None:
    from ai_test_asset_center import test_data_receipt_bootstrap as bootstrap_module

    monkeypatch.setattr(
        bootstrap_module,
        "_login_control_header_candidates",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("login must not run")),
    )

    common = {
        "project": "enterprise-project",
        "root": tmp_path,
        "base_url": "https://target.example.test",
        "api_doc_text": API_SPEC,
        "selected_slices": [],
        "contract": {"strategy": "create_disposable", "write_approved": True},
    }
    production = bootstrap_test_data_receipts_for_campaign(
        **common,
        campaign={"campaign_id": "CMP_PROD", "scope_id": "scope", "environment_ref": "customer-production"},
        environment_kind="production",
    )
    unknown = bootstrap_test_data_receipts_for_campaign(
        **common,
        campaign={"campaign_id": "CMP_UNKNOWN", "scope_id": "scope", "environment_ref": "customer-primary"},
    )

    assert production["status"] == "blocked"
    assert production["reason"] == "production_environment_blocked"
    assert unknown["status"] == "blocked"
    assert unknown["reason"] == "environment_not_recognized_nonprod"


def test_bootstrap_retries_next_control_account_when_first_fixture_attempt_is_forbidden(tmp_path, monkeypatch) -> None:
    from ai_test_asset_center import test_data_receipt_bootstrap as bootstrap_module

    def fake_login(self, email: str = "", password: str = "", login_path: str = "", body_template=None) -> bool:
        self._token = f"token-for-{email}"
        return bool(email and password and login_path)

    def fake_execute(config: dict, base_url: str, probe: dict, key: str, timeout: float):
        auth = str((config.get("fixture_headers") or {}).get("Authorization") or "")
        if "buyer@example.com" in auth:
            return [{
                "status": "executed",
                "accepted": False,
                "path": "/api/products/admin",
                "purpose": key,
                "response": {"status_code": 403},
            }]
        if key == "setup_requests":
            return [{"status": "executed", "accepted": True, "path": "/api/products/admin", "purpose": "create_disposable_qb_auto_fixture"}]
        return [{"status": "executed", "accepted": True, "path": "/api/products/admin/qb_auto_1", "purpose": "cleanup_qb_auto_fixture"}]

    monkeypatch.setattr("ai_test_asset_center.parameter_fuzzer.ParameterFuzzer.login", fake_login)
    monkeypatch.setattr(bootstrap_module, "_execute_auto_fixture_requests", fake_execute)
    monkeypatch.setattr(
        "ai_test_asset_center.enterprise_pilot_runtime.load_connector_registry",
        lambda project, root: {
            "test_profile": {
                "test_credentials": {
                    "buyer": {"email": "buyer@example.com", "password": "Test@123456"},
                    "admin": {"email": "admin@example.com", "password": "Admin@123456"},
                }
            }
        },
    )

    result = bootstrap_test_data_receipts_for_campaign(
        project="enterprise-project",
        root=tmp_path,
        base_url="http://sandbox.local",
        api_doc_text=API_SPEC,
        campaign={"campaign_id": "CMP_2", "scope_id": "scope-b", "environment_ref": "test-b"},
        selected_slices=[{"slice_id": "slice-1", "endpoints": ["/api/orders/:order_id"]}],
        contract={"strategy": "create_disposable", "write_approved": True, "disposable_scope_ref": "sandbox-b"},
        environment_kind="test",
    )

    assert result["status"] == "ready"
    assert len(result["control_attempts"]) == 2
    assert result["control_attempts"][0]["credential_profile"] == "buyer"
    assert result["control_attempts"][0]["setup_accepted"] is False
    assert result["control_attempts"][1]["credential_profile"] == "admin"
    assert result["control_attempts"][1]["setup_accepted"] is True
    assert result["control_attempts"][1]["cleanup_accepted"] is True


def test_bootstrap_control_account_order_uses_configured_default_without_role_names(tmp_path, monkeypatch) -> None:
    from ai_test_asset_center import test_data_receipt_bootstrap as bootstrap_module

    def fake_login(self, email: str = "", password: str = "", login_path: str = "", body_template=None) -> bool:
        self._token = f"token-for-{email}"
        return bool(email and password and login_path)

    def fake_execute(config: dict, base_url: str, probe: dict, key: str, timeout: float):
        auth = str((config.get("fixture_headers") or {}).get("Authorization") or "")
        if "ops-reader@example.com" in auth:
            return [{
                "status": "executed",
                "accepted": False,
                "path": "/api/assets",
                "purpose": key,
                "response": {"status_code": 403},
            }]
        return [{"status": "executed", "accepted": True, "path": "/api/assets", "purpose": key}]

    monkeypatch.setattr("ai_test_asset_center.parameter_fuzzer.ParameterFuzzer.login", fake_login)
    monkeypatch.setattr(bootstrap_module, "_execute_auto_fixture_requests", fake_execute)
    monkeypatch.setattr(
        "ai_test_asset_center.enterprise_pilot_runtime.load_connector_registry",
        lambda project, root: {
            "test_profile": {
                "test_credentials": {
                    "ops_reader": {"email": "ops-reader@example.com", "password": "Reader@123456"},
                    "portal_operator": {
                        "email": "portal-operator@example.com",
                        "password": "Portal@123456",
                        "default": True,
                    },
                }
            }
        },
    )

    result = bootstrap_test_data_receipts_for_campaign(
        project="enterprise-project",
        root=tmp_path,
        base_url="http://sandbox.local",
        api_doc_text=API_SPEC,
        campaign={"campaign_id": "CMP_3", "scope_id": "scope-c", "environment_ref": "test-c"},
        selected_slices=[{"slice_id": "slice-1", "endpoints": ["/api/orders/:order_id"]}],
        contract={"strategy": "create_disposable", "write_approved": True, "disposable_scope_ref": "sandbox-c"},
        environment_kind="test",
    )

    assert result["status"] == "ready"
    assert result["control_attempts"][0]["credential_profile"] == "portal_operator"


def test_scan_promotes_bootstrapped_contract_into_ready_test_data_plan(tmp_path, monkeypatch) -> None:
    from ai_test_asset_center.__main__ import scan

    register_source_asset("enterprise-project", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)

    def fake_v12_pipeline(**kwargs):
        return {
            "total_duration_ms": 1,
            "findings": [],
            "campaign": {
                "campaign_id": "CMP_SCAN",
                "campaign_status": "active",
                "scope_id": "service-a",
                "environment_ref": "test-a",
                "confirmed_slice_count": 0,
            },
            "phases": {
                "state_graph": {"coverage_gaps": []},
                "execution": {"status": "plan_only"},
                "incremental_discovery": {"selected_slices": []},
            },
            "auto_har": {"status": "no_traffic"},
            "behavior_slice_ledger": {},
        }

    def fake_bootstrap(**kwargs):
        creation = issue_test_data_receipt(
            "enterprise-project",
            root=tmp_path,
            kind="creation",
            campaign_id="CMP_SCAN",
            scope_id="service-a",
            environment_ref="test-a",
            actor={"name": "QualiBug", "role": "sandbox_operator"},
            data_scope_ref="sandbox-a",
        )
        cleanup = issue_test_data_receipt(
            "enterprise-project",
            root=tmp_path,
            kind="cleanup",
            campaign_id="CMP_SCAN",
            scope_id="service-a",
            environment_ref="test-a",
            actor={"name": "QualiBug", "role": "sandbox_operator"},
            operation_ref="cleanup-a",
        )
        return {
            "status": "ready",
            "reason": "bootstrap_receipts_issued",
            "contract": {
                "strategy": "create_disposable",
                "write_approved": True,
                "campaign_id": "CMP_SCAN",
                "scope_id": "service-a",
                "environment_ref": "test-a",
                "disposable_scope_ref": "sandbox-a",
                "creation_receipt_ref": creation["receipt_id"],
                "cleanup_receipt_ref": cleanup["receipt_id"],
            },
        }

    monkeypatch.setattr(
        "ai_test_asset_center.v12_pipeline.run_v12_pipeline",
        authoritative_v12_double(fake_v12_pipeline),
    )
    monkeypatch.setattr("ai_test_asset_center.__main__.bootstrap_test_data_receipts_for_campaign", fake_bootstrap)

    result = scan(
        project="enterprise-project",
        root=tmp_path,
        api_doc_text=API_SPEC,
        base_url="http://sandbox.local",
        campaign_context={
            "scope_id": "service-a",
            "environment_ref": "test-a",
            "environment_type": "test",
            "test_data_contract": {
                "strategy": "create_disposable",
                "write_approved": True,
                "disposable_scope_ref": "sandbox-a",
            },
            "source_manifest": {
                "source_id": "api-contract",
                "source_hash": hashlib.sha256(API_SPEC.encode("utf-8")).hexdigest(),
            },
        },
    )

    assert result["test_data_bootstrap"]["status"] == "ready"
    assert result["test_data_plan"]["status"] == "ready"
    assert result["test_data_plan"]["receipt_validation"] == "verified"


def test_scan_infers_create_disposable_contract_before_bootstrap(tmp_path, monkeypatch) -> None:
    from ai_test_asset_center.__main__ import scan

    register_source_asset("enterprise-project", "api-contract", API_SPEC, source_type="openapi", root=tmp_path)
    observed: dict[str, object] = {}

    def fake_v12_pipeline(**kwargs):
        return {
            "total_duration_ms": 1,
            "findings": [],
            "campaign": {
                "campaign_id": "CMP_SCAN",
                "campaign_status": "active",
                "scope_id": "service-a",
                "environment_ref": "test-a",
                "confirmed_slice_count": 0,
            },
            "phases": {
                "state_graph": {"coverage_gaps": []},
                "execution": {"status": "plan_only"},
                "incremental_discovery": {"selected_slices": []},
            },
            "auto_har": {"status": "no_traffic"},
            "behavior_slice_ledger": {},
        }

    def fake_bootstrap(**kwargs):
        observed["contract"] = dict(kwargs.get("contract") or {})
        creation = issue_test_data_receipt(
            "enterprise-project",
            root=tmp_path,
            kind="creation",
            campaign_id="CMP_SCAN",
            scope_id="service-a",
            environment_ref="test-a",
            actor={"name": "QualiBug", "role": "sandbox_operator"},
            data_scope_ref="service-a",
        )
        cleanup = issue_test_data_receipt(
            "enterprise-project",
            root=tmp_path,
            kind="cleanup",
            campaign_id="CMP_SCAN",
            scope_id="service-a",
            environment_ref="test-a",
            actor={"name": "QualiBug", "role": "sandbox_operator"},
            operation_ref="cleanup-a",
        )
        return {
            "status": "ready",
            "reason": "bootstrap_receipts_issued",
            "contract": {
                "strategy": "create_disposable",
                "write_approved": True,
                "campaign_id": "CMP_SCAN",
                "scope_id": "service-a",
                "environment_ref": "test-a",
                "disposable_scope_ref": "service-a",
                "creation_receipt_ref": creation["receipt_id"],
                "cleanup_receipt_ref": cleanup["receipt_id"],
            },
        }

    monkeypatch.setattr(
        "ai_test_asset_center.v12_pipeline.run_v12_pipeline",
        authoritative_v12_double(fake_v12_pipeline),
    )
    monkeypatch.setattr("ai_test_asset_center.__main__.bootstrap_test_data_receipts_for_campaign", fake_bootstrap)

    result = scan(
        project="enterprise-project",
        root=tmp_path,
        api_doc_text=API_SPEC,
        base_url="http://sandbox.local",
        campaign_context={
            "scope_id": "service-a",
            "environment_ref": "test-a",
            "environment_type": "test",
            "source_manifest": {
                "source_id": "api-contract",
                "source_hash": hashlib.sha256(API_SPEC.encode("utf-8")).hexdigest(),
            },
        },
    )

    assert observed["contract"] == {
        "strategy": "create_disposable",
        "write_approved": True,
        "disposable_scope_ref": "service-a",
    }
    assert result["test_data_bootstrap"]["status"] == "ready"
    assert result["test_data_plan"]["status"] == "ready"
