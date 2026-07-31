from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center._linking_impl import (
    _links_by_exact_source_section,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.interface_runtime_contracts import (
    enrich_openapi_runtime_contracts,
)


RULE_STATEMENT = "同一付款请求不得重复成功扣款；重复提交时业务成功效果最多发生一次。"


def _interface_rows():
    return [
        {
            "interface_id": "api:POST:/payments",
            "source_id": "source:payment-api",
            "method": "POST",
            "path": "/payments",
            "operation_id": "submitPayment",
            "summary": "提交付款请求",
        }
    ]


def test_openapi_description_enters_existing_exact_source_link_authority():
    openapi = {
        "openapi": "3.0.3",
        "paths": {
            "/payments": {
                "post": {
                    "operationId": "submitPayment",
                    "summary": "提交付款请求",
                    "description": RULE_STATEMENT,
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }

    interfaces = enrich_openapi_runtime_contracts(openapi, _interface_rows())
    interface = interfaces[0]
    edges = _links_by_exact_source_section(
        [
            {
                "rule_id": "rule:idempotency",
                "source_id": "source:business-rules",
                "statement": RULE_STATEMENT,
            }
        ],
        interfaces,
    )

    assert interface["source_excerpt"] == f"提交付款请求\n{RULE_STATEMENT}"
    assert interface["openapi_description"] == RULE_STATEMENT
    assert interface["source_excerpt_authority"] == (
        "OPENAPI_OPERATION_SUMMARY_DESCRIPTION"
    )
    assert interface["source_excerpt_exact_source_declared"] is True
    assert len(edges) == 1
    assert edges[0]["from"] == "rule:idempotency"
    assert edges[0]["to"] == "api:POST:/payments"
    assert edges[0]["status"] == "accepted"
    assert edges[0]["derivation"] == "exact_source_section"
    assert edges[0]["evidence_gate"] == "exact_source_section"


def test_semantic_overlap_without_verbatim_statement_does_not_create_exact_edge():
    openapi = {
        "openapi": "3.0.3",
        "paths": {
            "/payments": {
                "post": {
                    "operationId": "submitPayment",
                    "summary": "提交付款请求",
                    "description": "处理付款并返回结果。",
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }

    interfaces = enrich_openapi_runtime_contracts(openapi, _interface_rows())
    edges = _links_by_exact_source_section(
        [
            {
                "rule_id": "rule:idempotency",
                "source_id": "source:business-rules",
                "statement": RULE_STATEMENT,
            }
        ],
        interfaces,
    )

    assert edges == []
