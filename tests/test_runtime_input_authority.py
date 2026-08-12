import json
from pathlib import Path

from ai_test_asset_center.runtime_input_authority import (
    derive_auth_flow,
    derive_service_routes,
    load_recursive_openapi,
    parse_document_accounts,
)


def _roles(value: str) -> list[str]:
    return [value.strip().lower().replace(" ", "_")]


def test_plain_username_account_table_is_parsed_by_header_meaning(tmp_path: Path) -> None:
    (tmp_path / "accounts.md").write_text(
        "| tenant | username | purpose | password | role |\n"
        "|---|---|---|---|---|\n"
        "| A | admin | smoke | Admin@123 | ADMIN |\n",
        encoding="utf-8",
    )

    accounts = parse_document_accounts(tmp_path, role_key_resolver=_roles)

    assert accounts["admin"] == {
        "username": "admin",
        "password": "Admin@123",
        "role": "admin",
    }


def test_recursive_openapi_ignores_ordinary_json_and_aggregates_services(tmp_path: Path) -> None:
    (tmp_path / "docs" / "openapi").mkdir(parents=True)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "settings.json").write_text(
        json.dumps({"openapi": "mentioned but not a contract"}), encoding="utf-8"
    )
    for name, path in (("a_service.json", "/a"), ("b_service.json", "/b")):
        (tmp_path / "docs" / "openapi" / name).write_text(
            json.dumps(
                {
                    "openapi": "3.0.3",
                    "info": {"title": name, "version": "1"},
                    "paths": {path: {"get": {"responses": {"200": {"description": "ok"}}}}},
                }
            ),
            encoding="utf-8",
        )

    spec, source = load_recursive_openapi(tmp_path)

    assert source == "multi_openapi:2"
    assert set(spec["paths"]) == {"/a", "/b"}
    assert len(spec["x-qualibug-openapi-sources"]) == 2


def test_auth_flow_resolves_request_body_schema_ref() -> None:
    spec = {
        "openapi": "3.0.3",
        "paths": {
            "/api/v1/auth/login": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/LoginIn"}
                            }
                        }
                    }
                }
            }
        },
        "components": {
            "schemas": {
                "LoginIn": {
                    "type": "object",
                    "properties": {
                        "username": {"type": "string"},
                        "password": {"type": "string"},
                    },
                }
            }
        },
    }

    auth = derive_auth_flow("", spec)

    assert auth["login_path"] == "/api/v1/auth/login"
    assert auth["username_field"] == "username"
    assert auth["password_field"] == "password"


def test_service_routes_use_declared_ports_and_matching_openapi(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "docs" / "openapi").mkdir(parents=True)
    (tmp_path / "config" / "services.json").write_text(
        json.dumps({"orders_service": 8111, "inventory_service": 8120}),
        encoding="utf-8",
    )
    for name, path in (("orders_service.json", "/orders"), ("inventory_service.json", "/inventory")):
        (tmp_path / "docs" / "openapi" / name).write_text(
            json.dumps(
                {
                    "openapi": "3.0.3",
                    "info": {"title": name, "version": "1"},
                    "paths": {path: {"get": {"responses": {"200": {"description": "ok"}}}}},
                }
            ),
            encoding="utf-8",
        )

    services = derive_service_routes(tmp_path, "http://127.0.0.1:8101")

    by_name = {item["name"]: item for item in services}
    assert by_name["orders_service"]["base_url"] == "http://127.0.0.1:8111"
    assert by_name["inventory_service"]["base_url"] == "http://127.0.0.1:8120"
    assert by_name["orders_service"]["openapi_spec"] == "docs/openapi/orders_service.json"
