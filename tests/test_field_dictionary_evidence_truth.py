from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center._parsing import (
    _field_dictionary_entries,
)
from ai_test_asset_center.enterprise_knowledge_center._utils import (
    _dedupe_by_id,
)


def test_field_dictionary_projection_is_not_exact_source_quote() -> None:
    source_text = (
        '{"fields":['
        '{"table":"orders","field":"warehouse_id","required":false},'
        '{"table":"orders","field":"sku","required":true,"type":"string"}'
        ']}'
    )
    rows = _field_dictionary_entries(
        source_text,
        {
            "fields": [
                {"table": "orders", "field": "warehouse_id", "required": False},
                {
                    "table": "orders",
                    "field": "sku",
                    "required": True,
                    "type": "string",
                },
            ]
        },
        "source-fields",
    )
    by_field = {row["field"]: row for row in rows}

    warehouse = by_field["warehouse_id"]
    assert warehouse["required"] is False
    assert warehouse["normalized_evidence"] == (
        "table=orders; field=warehouse_id; required=false"
    )
    assert warehouse["evidence_kind"] == "NORMALIZED_STRUCTURED_DECLARATION"
    assert warehouse["evidence_derivation"] == (
        "normalized_field_dictionary_projection"
    )
    assert "quote" not in warehouse
    assert "source_excerpt" not in warehouse

    sku = by_field["sku"]
    assert sku["required"] is True
    assert sku["normalized_evidence"] == (
        "table=orders; field=sku; required=true; type=string"
    )
    assert "quote" not in sku
    assert "source_excerpt" not in sku


def test_explicit_exact_source_quote_is_preserved() -> None:
    rows = _dedupe_by_id(
        [
            {
                "field_id": "field:source:orders.sku",
                "source_id": "source-fields",
                "table": "orders",
                "field": "sku",
                "required": True,
                "quote": "The source says SKU is required.",
                "source_locator": "fields.json#/fields/0",
                "evidence_kind": "EXACT_SOURCE_QUOTE",
            }
        ],
        "field_id",
    )

    assert rows[0]["quote"] == "The source says SKU is required."
    assert rows[0]["source_locator"] == "fields.json#/fields/0"
    assert "normalized_evidence" not in rows[0]
