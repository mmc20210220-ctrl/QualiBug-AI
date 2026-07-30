from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center import composition
from ai_test_asset_center.enterprise_knowledge_center.api_artifact_asset_projection import (
    API_ARTIFACT_ASSET_PROJECTION_SCHEMA,
    enrich_asset_with_api_artifact_semantics,
)


def _operation_block(
    *,
    block_id: str,
    source_id: str,
    method: str,
    path: str,
    pointer: str,
    node_kind: str = "OPENAPI_OPERATION",
    status: int | None = None,
    elapsed_ms: int | None = None,
) -> dict:
    row = {
        "block_id": block_id,
        "type": "KEY_VALUE",
        "node_kind": node_kind,
        "source_id": source_id,
        "source_hash": "a" * 64,
        "http_method": method,
        "api_path": path,
        "json_pointer": pointer,
        "source_locator": f"api.json#json-pointer={pointer}",
        "text": f"{method} {path}",
        "evidence_address": {
            "source_id": source_id,
            "source_hash": "a" * 64,
            "source_locator": f"api.json#json-pointer={pointer}",
            "address_kind": "EXACT_SOURCE_LOCATOR",
        },
    }
    if status is not None:
        row["response_status"] = status
    if elapsed_ms is not None:
        row["elapsed_ms"] = elapsed_ms
    return row


def test_openapi_interface_receives_exact_operation_and_child_declarations() -> None:
    operation = _operation_block(
        block_id="op_1",
        source_id="src_openapi",
        method="GET",
        path="/orders/{id}",
        pointer="/paths/~1orders~1{id}/get",
    )
    parameter = {
        "block_id": "param_1",
        "parent_id": "op_1",
        "type": "KEY_VALUE",
        "node_kind": "OPENAPI_PARAMETER",
        "source_id": "src_openapi",
        "source_hash": "a" * 64,
        "json_pointer": "/paths/~1orders~1{id}/get/parameters/0",
        "source_locator": "api.json#json-pointer=/paths/~1orders~1{id}/get/parameters/0",
        "parameter_name": "id",
        "parameter_location": "path",
        "required": True,
        "text": "path id",
    }
    asset = {
        "source_inventory": [
            {"source_id": "src_openapi", "source_type": "openapi"}
        ],
        "interfaces": [
            {
                "interface_id": "iface_1",
                "source_id": "src_openapi",
                "source_kind": "openapi",
                "method": "GET",
                "path": "/orders/{id}",
            }
        ],
    }
    sources = [
        {
            "source_id": "src_openapi",
            "document_structure": {
                "artifact_structure": {"artifact_kind": "openapi"},
                "blocks": [operation, parameter],
            },
        }
    ]

    result = enrich_asset_with_api_artifact_semantics(asset, sources)

    interface = result["interfaces"][0]
    assert interface["json_pointer"] == "/paths/~1orders~1{id}/get"
    assert interface["source_locator"].endswith(
        "#json-pointer=/paths/~1orders~1{id}/get"
    )
    assert interface["document_ir_block_id"] == "op_1"
    declarations = interface["technical_declarations"]
    assert declarations[0]["node_kind"] == "OPENAPI_PARAMETER"
    assert declarations[0]["parameter_name"] == "id"
    assert declarations[0]["credential_values_retained"] is False
    receipt = result["api_artifact_semantic_projection"]
    assert receipt["schema"] == API_ARTIFACT_ASSET_PROJECTION_SCHEMA
    assert receipt["status"] == "COMPLETE"
    assert receipt["exact_pointer_interface_count"] == 1


def test_identical_method_paths_from_different_sources_keep_separate_evidence() -> None:
    asset = {
        "source_inventory": [
            {"source_id": "src_a", "source_type": "openapi"},
            {"source_id": "src_b", "source_type": "openapi"},
        ],
        "interfaces": [
            {
                "interface_id": "iface_a",
                "source_id": "src_a",
                "source_kind": "openapi",
                "method": "POST",
                "path": "/orders",
            },
            {
                "interface_id": "iface_b",
                "source_id": "src_b",
                "source_kind": "openapi",
                "method": "POST",
                "path": "/orders",
            },
        ],
    }
    sources = [
        {
            "source_id": "src_a",
            "document_structure": {
                "artifact_structure": {"artifact_kind": "openapi"},
                "blocks": [
                    _operation_block(
                        block_id="op_a",
                        source_id="src_a",
                        method="POST",
                        path="/orders",
                        pointer="/paths/~1orders/post",
                    )
                ],
            },
        },
        {
            "source_id": "src_b",
            "document_structure": {
                "artifact_structure": {"artifact_kind": "openapi"},
                "blocks": [
                    _operation_block(
                        block_id="op_b",
                        source_id="src_b",
                        method="POST",
                        path="/orders",
                        pointer="/paths/~1orders/post",
                    )
                ],
            },
        },
    ]

    result = enrich_asset_with_api_artifact_semantics(asset, sources)

    by_source = {row["source_id"]: row for row in result["interfaces"]}
    assert by_source["src_a"]["document_ir_block_id"] == "op_a"
    assert by_source["src_b"]["document_ir_block_id"] == "op_b"
    assert by_source["src_a"]["source_locator"] != by_source["src_b"][
        "source_locator"
    ] or by_source["src_a"]["document_ir_block_id"] != by_source["src_b"][
        "document_ir_block_id"
    ]
    assert result["api_artifact_semantic_projection"]["processed_source_count"] == 2


def test_repeated_har_entries_are_preserved_as_runtime_observation_set() -> None:
    asset = {
        "source_inventory": [{"source_id": "src_har", "source_type": "har"}],
        "interfaces": [
            {
                "interface_id": "iface_har",
                "source_id": "src_har",
                "source": "har_traffic",
                "method": "GET",
                "path": "/orders",
            }
        ],
    }
    sources = [
        {
            "source_id": "src_har",
            "document_structure": {
                "artifact_structure": {"artifact_kind": "har"},
                "blocks": [
                    _operation_block(
                        block_id="har_1",
                        source_id="src_har",
                        method="GET",
                        path="/orders",
                        pointer="/log/entries/0",
                        node_kind="HAR_ENTRY",
                        status=200,
                        elapsed_ms=40,
                    ),
                    _operation_block(
                        block_id="har_2",
                        source_id="src_har",
                        method="GET",
                        path="/orders",
                        pointer="/log/entries/1",
                        node_kind="HAR_ENTRY",
                        status=500,
                        elapsed_ms=900,
                    ),
                ],
            },
        }
    ]

    result = enrich_asset_with_api_artifact_semantics(asset, sources)

    interface = result["interfaces"][0]
    assert interface["runtime_observation_count"] == 2
    assert interface["json_pointers"] == ["/log/entries/0", "/log/entries/1"]
    assert interface["runtime_observation_statuses"] == ["200", "500"]
    assert "json_pointer" not in interface
    assert interface["contract_authority"] == "runtime_observation_only"
    assert interface["design_contract_inference_allowed"] is False
    receipt = result["api_artifact_semantic_projection"]
    assert receipt["har_runtime_observation_count"] == 2
    assert receipt["har_observation_set_count"] == 1
    assert result["governance"]["har_repeated_observations_are_not_collapsed"] is True


def test_explicit_composition_projects_api_before_enterprise_understanding(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    asset = {
        "source_inventory": [],
        "interfaces": [],
        "relationships": [],
        "summary": {},
        "governance": {},
    }

    monkeypatch.setattr(composition, "configure_source_parser_extensions", lambda: None)
    monkeypatch.setattr(
        composition._base_api,
        "build_enterprise_business_knowledge_asset",
        lambda project, root, options: dict(asset),
    )
    monkeypatch.setattr(
        composition,
        "_parsed_sources_for_context",
        lambda asset, root: [],
    )

    def project_api(value, sources):
        calls.append("api")
        return value

    def understand(value, *, parsed_sources):
        calls.append("understanding")
        value["enterprise_comprehension_gate"] = {"entry_allowed": True}
        return value

    monkeypatch.setattr(composition, "enrich_asset_with_api_artifact_semantics", project_api)
    monkeypatch.setattr(composition, "enrich_asset_with_enterprise_understanding", understand)
    monkeypatch.setattr(
        composition._downstream,
        "refresh_chinese_business_downstream",
        lambda value, max_probe_count: (calls.append("downstream") or value, []),
    )
    monkeypatch.setattr(
        composition,
        "enrich_job_assets_with_governance",
        lambda value, **kwargs: calls.append("jobs") or value,
    )
    monkeypatch.setattr(
        composition,
        "refresh_job_behavior_projection",
        lambda value: calls.append("behavior") or value,
    )
    monkeypatch.setattr(composition, "probe_generation_block_reason", lambda value: "")
    monkeypatch.setattr(composition, "build_gated_probes", lambda *args, **kwargs: [])
    monkeypatch.setattr(composition, "_persist_final", lambda *args, **kwargs: None)

    result = composition.build_enterprise_business_knowledge_asset(
        "api_projection_order",
        tmp_path,
        {"probe_limit": 0},
    )

    assert calls[:2] == ["api", "understanding"]
    assert calls == ["api", "understanding", "downstream", "jobs", "behavior"]
    assert result["governance"][
        "api_artifact_projection_precedes_enterprise_understanding"
    ] is True
