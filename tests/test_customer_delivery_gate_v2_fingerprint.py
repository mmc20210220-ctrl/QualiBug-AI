from __future__ import annotations

from ai_test_asset_center.customer_delivery_gate_v2 import (
    finding_payload_fingerprint,
)


def test_shadow_projection_fields_do_not_change_delivery_payload_fingerprint() -> None:
    finding = {
        "finding_id": "FINDING-1",
        "title": "Owner can read another owner resource",
        "severity": "high",
        "evidence": {"method": "GET", "path": "/api/resources/1"},
    }

    assert finding_payload_fingerprint({
        **finding,
        "finding_class": "shadow",
        "shadow_origin": "delivery_gate",
        "semantic_delivery_gate_status": "DELIVERABLE",
        "delivery_gate_receipt_id": "gate_1",
    }) == finding_payload_fingerprint(finding)
