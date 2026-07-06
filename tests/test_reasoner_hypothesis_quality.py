from ai_test_asset_center.stage_reason_all_v2 import (
    _dedupe_hypotheses,
    _filter_low_quality_hypotheses,
    _prioritize_hypotheses,
)


def test_quality_gate_drops_narrative_only_hypotheses():
    hypotheses = [
        {
            "title": "Narrative only risk",
            "description": "This is a broad concern but has no executable probe or concrete evidence.",
        },
        {
            "title": "Executable order lookup risk",
            "verification_method": {
                "method": "GET",
                "path": "/api/orders/{order_id}",
            },
        },
        {
            "title": "Evidence-backed reconciliation risk",
            "evidence": "Observed order total differs from payment total in reader evidence.",
        },
    ]

    filtered = _filter_low_quality_hypotheses(hypotheses)

    assert [item["title"] for item in filtered] == [
        "Executable order lookup risk",
        "Evidence-backed reconciliation risk",
    ]


def test_prioritization_prefers_executable_and_cross_source_hypotheses():
    hypotheses = [
        {
            "title": "Evidence-only risk",
            "severity": "P1",
            "evidence": "Reader evidence suggests this may fail.",
        },
        {
            "title": "Executable cross-source risk",
            "severity": "P2",
            "verification_method": {
                "method": "GET",
                "path": "/api/orders/{order_id}",
                "step1": "Fetch order as owner",
                "step2": "Fetch same order as another user",
            },
            "_merged_sources": ["causality", "local_analyzer"],
        },
        {
            "title": "Single-step executable risk",
            "severity": "P0",
            "verification_method": {"path": "/api/users/{user_id}"},
        },
    ]

    prioritized = _prioritize_hypotheses(hypotheses)

    assert prioritized[0]["title"] == "Executable cross-source risk"
    assert prioritized[-1]["title"] == "Evidence-only risk"


def test_dedupe_merges_duplicate_hypotheses_without_losing_executable_binding():
    duplicate_without_path = {
        "hypothesis_id": "h1",
        "title": "Order can be read across tenants",
        "risk_type": "authorization",
        "expected_behavior": "Orders must be tenant isolated",
        "evidence": "Reader identified tenant_id on orders.",
    }
    duplicate_with_path = {
        "hypothesis_id": "h2",
        "title": "Order can be read across tenants",
        "risk_type": "authorization",
        "expected_behavior": "Orders must be tenant isolated",
        "verification_method": {
            "method": "GET",
            "path": "/api/orders/{order_id}",
            "step1": "Fetch tenant A order as tenant B user",
        },
        "_reasoner_engine": "consistency",
    }

    deduped = _dedupe_hypotheses([duplicate_without_path, duplicate_with_path])

    assert len(deduped) == 1
    merged = deduped[0]
    assert merged["verification_method"]["path"] == "/api/orders/{order_id}"
    assert merged["verification_method"]["step1"] == "Fetch tenant A order as tenant B user"
    assert merged["_merge_count"] == 2
