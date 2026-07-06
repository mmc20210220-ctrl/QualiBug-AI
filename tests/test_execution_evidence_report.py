from ai_test_asset_center.execution_evidence_report import (
    build_execution_evidence_report,
    has_runtime_evidence,
)


def test_has_runtime_evidence_detects_request_response_status():
    item = {
        "hypothesis_id": "h1",
        "verification_method": {"path": "/api/order/create"},
        "runtime": {
            "request": {"method": "POST", "path": "/api/order/create"},
            "response": {"status_code": 500, "body": "server error"},
        },
    }

    assert has_runtime_evidence(item) is True


def test_has_runtime_evidence_rejects_narrative_only_items():
    item = {
        "hypothesis_id": "h2",
        "summary": "The order API may have a concurrency issue.",
        "verification_method": {
            "path": "/api/order/create",
            "step1": "Create two orders at the same time",
            "step2": "Check duplicated inventory deduction",
            "step3": "Verify order state",
        },
    }

    assert has_runtime_evidence(item) is False


def test_execution_evidence_report_counts_per_engine_quality():
    report = build_execution_evidence_report(
        {
            "causality": [
                {"request": "POST /api/order/create", "status_code": 500},
                {"verification_method": {"path": "/api/order/cancel"}},
            ],
            "temporal": [
                {"summary": "Needs follow-up analysis"},
            ],
            "invariant": [
                {"probe": {"path": "/api/inventory", "observed": {"status": 200}}},
            ],
        },
        engine_names=["causality", "temporal", "invariant", "boundary"],
    )

    assert report["evidence_backed_items"] == 2
    assert report["non_evidence_backed_items"] == 2
    assert report["evidence_backed_ratio"] == 0.5
    assert report["per_engine_evidence_backed_ratio"]["causality"] == 0.5
    assert report["per_engine_evidence_backed_ratio"]["temporal"] == 0.0
    assert report["per_engine_evidence_backed_ratio"]["invariant"] == 1.0
    assert report["per_engine_evidence_backed_ratio"]["boundary"] == 0.0
    assert report["engines_with_no_evidence_backed_output"] == ["temporal", "boundary"]
