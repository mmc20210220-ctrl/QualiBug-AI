from __future__ import annotations

from pathlib import Path

from ai_test_asset_center.enterprise_knowledge_center import (
    _classify_source,
    _parse_source,
    build_enterprise_business_knowledge_asset,
    ingest_enterprise_knowledge_documents,
)


MARKDOWN_API_DOC = """# API Docs

### POST `/orders`
Create an order.

| Field | Type | Description |
| --- | --- | --- |
| order_id | string | order identifier |
| amount | number | payable amount |

```json
{"order_id": "ORD-1", "amount": 99.5}
```

### GET `/orders/{order_id}`
Query order detail.

| Field | Type | Description |
| --- | --- | --- |
| order_id | string | order identifier |
```
"""


FIELD_DICTIONARY_DOC = """# Table: orders

| Field | Type | Description | Required |
| --- | --- | --- | --- |
| order_id | string | order identifier | yes |
| amount | decimal | payable amount | yes |
| tenant_id | string | tenant scope | no |
"""


SVG_DOC = """<svg xmlns="http://www.w3.org/2000/svg">
  <title>Orders Checkout</title>
  <desc>Checkout page with loading state and submit action</desc>
  <g id="order-form"></g>
  <g data-name="summary-card"></g>
  <text>Submit Order</text>
  <text>Loading</text>
</svg>
"""


def test_classify_source_distinguishes_new_document_types() -> None:
    prd_text = "# PRD\nThis product requirement references an API appendix."

    assert _classify_source("PRD.md", prd_text) == "prd"
    assert _classify_source("API_DOCS.md", MARKDOWN_API_DOC) == "markdown_api"
    assert _classify_source("db_field_dictionary.csv", "table,field,type,description\norders,amount,decimal,payable amount\n") == "db_field_dictionary"
    assert _classify_source("checkout_flow.svg", SVG_DOC) == "uiux_svg"


def test_parse_source_extracts_markdown_api_field_dictionary_and_svg() -> None:
    api = _parse_source(MARKDOWN_API_DOC.encode("utf-8"), "API_DOCS.md", "markdown_api", "src_api")
    field_dict = _parse_source(FIELD_DICTIONARY_DOC.encode("utf-8"), "field_dictionary.md", "db_field_dictionary", "src_dict")
    svg = _parse_source(SVG_DOC.encode("utf-8"), "checkout_flow.svg", "uiux_svg", "src_ui")

    assert len(api["operations"]) == 2
    assert {row["path"] for row in api["operations"]} == {"/orders", "/orders/{order_id}"}
    assert "order_id" in api["operations"][0]["parameters"]
    assert "amount" in api["operations"][0]["parameters"]

    assert len(field_dict["tables"]) == 1
    assert field_dict["tables"][0]["name"] == "orders"
    assert {row["field"] for row in field_dict["field_dictionary"]} >= {"order_id", "amount", "tenant_id"}
    tenant_row = next(row for row in field_dict["field_dictionary"] if row["field"] == "tenant_id")
    assert tenant_row["required"] is False

    assert len(svg["ui_specs"]) == 1
    assert svg["ui_specs"][0]["name"] == "Orders Checkout"
    assert "Submit Order" in svg["ui_specs"][0]["text_labels"]
    assert "Loading" in svg["ui_specs"][0]["states"]


def test_build_asset_includes_new_extracted_structures(tmp_path: Path) -> None:
    project_id = "enterprise_knowledge_parse_case"
    actor = {"name": "tester", "role": "project_owner"}
    docs = [
        {"filename": "API_DOCS.md", "text": MARKDOWN_API_DOC},
        {"filename": "field_dictionary.md", "text": FIELD_DICTIONARY_DOC},
        {"filename": "checkout_flow.svg", "text": SVG_DOC},
    ]

    ingest = ingest_enterprise_knowledge_documents(project_id, docs, root=tmp_path, actor=actor)
    asset = build_enterprise_business_knowledge_asset(project_id, root=tmp_path)

    assert ingest["ok"] is True
    assert asset["summary"]["active_source_count"] == 3
    assert asset["summary"]["interface_count"] == 2
    assert asset["summary"]["data_table_count"] >= 1
    assert asset["summary"]["field_dictionary_count"] >= 3
    assert asset["summary"]["ui_design_spec_count"] == 1
    assert asset["summary"]["source_type_distribution"]["markdown_api"] == 1
    assert asset["summary"]["source_type_distribution"]["db_field_dictionary"] == 1
    assert asset["summary"]["source_type_distribution"]["uiux_svg"] == 1
    assert any(row["relation"] == "source_to_asset" for row in asset["relationships"])
