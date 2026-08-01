from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

import ai_test_asset_center.openapi_connector_adapter as openapi
from ai_test_asset_center.connector_sync_authority import register_connector_instance
from ai_test_asset_center.connector_auto_sync_core import run_managed_connector_sync
from ai_test_asset_center.connector_configuration_service import configure_managed_connector
from ai_test_asset_center.enterprise_knowledge_center import list_enterprise_knowledge_sources


BASE = "https://api.example.com"
PROJECT = "openapi-project"
CONNECTOR = "api-contract-main"
ACTOR = {"name": "qa-owner", "role": "qa_lead"}


def _scope(**overrides: object) -> str:
    value: dict[str, object] = {
        "document_urls": [f"{BASE}/openapi.json"],
        "max_documents": 10,
        "max_ref_depth": 3,
    }
    value.update(overrides)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _root_body() -> bytes:
    return json.dumps(
        {
            "openapi": "3.0.3",
            "info": {"title": "Orders API", "version": "2026.08"},
            "paths": {
                "/orders": {
                    "get": {
                        "operationId": "listOrders",
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
            "components": {
                "schemas": {
                    "Order": {"$ref": "models.yaml#/components/schemas/Order"}
                }
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()


def _responses(model_body: bytes | None = None) -> dict[str, openapi.OpenApiHttpResponse]:
    return {
        "/openapi.json": openapi.OpenApiHttpResponse(
            200,
            {"Content-Type": "application/json", "ETag": '"root-v1"'},
            _root_body(),
            f"{BASE}/openapi.json",
        ),
        "/models.yaml": openapi.OpenApiHttpResponse(
            200,
            {"Content-Type": "application/yaml", "ETag": '"models-v1"'},
            model_body
            or b"components:\n  schemas:\n    Order:\n      type: object\n      properties:\n        id:\n          type: string\n",
            f"{BASE}/models.yaml",
        ),
    }


def _transport_for(
    responses: dict[str, openapi.OpenApiHttpResponse],
    calls: list[tuple[str, str, dict[str, str], bytes | None]],
):
    def transport(method, url, headers, body, timeout, max_bytes):
        del timeout, max_bytes
        calls.append((method, url, dict(headers), body))
        path = urlsplit(url).path
        response = responses.get(path, openapi.OpenApiHttpResponse(404, {}, b"", url))
        etag = response.headers.get("ETag")
        if etag and headers.get("If-None-Match") == etag:
            return openapi.OpenApiHttpResponse(304, dict(response.headers), b"", url)
        return response

    return transport


@pytest.fixture
def bypass_test_dns(monkeypatch):
    monkeypatch.setattr(openapi, "validate_url", lambda url, **_: url)


def test_manifest_is_generic_read_only_and_registered_without_network() -> None:
    manifest = openapi.openapi_connector_manifest()

    assert manifest.connector_type == "openapi"
    assert manifest.category == "api_contract"
    assert manifest.read_only is True
    assert manifest.scope_schema["required"] == ["document_urls"]
    assert set(manifest.auth_modes) == {
        "anonymous",
        "bearer_token",
        "api_key",
        "cookie_session",
    }
    assert {field.name for field in manifest.credential_fields_for_auth_mode("api_key")} == {
        "header_name",
        "api_key",
    }


def test_discovery_is_read_only_and_follows_external_refs_with_exact_relationships(
    bypass_test_dns,
) -> None:
    calls: list[tuple[str, str, dict[str, str], bytes | None]] = []
    result = openapi.OpenApiConnectorAdapter().discover(
        {
            "resource_scope": _scope(),
            "transport": _transport_for(_responses(), calls),
            "sleeper": lambda _: None,
        }
    )

    assert result["complete"] is True
    assert len(result["descriptors"]) == 2
    root = next(row for row in result["descriptors"] if row["remote_resource_id"].endswith("openapi.json"))
    assert root["openapi_version"] == "3.0.3"
    assert root["openapi_info_version"] == "2026.08"
    relationships = json.loads(root["source_relationships_json"])
    assert relationships[0]["target_url"] == f"{BASE}/models.yaml"
    assert relationships[0]["source_pointer"].endswith("/Order/$ref")
    assert all(method == "GET" and body is None for method, _, _, body in calls)


def test_authentication_headers_are_transport_only_and_not_in_discovery_metadata(
    bypass_test_dns,
) -> None:
    calls: list[tuple[str, str, dict[str, str], bytes | None]] = []
    result = openapi.OpenApiConnectorAdapter().discover(
        {
            "resource_scope": _scope(resolve_refs=False),
            "connection_profile": {
                "auth_mode": "bearer_token",
                "token": "transport-secret-token",
            },
            "transport": _transport_for(
                {
                    "/openapi.json": openapi.OpenApiHttpResponse(
                        200,
                        {"Content-Type": "application/json"},
                        json.dumps({"openapi": "3.0.0", "info": {"title": "API"}, "paths": {}}).encode(),
                        f"{BASE}/openapi.json",
                    )
                },
                calls,
            ),
        }
    )

    assert calls[0][2]["Authorization"] == "Bearer transport-secret-token"
    assert all("transport-secret-token" not in repr(row) for row in result["descriptors"])


def test_ssrf_block_is_visible_and_stops_transport_before_any_request(monkeypatch) -> None:
    def reject(url: str, **_: object):
        raise openapi.SsrfBlockedError(url)

    monkeypatch.setattr(openapi, "validate_url", reject)
    calls: list[str] = []
    result = openapi.OpenApiConnectorAdapter().discover(
        {
            "resource_scope": _scope(document_urls=["http://127.0.0.1:8088/openapi.json"]),
            "transport": lambda method, url, headers, body, timeout, max_bytes: calls.append(url),
        }
    )

    assert result["descriptors"] == []
    assert calls == []
    assert result["complete"] is False
    assert result["coverage"]["observations"][0]["metadata"]["error_code"] == "openapi_ssrf_blocked"


def test_credential_query_parameters_are_rejected_instead_of_persisted() -> None:
    with pytest.raises(openapi.OpenApiConnectorError, match="query_credential_forbidden"):
        openapi.OpenApiConnectorAdapter().discover(
            {"resource_scope": _scope(document_urls=[f"{BASE}/openapi.json?access_token=secret"])}
        )


def test_incremental_etag_304_reuses_root_and_external_ref_snapshots(
    bypass_test_dns,
    tmp_path: Path,
) -> None:
    register_connector_instance(
        PROJECT,
        root=tmp_path,
        actor=ACTOR,
        connector_instance_id=CONNECTOR,
        connector_type="openapi",
        resource_scope=_scope(),
    )
    responses = _responses()
    calls: list[tuple[str, str, dict[str, str], bytes | None]] = []
    transport = _transport_for(responses, calls)

    first = openapi.sync_openapi_connector(
        PROJECT,
        connector_instance_id=CONNECTOR,
        root=tmp_path,
        actor=ACTOR,
        transport=transport,
        sleeper=lambda _: None,
    )
    second = openapi.sync_openapi_connector(
        PROJECT,
        connector_instance_id=CONNECTOR,
        root=tmp_path,
        actor=ACTOR,
        previous_cursor=first["next_cursor"],
        transport=transport,
        sleeper=lambda _: None,
    )

    assert first["status"] == "COMPLETE"
    assert first["materialized_resource_count"] == 2
    assert second["status"] == "COMPLETE"
    assert second["materialized_resource_count"] == 0
    assert second["unchanged_resource_count"] == 2
    assert sum(1 for _, _, headers, _ in calls if headers.get("If-None-Match")) == 2
    assert list_enterprise_knowledge_sources(PROJECT, root=tmp_path)["summary"]["active_source_count"] == 2


def test_external_ref_change_refreshes_parent_materialization_and_source_occurrence(
    bypass_test_dns,
    tmp_path: Path,
) -> None:
    register_connector_instance(
        PROJECT + "-refresh",
        root=tmp_path,
        actor=ACTOR,
        connector_instance_id=CONNECTOR,
        connector_type="openapi",
        resource_scope=_scope(),
    )
    state = {"changed": False}
    calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def transport(method, url, headers, body, timeout, max_bytes):
        del method, body, timeout, max_bytes
        calls.append(("GET", url, dict(headers), None))
        path = urlsplit(url).path
        if path == "/openapi.json":
            response = _responses()[path]
        elif state["changed"]:
            response = openapi.OpenApiHttpResponse(
                200,
                {"Content-Type": "application/yaml", "ETag": '"models-v2"'},
                b"components:\n  schemas:\n    Order:\n      type: object\n      properties:\n        id:\n          type: integer\n",
                f"{BASE}/models.yaml",
            )
        else:
            response = _responses()[path]
        if headers.get("If-None-Match") == response.headers.get("ETag"):
            return openapi.OpenApiHttpResponse(304, dict(response.headers), b"", url)
        return response

    project = PROJECT + "-refresh"
    first = openapi.sync_openapi_connector(
        project,
        connector_instance_id=CONNECTOR,
        root=tmp_path,
        actor=ACTOR,
        transport=transport,
        sleeper=lambda _: None,
    )
    state["changed"] = True
    second = openapi.sync_openapi_connector(
        project,
        connector_instance_id=CONNECTOR,
        root=tmp_path,
        actor=ACTOR,
        previous_cursor=first["next_cursor"],
        transport=transport,
        sleeper=lambda _: None,
    )

    assert second["status"] == "COMPLETE"
    assert second["materialized_resource_count"] == 2
    assert sum(1 for _, url, headers, _ in calls if url.endswith("/openapi.json") and not headers.get("If-None-Match")) == 2


def test_openapi_adapter_runs_through_managed_dispatcher_and_checkpoint_protocol(
    bypass_test_dns,
    tmp_path: Path,
) -> None:
    project = PROJECT + "-managed"
    configure_managed_connector(
        project,
        connector_type="openapi",
        connector_instance_id=CONNECTOR,
        resource_scope=_scope(resolve_refs=False),
        profile={"auth_mode": "anonymous"},
        root=tmp_path,
        actor=ACTOR,
        display_name="API contract",
    )
    calls: list[tuple[str, str, dict[str, str], bytes | None]] = []
    result = run_managed_connector_sync(
        project,
        CONNECTOR,
        root=tmp_path,
        actor=ACTOR,
        transport=_transport_for(
            {
                "/openapi.json": openapi.OpenApiHttpResponse(
                    200,
                    {"Content-Type": "application/json"},
                    json.dumps(
                        {
                            "openapi": "3.0.0",
                            "info": {"title": "API"},
                            "paths": {"/health": {"get": {}}},
                        }
                    ).encode(),
                    f"{BASE}/openapi.json",
                )
            },
            calls,
        ),
        sleeper=lambda _: None,
    )

    assert result["status"] == "COMPLETE"
    assert result["adapter"] == "openapi"
    assert result["checkpoint_commit_protocol"] == "RECOVERABLE_TWO_STAGE"
    assert all(method == "GET" for method, _, _, _ in calls)
