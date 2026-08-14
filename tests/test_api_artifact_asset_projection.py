from __future__ import annotations

import json

from ai_test_asset_center.enterprise_knowledge_center import composition
from ai_test_asset_center.enterprise_knowledge_center.api_artifact_asset_projection import (
    API_ARTIFACT_ASSET_PROJECTION_SCHEMA,
    API_ARTIFACT_CONTRACT_CONFLICT_SCHEMA,
    API_ARTIFACT_SOURCE_RECORD_SCHEMA,
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
        "source_locator": f"{source_id}.json#json-pointer={pointer}",
        "text": f"{method} {path}",
        "evidence_address": {
            "source_id": source_id,
            "source_hash": "a" * 64,
            "source_locator": f"{source_id}.json#json-pointer={pointer}",
            "address_kind": "EXACT_SOURCE_LOCATOR",
        },
    }
    if status is not None:
        row["response_status"] = status
    if elapsed_ms is not None:
        row["elapsed_ms"] = elapsed_ms
    return row


def _openapi_payload(*, parameter_required: bool = True) -> dict:
    return {
        "openapi": "3.0.3",
        "info": {"title": "Orders", "version": "1"},
        "paths": {
            "/orders": {
                "post": {
                    "operationId": "createOrder",
                    "parameters": [
                        {
                            "name": "mode",
                            "in": "query",
                            "required": parameter_required,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"201": {"description": "Created"}},
                }
            }
        },
    }


def _openapi_source(
    source_id: str,
    *,
    block_id: str,
    parameter_required: bool = True,
) -> dict:
    return {
        "source_id": source_id,
        "document_structure": {
            "artifact_structure": {"artifact_kind": "openapi"},
            "plain_text": json.dumps(
                _openapi_payload(parameter_required=parameter_required),
                ensure_ascii=False,
            ),
            "blocks": [
                _operation_block(
                    block_id=block_id,
                    source_id=source_id,
                    method="POST",
                    path="/orders",
                    pointer="/paths/~1orders/post",
                )
            ],
        },
    }


def test_openapi_interface_receives_one_exact_source_record_and_child_declarations() -> None:
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
        "source_locator": (
            "src_openapi.json#json-pointer=/paths/~1orders~1{id}/get/parameters/0"
        ),
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

    assert len(result["interfaces"]) == 1
    interface = result["interfaces"][0]
    assert interface["json_pointer"] == "/paths/~1orders~1{id}/get"
    assert interface["source_locator"].endswith(
        "#json-pointer=/paths/~1orders~1{id}/get"
    )
    assert interface["document_ir_block_id"] == "op_1"
    assert interface["evidence_scope"] == "SINGLE_SOURCE_DECLARATION"
    assert interface["canonical_contract_source_id"] == "src_openapi"
    records = interface["api_artifact_source_records"]
    assert len(records) == 1
    record = records[0]
    assert record["schema"] == API_ARTIFACT_SOURCE_RECORD_SCHEMA
    assert record["source_id"] == "src_openapi"
    assert record["json_pointer"] == "/paths/~1orders~1{id}/get"
    declarations = record["technical_declarations"]
    assert declarations[0]["node_kind"] == "OPENAPI_PARAMETER"
    assert declarations[0]["parameter_name"] == "id"
    assert declarations[0]["credential_values_retained"] is False
    receipt = result["api_artifact_semantic_projection"]
    assert receipt["schema"] == API_ARTIFACT_ASSET_PROJECTION_SCHEMA
    assert receipt["status"] == "COMPLETE"
    assert receipt["source_record_count"] == 1
    assert receipt["exact_pointer_source_record_count"] == 1


def test_identical_interface_from_two_sources_uses_one_logical_interface_and_two_records() -> None:
    asset = {
        "source_inventory": [
            {"source_id": "src_a", "source_type": "openapi"},
            {"source_id": "src_b", "source_type": "openapi"},
        ],
        "interfaces": [
            {
                "interface_id": "api:POST:/orders",
                "method": "POST",
                "path": "/orders",
            }
        ],
    }
    sources = [
        _openapi_source("src_a", block_id="op_a"),
        _openapi_source("src_b", block_id="op_b"),
    ]

    result = enrich_asset_with_api_artifact_semantics(asset, sources)

    assert len(result["interfaces"]) == 1
    interface = result["interfaces"][0]
    assert interface["interface_id"] == "api:POST:/orders"
    assert interface["source_ids"] == ["src_a", "src_b"]
    assert interface["evidence_scope"] == "MULTI_SOURCE_DECLARATION_LEDGER"
    assert interface["single_source_evidence_claim_forbidden"] is True
    assert "source_id" not in interface
    assert "json_pointer" not in interface
    records = interface["api_artifact_source_records"]
    assert len(records) == 2
    assert {row["source_id"] for row in records} == {"src_a", "src_b"}
    assert {row["document_ir_block_id"] for row in records} == {"op_a", "op_b"}
    assert all(row["json_pointer"] == "/paths/~1orders/post" for row in records)
    assert interface["source_contract_alignment"] == "AGREED_OR_SINGLE_SOURCE"
    receipt = result["api_artifact_semantic_projection"]
    assert receipt["processed_source_count"] == 2
    assert receipt["source_record_count"] == 2
    assert receipt["contract_conflict_candidate_count"] == 0
    assert result["governance"][
        "multi_source_api_evidence_uses_source_record_ledger"
    ] is True


def test_conflicting_openapi_declarations_create_candidate_without_automatic_winner() -> None:
    asset = {
        "source_inventory": [
            {"source_id": "src_old", "source_type": "openapi"},
            {"source_id": "src_new", "source_type": "openapi"},
        ],
        "interfaces": [
            {
                "interface_id": "api:POST:/orders",
                "method": "POST",
                "path": "/orders",
            }
        ],
    }
    sources = [
        _openapi_source(
            "src_old",
            block_id="op_old",
            parameter_required=False,
        ),
        _openapi_source(
            "src_new",
            block_id="op_new",
            parameter_required=True,
        ),
    ]

    result = enrich_asset_with_api_artifact_semantics(asset, sources)

    interface = result["interfaces"][0]
    assert interface["source_contract_alignment"] == "CONFLICT_CANDIDATE"
    assert interface["source_contract_conflict_candidate"] is True
    assert interface["automatic_contract_authority_selected"] is False
    conflicts = result["api_artifact_contract_conflicts"]
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict["schema"] == API_ARTIFACT_CONTRACT_CONFLICT_SCHEMA
    assert conflict["source_ids"] == ["src_new", "src_old"]
    assert len(conflict["contract_fingerprints"]) == 2
    assert conflict["automatic_winner_selected"] is False
    assert result["api_artifact_semantic_projection"]["status"] == "PARTIAL"
    assert result["api_artifact_semantic_projection"][
        "contract_conflict_candidate_count"
    ] == 1
    assert result["governance"][
        "api_contract_conflict_winner_requires_authority_decision"
    ] is True


def test_repeated_har_entries_remain_one_source_runtime_observation_set() -> None:
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
    assert interface["evidence_scope"] == "SINGLE_SOURCE_RUNTIME_OBSERVATION_SET"
    assert interface["contract_authority"] == "runtime_observation_only"
    assert interface["design_contract_inference_allowed"] is False
    records = interface["api_artifact_source_records"]
    assert len(records) == 1
    assert len(records[0]["runtime_observations"]) == 2
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
        lambda project, root, options, **kwargs: dict(asset),
    )
    monkeypatch.setattr(
        composition,
        "_parsed_sources_for_context",
        lambda asset, root, **kwargs: [],
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
    assert calls == ["api", "understanding", "understanding", "downstream", "jobs", "behavior"]
    assert result["governance"][
        "api_artifact_projection_precedes_enterprise_understanding"
    ] is True
