from __future__ import annotations

from pathlib import Path
import runpy

_LEGACY_TARGET = "test_field_dictionary_json_preserves_required_false_in_excerpt"
_legacy = runpy.run_path(
    str(Path(__file__).with_name("_enterprise_knowledge_center_parsing_legacy.py"))
)
for _name, _value in _legacy.items():
    if _name.startswith("test_") and _name != _LEGACY_TARGET:
        globals()[_name] = _value


def test_field_dictionary_json_preserves_required_false_in_normalized_evidence() -> None:
    from ai_test_asset_center.enterprise_knowledge_center._parsing import (
        _field_dictionary_entries,
    )

    rows = _field_dictionary_entries(
        '{"fields":[{"table":"orders","field":"warehouse_id","required":false}]}',
        {
            "fields": [
                {"table": "orders", "field": "warehouse_id", "required": False},
                {"table": "orders", "field": "sku", "required": True},
            ]
        },
        "src_fields",
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
    assert sku["normalized_evidence"] == "table=orders; field=sku; required=true"
    assert sku["evidence_kind"] == "NORMALIZED_STRUCTURED_DECLARATION"
    assert sku["evidence_derivation"] == "normalized_field_dictionary_projection"
    assert "quote" not in sku
    assert "source_excerpt" not in sku
