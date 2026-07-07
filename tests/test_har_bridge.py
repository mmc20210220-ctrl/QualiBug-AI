from __future__ import annotations

from ai_test_asset_center.har_bridge import enrich_finding_with_har, match_finding_to_har


def _cancel_finding() -> dict:
    return {
        "title": "order: CANCELLED -> /api/orders/{id}/cancel",
        "risk_type": "state_machine",
        "_api_method": "POST",
        "_api_path": "/api/orders/{id}/cancel",
        "repro_method": "POST",
        "repro_path": "/api/orders/{id}/cancel",
    }


def test_match_finding_to_har_rejects_weak_collection_route_binding_for_declared_detail_path() -> None:
    finding = _cancel_finding()
    har_entries = [
        {
            "request": {"method": "GET", "url": "http://example.test/api/orders"},
            "response": {"status": 200, "body": "{\"items\": []}"},
            "time": 12,
        }
    ]

    matches = match_finding_to_har(finding, har_entries)
    enriched = enrich_finding_with_har(finding, har_entries)

    assert matches == []
    assert "har_evidence" not in enriched


def test_match_finding_to_har_accepts_declared_template_path_with_real_instance_path() -> None:
    finding = _cancel_finding()
    har_entries = [
        {
            "request": {"method": "POST", "url": "http://example.test/api/orders/123/cancel"},
            "response": {"status": 200, "body": "{\"status\": \"CANCELLED\"}"},
            "time": 18,
        }
    ]

    matches = match_finding_to_har(finding, har_entries)
    enriched = enrich_finding_with_har(finding, har_entries)

    assert len(matches) == 1
    assert enriched["har_evidence"]["path"] == "/api/orders/123/cancel"
    assert enriched["har_evidence"]["status_code"] == 200
