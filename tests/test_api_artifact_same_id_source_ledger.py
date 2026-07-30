from __future__ import annotations

import json

from ai_test_asset_center.enterprise_knowledge_center.api_artifact_asset_projection import (
    API_ARTIFACT_CONTRACT_CONFLICT_SCHEMA,
    API_ARTIFACT_SOURCE_RECORD_SCHEMA,
    enrich_asset_with_api_artifact_semantics,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion import (
    build_document_structure_ir,
)


def _openapi(*, status: str, description: str) -> bytes:
    return json.dumps(
        {
            "openapi": "3.0.3",
            "info": {"title": "Orders", "version": "1"},
            "paths": {
                "/orders": {
                    "get": {
                        "operationId": "listOrders",
                        "summary": "List orders",
                        "parameters": [
                            {
                                "name": "limit",
                                "in": "query",
                                "required": False,
                                "schema": {"type": "integer", "minimum": 1},
                            }
                        ],
                        "responses": {status: {"description": description}},
                    }
                }
            },
        }
    ).encode()


def _structure(blob: bytes, filename: str, source_id: str) -> dict:
    return build_document_structure_ir(
        blob,
        filename=filename,
        source_id=source_id,
    )


def _asset() -> dict:
    return {
        "source_inventory": [
            {"source_id": "src_a", "source_type": "openapi", "status": "active"},
            {"source_id": "src_b", "source_type": "openapi", "status": "active"},
        ],
        # This reproduces the real base compiler: _openapi_operations generates the
        # same interface_id for both sources, then global identity dedupe retains one.
        "interfaces": [
            {
                "interface_id": "api:GET:/orders",
                "source_id": "src_a",
                "source_kind": "openapi",
                "method": "GET",
                "path": "/orders",
                "operation_id": "listOrders",
                "summary": "List orders",
            }
        ],
        "summary": {},
        "governance": {},
    }


def test_real_same_interface_id_retains_both_source_declarations() -> None:
    sources = [
        {
            "source_id": "src_a",
            "document_structure": _structure(
                _openapi(status="200", description="OK"),
                "orders-v1.openapi.json",
                "src_a",
            ),
        },
        {
            "source_id": "src_b",
            "document_structure": _structure(
                _openapi(status="404", description="Not found"),
                "orders-v2.openapi.json",
                "src_b",
            ),
        },
    ]

    result = enrich_asset_with_api_artifact_semantics(_asset(), sources)

    assert len(result["interfaces"]) == 1
    interface = result["interfaces"][0]
    assert interface["interface_id"] == "api:GET:/orders"
    assert interface["api_artifact_source_record_count"] == 2
    assert interface["source_ids"] == ["src_a", "src_b"]
    assert interface["evidence_scope"] == "MULTI_SOURCE_DECLARATION_LEDGER"
    assert interface["single_source_evidence_claim_forbidden"] is True
    assert "source_id" not in interface
    assert "source_locator" not in interface
    assert "json_pointer" not in interface

    records = {
        row["source_id"]: row for row in interface["api_artifact_source_records"]
    }
    assert set(records) == {"src_a", "src_b"}
    assert all(
        row["schema"] == API_ARTIFACT_SOURCE_RECORD_SCHEMA for row in records.values()
    )
    assert records["src_a"]["source_locator"].startswith("orders-v1.openapi.json#")
    assert records["src_b"]["source_locator"].startswith("orders-v2.openapi.json#")
    assert records["src_a"]["json_pointer"] == "/paths/~1orders/get"
    assert records["src_b"]["json_pointer"] == "/paths/~1orders/get"
    assert {row["status"] for row in records["src_a"]["response_contracts"]} == {
        "200"
    }
    assert {row["status"] for row in records["src_b"]["response_contracts"]} == {
        "404"
    }

    assert interface["source_contract_conflict_candidate"] is True
    assert interface["automatic_contract_authority_selected"] is False
    conflicts = result["api_artifact_contract_conflicts"]
    assert len(conflicts) == 1
    assert conflicts[0]["schema"] == API_ARTIFACT_CONTRACT_CONFLICT_SCHEMA
    assert conflicts[0]["source_ids"] == ["src_a", "src_b"]
    assert conflicts[0]["automatic_winner_selected"] is False
    receipt = result["api_artifact_semantic_projection"]
    assert receipt["source_record_count"] == 2
    assert receipt["contract_conflict_candidate_count"] == 1
    assert receipt["status"] == "PARTIAL"


def test_equal_contracts_do_not_conflict_only_because_evidence_locations_differ() -> None:
    same = _openapi(status="200", description="OK")
    sources = [
        {
            "source_id": "src_a",
            "document_structure": _structure(
                same,
                "orders-copy-a.openapi.json",
                "src_a",
            ),
        },
        {
            "source_id": "src_b",
            "document_structure": _structure(
                same,
                "orders-copy-b.openapi.json",
                "src_b",
            ),
        },
    ]

    result = enrich_asset_with_api_artifact_semantics(_asset(), sources)

    interface = result["interfaces"][0]
    records = interface["api_artifact_source_records"]
    assert len(records) == 2
    assert len({row["contract_fingerprint"] for row in records}) == 1
    assert interface["source_contract_alignment"] == "AGREED_OR_SINGLE_SOURCE"
    assert interface.get("source_contract_conflict_candidate") is not True
    assert result["api_artifact_contract_conflicts"] == []
    assert result["api_artifact_semantic_projection"][
        "contract_conflict_candidate_count"
    ] == 0
    assert result["api_artifact_semantic_projection"]["status"] == "COMPLETE"
