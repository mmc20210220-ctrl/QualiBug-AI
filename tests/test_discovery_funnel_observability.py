"""Tests for discovery funnel aggregation (no mock findings as bugs)."""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ai_test_asset_center.discovery_funnel import build_funnel


def test_build_funnel_five_stages_and_separates_validated_from_pending():
    v12_result = {
        "behavior_slices": [{"slice_id": f"BHV_{i}"} for i in range(10)],
        "phases": {
            "incremental_discovery": {
                "selected_slice_ids": [f"BHV_{i}" for i in range(4)],
                "status": "planned",
            },
            "execution": {
                "status": "completed",
                "executed": 3,
                "failed": 1,
                "planned_only": 0,
                "production_data_blocked": 1,
            },
            "oracle": {
                "status": "completed",
                "total_evaluated": 3,
                "violations_found": 2,
            },
        },
        "mainline_unification": {
            "analyzer": {"input": 8, "bound": 5, "dropped_no_endpoint": 3},
        },
        "findings": [
            {
                "title": "validated bug",
                "gate_passed": True,
                "bug_status": "reproduced",
                "customer_delivery_status": "defect",
                "final_review_status": "VALIDATED_CANDIDATE",
                "business_evidence_status": "VALIDATED",
                "execution_status": "executed",
                "confirmation_status": "confirmed",
                "expected": "HTTP 403",
                "actual": "HTTP 200",
                "evidence_status": {
                    "semantic_verdict": "SEMANTIC_CONFIRMED",
                    "business_evidence_status": "VALIDATED",
                    "final_review_status": "VALIDATED_CANDIDATE",
                    "missing_requirements": [],
                },
                "evidence_quality": {"level": "validated", "score": 95, "can_reproduce": True},
                "raw_evidence": {
                    "request_raw": {"method": "GET", "path": "/source-derived-path"},
                    "response_raw": {"status_code": 200},
                    "timestamp": "2026-01-01T00:00:00Z",
                    "has_real_evidence": True,
                },
                "reproduction": {
                    "method": "GET",
                    "path": "/source-derived-path",
                    "har_evidence": {"status_code": 200},
                },
            },
            {
                "title": "pending clue",
                "gate_passed": False,
                "bug_status": "suspected",
                "customer_delivery_status": "defect",
                "evidence_quality": {"level": "needs_evidence", "can_reproduce": False},
                "final_review_status": "NEEDS_MORE_EVIDENCE",
                "business_evidence_status": "PENDING_EVIDENCE",
                "business_gate_missing": ["BEFORE_SNAPSHOT_MISSING", "CLEANUP_PENDING"],
            },
        ],
    }

    funnel = build_funnel(v12_result)

    assert len(funnel["stages"]) == 5
    names = [s["name"] for s in funnel["stages"]]
    assert names == [
        "candidate_generation",
        "probe_selection",
        "execution",
        "verification",
        "formal_accounting",
    ]
    assert funnel["validated_bug_count"] == 1
    assert funnel["pending_finding_count"] == 1
    # Formal accounting output must equal validated only (pending excluded)
    accounting = next(s for s in funnel["stages"] if s["name"] == "formal_accounting")
    assert accounting["output"] == funnel["validated_bug_count"]
    assert accounting["output"] != funnel["validated_bug_count"] + funnel["pending_finding_count"]

    reasons = {item["reason"]: item["count"] for item in funnel["top_blocking_reasons"]}
    assert reasons.get("BEFORE_SNAPSHOT_MISSING", 0) >= 1
    assert reasons.get("CLEANUP_PENDING", 0) >= 1
    assert reasons.get("dropped_no_endpoint", 0) == 3
    assert reasons.get("production_data_blocked", 0) == 1

    assert isinstance(funnel["explanation"], str) and funnel["explanation"]
    assert "已验证" in funnel["explanation"] or "正式" in funnel["explanation"]
    # Actionable suggestion when cleanup/snapshot missing
    assert "QUALIBUG_ENABLE_SANDBOX_WRITE" in funnel["explanation"] or "沙箱" in funnel["explanation"]


def test_build_funnel_empty_result_has_zero_validated_no_fake_numbers():
    funnel = build_funnel({})
    assert funnel["validated_bug_count"] == 0
    assert funnel["pending_finding_count"] == 0
    assert len(funnel["stages"]) == 5
    assert all(isinstance(s.get("input"), int) for s in funnel["stages"])
    assert isinstance(funnel["explanation"], str)
