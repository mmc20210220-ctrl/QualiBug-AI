from ai_test_asset_center.private_pilot_campaign_projection import _report_finding_dedupe_key


def test_canonical_defect_id_overrides_title_method_path_identity() -> None:
    first = {
        "canonical_defect_id": "CDEF-001",
        "title": "first presentation",
        "method": "GET",
        "path": "/v1/orders/1",
    }
    second = {
        "canonical_defect_id": "CDEF-001",
        "title": "different presentation",
        "method": "POST",
        "path": "/v2/orders/2",
    }
    assert _report_finding_dedupe_key(first) == "canonical:CDEF-001"
    assert _report_finding_dedupe_key(second) == "canonical:CDEF-001"


def test_distinct_canonical_defects_do_not_collapse_on_same_presentation() -> None:
    common = {
        "title": "same visible title",
        "method": "GET",
        "path": "/v1/orders/1",
    }
    first = {**common, "canonical_defect_id": "CDEF-001"}
    second = {**common, "canonical_defect_id": "CDEF-002"}
    assert _report_finding_dedupe_key(first) != _report_finding_dedupe_key(second)


def test_legacy_finding_without_canonical_id_keeps_existing_fallback() -> None:
    finding = {
        "title": "  [P1]  Order   visibility  ",
        "method": "get",
        "path": "/v1/orders/1",
    }
    assert _report_finding_dedupe_key(finding) == "order visibility|GET|/v1/orders/1"
