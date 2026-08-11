"""P6 contract test: benchmark metrics computation against seeded ground truth.

Verifies that:
- ``compute_benchmark`` returns empty when no ground truth exists
- With ground truth, computes recall/precision/FPR/FNR correctly
- ``_method_path_key`` normalizes path params for matching
- evaluator scoring exposes no unsigned generic persistence API
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from benchmark_evaluator import benchmark_compute as benchmark_compute_module
from benchmark_evaluator.benchmark_compute import (
    _extract_api_paths,
    _method_path_key,
    compute_benchmark,
)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _canonical_finding(canonical_id: str, **fields: Any) -> dict[str, Any]:
    finding_id = str(fields.get("finding_id") or f"finding-{canonical_id}")
    return {
        "canonical_defect_id": canonical_id,
        "canonical_identity_fingerprint": canonical_id.removeprefix("cdef_"),
        "finding_id": finding_id,
        "delivery_occurrence_finding_id": finding_id,
        "delivery_occurrence_finding_ids": [finding_id],
        "delivery_occurrence_count": 1,
        **fields,
    }


def test_method_path_key_normalizes_path_params() -> None:
    assert _method_path_key({"method": "GET", "path": "/api/orders/123"}) == ("GET", "/api/orders/{id}")
    assert _method_path_key({"method": "POST", "_api_path": "/api/tenants/{tenantId}/orders/{orderId}"}) == ("POST", "/api/tenants/{id}/orders/{id}")
    assert _method_path_key({"method": "", "_api_method": "DELETE", "path": "/api/items/42"}) == ("DELETE", "/api/items/{id}")


def test_compute_benchmark_returns_empty_when_no_ground_truth(tmp_path: Path) -> None:
    result = compute_benchmark("test", [], root=tmp_path)
    assert result["benchmark_active"] is False
    assert result["ground_truth_available"] is False
    assert "recall" not in result
    assert result["coverage_matrix"]["covered_family_count"] == 0


def test_compute_benchmark_with_ground_truth(tmp_path: Path) -> None:
    # Write ground truth inside the test temp dir
    gt_path = tmp_path / "ground_truth" / "bugs.json"
    gt_bugs = [
        {"bug_id": "BUG_001", "title": "SQL Injection in login", "type": "security", "severity": "P0", "trigger": "/api/login", "method": "POST"},
        {"bug_id": "BUG_002", "title": "Missing auth on orders", "type": "auth", "severity": "P1", "trigger": "/api/orders", "method": "GET"},
        {"bug_id": "BUG_003", "title": "Double refund allowed", "type": "idempotency", "severity": "P0", "trigger": "/api/refunds", "method": "POST"},
    ]
    _write_json(gt_path, {"bugs": gt_bugs})

    # Scan findings — found BUG_001 and BUG_003, missed BUG_002
    findings = [
        _canonical_finding(
            "cdef_sql", title="SQL Injection", method="POST", path="/api/login",
            severity="P0", confirmation_status="confirmed",
            raw_evidence={"request_raw": {"method": "POST"}, "response_raw": {"status_code": 200}},
            expected="no SQL error", actual="SQL error",
            reproduction={"is_synthetic": False}, gate_passed=True,
        ),
        _canonical_finding(
            "cdef_refund", title="Double Refund",
            category="idempotency_duplicate_submit", method="POST",
            path="/api/refunds", severity="P0", confirmation_status="confirmed",
            raw_evidence={"request_raw": {"method": "POST"}, "response_raw": {"status_code": 201}},
            expected="409 conflict", actual="201 created",
            reproduction={"is_synthetic": False}, gate_passed=True,
        ),
    ]

    result = compute_benchmark("test", findings, root=tmp_path, ground_truth_path=str(gt_path))

    assert result.get("benchmark_active") is True
    assert result.get("ground_truth_bug_count") == 3
    assert result.get("true_positives") == 2
    assert result.get("false_negatives") == 1
    assert result.get("recall") == round(2 / 3, 4)
    assert result.get("precision") == 1.0  # both findings matched
    assert result.get("false_positive_rate") == 0.0
    assert result.get("high_value_recall") == round(2 / 3, 4)  # BUG_001(P0)+BUG_003(P0) found, BUG_002(P1) missed among 3 high-value bugs
    # Evidence completeness: both have request + response + assertion
    assert result.get("evidence_completeness_rate") == 1.0
    # Reproduction success: both gate_passed and not synthetic
    assert result.get("reproduction_success_rate") == 1.0
    # Missed bug
    assert "BUG_002" in result.get("missed_bug_ids", [])
    # Bug type breakdown
    assert result.get("bug_type_breakdown", {}).get("security", {}).get("detected") == 1


def test_benchmark_rejects_duplicate_canonical_representatives(tmp_path: Path) -> None:
    gt_path = tmp_path / "ground_truth" / "bugs.json"
    _write_json(gt_path, {"bugs": [
        {"bug_id": "BUG_1", "title": "missing auth", "type": "auth", "severity": "P1", "endpoint_hint": "/api/items"},
        {"bug_id": "BUG_2", "title": "permission variant", "type": "auth", "severity": "P1", "endpoint_hint": "/api/items"},
    ]})
    base = {
        "method": "POST",
        "path": "/api/items",
        "confirmation_status": "confirmed",
        "customer_delivery_status": "defect",
        "gate_passed": True,
    }

    with pytest.raises(ValueError, match="duplicate_canonical_defect_id"):
        compute_benchmark(
            "test",
            [
                _canonical_finding("cdef_permission", **base, title="Permission Oracle A", category="permission"),
                _canonical_finding("cdef_permission", **base, title="Permission Oracle B", category="permission"),
            ],
            root=tmp_path,
            ground_truth_path=str(gt_path),
        )


def test_endpoint_coverage_alone_is_not_counted_as_a_detected_bug(tmp_path: Path) -> None:
    gt_path = tmp_path / "ground_truth" / "bugs.json"
    _write_json(gt_path, {"bugs": [{
        "bug_id": "BUG_PRICE",
        "title": "amount calculation violates pricing rules",
        "type": "money",
        "severity": "P1",
        "endpoint_hint": "/api/pricing/quote",
    }]})
    finding = _canonical_finding(
        "cdef_permission",
        title="Permission oracle accepted readonly actor",
        category="permission",
        method="POST",
        path="/api/pricing/quote",
        confirmation_status="confirmed",
        customer_delivery_status="defect",
        gate_passed=True,
    )

    result = compute_benchmark(
        "test",
        [finding],
        root=tmp_path,
        ground_truth_path=str(gt_path),
    )

    assert result["true_positives"] == 0
    assert result["false_positives"] == 1
    assert result["recall"] == 0.0
    assert result["canonical_unmatched"] == ["cdef_permission"]
    assert result["gt_unmatched"] == ["BUG_PRICE"]


def test_api_path_matching_preserves_unicode_segments_without_prefix_collisions() -> None:
    assert _extract_api_paths("POST /api/v1/ecommerce/订单/{id}") == {"/api/v1/ecommerce/订单/*"}
    assert _extract_api_paths("POST /api/v1/ecommerce/库存/deduct") == {"/api/v1/ecommerce/库存/deduct"}


def test_evaluator_scoring_has_no_unsigned_generic_persistence_api() -> None:
    assert not hasattr(benchmark_compute_module, "persist_benchmark_result")


def test_benchmark_rejects_occurrences_and_archives_from_scoring(tmp_path: Path) -> None:
    gt_path = tmp_path / "ground_truth" / "bugs.json"
    _write_json(gt_path, {"bugs": [{"bug_id": "GT-1", "title": "permission", "type": "auth"}]})

    with pytest.raises(ValueError, match="canonical_representative"):
        compute_benchmark(
            "test",
            [{"finding_id": "occurrence-1", "title": "permission", "gate_passed": True}],
            root=tmp_path,
            ground_truth_path=str(gt_path),
        )

    archived = _canonical_finding(
        "cdef_archived", title="permission", gate_passed=True, archive_entry=True
    )
    with pytest.raises(ValueError, match="archive"):
        compute_benchmark(
            "test", [archived], root=tmp_path, ground_truth_path=str(gt_path)
        )


def test_canonical_matching_is_order_independent_and_globally_optimal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    gt_path = tmp_path / "ground_truth" / "bugs.json"
    gt_rows = [
        {"bug_id": "GT-1", "title": "first", "type": "auth", "severity": "P1"},
        {"bug_id": "GT-2", "title": "second", "type": "auth", "severity": "P1"},
    ]
    _write_json(gt_path, {"bugs": gt_rows})
    findings = [
        _canonical_finding(
            "cdef_a", title="A", category="permission", gate_passed=True,
            confirmation_status="confirmed", customer_delivery_status="defect",
        ),
        _canonical_finding(
            "cdef_b", title="B", category="permission", gate_passed=True,
            confirmation_status="confirmed", customer_delivery_status="defect",
        ),
    ]
    edge_scores = {
        ("cdef_a", "GT-1"): 0.95,
        ("cdef_a", "GT-2"): 0.80,
        ("cdef_b", "GT-1"): 0.85,
    }

    monkeypatch.setattr(
        benchmark_compute_module,
        "_score_finding_gt",
        lambda finding, gt: edge_scores.get(
            (finding["canonical_defect_id"], gt["bug_id"])
        ),
    )
    monkeypatch.setattr(
        benchmark_compute_module,
        "_match_evidence_finding_gt",
        lambda finding, gt: (
            {"criteria": ["synthetic_test_edge"]}
            if (finding["canonical_defect_id"], gt["bug_id"]) in edge_scores
            else None
        ),
    )

    first = compute_benchmark(
        "test", findings, root=tmp_path, ground_truth_path=str(gt_path)
    )
    second = compute_benchmark(
        "test", list(reversed(findings)), root=tmp_path,
        ground_truth_path=str(gt_path),
    )

    expected_pairs = [
        ("cdef_a", "GT-2"),
        ("cdef_b", "GT-1"),
    ]
    assert [
        (row["canonical_defect_id"], row["gt_bug_id"])
        for row in first["matched_bugs"]
    ] == expected_pairs
    assert second["matched_bugs"] == first["matched_bugs"]
    assert first["canonical_unmatched"] == []
    assert first["gt_unmatched"] == []
    assert first["true_positives"] == 2


def test_benchmark_rejects_duplicate_ground_truth_identity(tmp_path: Path) -> None:
    gt_path = tmp_path / "ground_truth" / "bugs.json"
    _write_json(gt_path, {"bugs": [
        {"bug_id": "GT-1", "title": "first", "type": "auth"},
        {"bug_id": "GT-1", "title": "duplicate", "type": "auth"},
    ]})

    with pytest.raises(ValueError, match="duplicate_ground_truth_bug_id"):
        compute_benchmark(
            "test",
            [_canonical_finding(
                "cdef_a", title="A", category="permission", gate_passed=True,
                confirmation_status="confirmed",
            )],
            root=tmp_path,
            ground_truth_path=str(gt_path),
        )


def test_match_rejects_cross_endpoint_family_and_keyword_collision(
    tmp_path: Path,
) -> None:
    gt_path = tmp_path / "ground_truth" / "bugs.json"
    _write_json(gt_path, {"bugs": [{
        "bug_id": "SURFACE-A",
        "title": "area a permission marker exposure",
        "type": "auth",
        "endpoint_hint": "/api/area-a/records",
        "match_keywords": ["permission", "marker", "actor-alpha"],
    }]})
    finding = _canonical_finding(
        "cdef_other_surface",
        title="area a permission marker exposure",
        category="permission",
        method="GET",
        path="/api/area-b/records",
        reproduction={"method": "GET", "path": "/api/area-b/records"},
        gate_passed=True,
        confirmation_status="confirmed",
    )

    result = compute_benchmark(
        "test", [finding], root=tmp_path, ground_truth_path=str(gt_path)
    )

    assert result["true_positives"] == 0
    assert result["canonical_unmatched"] == ["cdef_other_surface"]
    assert result["gt_unmatched"] == ["SURFACE-A"]


def test_match_normalizes_dynamic_endpoint_and_reports_edge_criteria(
    tmp_path: Path,
) -> None:
    gt_path = tmp_path / "ground_truth" / "bugs.json"
    _write_json(gt_path, {"bugs": [{
        "bug_id": "RESOURCE-AUTH",
        "title": "other actor can read resource",
        "type": "auth",
        "method": "GET",
        "endpoint_hint": "/api/resources/:id",
        "match_keywords": ["resource", "other actor", "permission"],
    }]})
    finding = _canonical_finding(
        "cdef_resource_auth",
        title="other actor can read resource",
        category="permission",
        method="GET",
        path="/api/resources/123",
        reproduction={"method": "GET", "path": "/api/resources/123"},
        gate_passed=True,
        confirmation_status="confirmed",
    )

    result = compute_benchmark(
        "test", [finding], root=tmp_path, ground_truth_path=str(gt_path)
    )

    assert result["true_positives"] == 1
    pair = result["matched_bugs"][0]
    assert pair["canonical_defect_id"] == "cdef_resource_auth"
    assert pair["gt_bug_id"] == "RESOURCE-AUTH"
    assert "path_overlap" in pair["match_evidence"]["criteria"]
    assert pair["match_evidence"]["finding_paths"] == ["/api/resources/123"]
    assert pair["match_evidence"]["gt_paths"] == ["/api/resources/*"]


def test_concrete_resource_id_does_not_match_static_action_route(
    tmp_path: Path,
) -> None:
    gt_path = tmp_path / "ground_truth" / "bugs.json"
    _write_json(gt_path, {"bugs": [{
        "bug_id": "RESOURCE-BULK",
        "title": "bulk transition authorization",
        "type": "auth",
        "trigger": "POST /api/resources/bulk-transition",
        "match_keywords": ["resource", "transition", "permission"],
    }]})
    finding = _canonical_finding(
        "cdef_resource_detail",
        title="resource permission failure",
        category="permission",
        method="GET",
        path="/api/resources/e277f6ed-a4fd-42c1-9d0a-932f27d91c80",
        reproduction={
            "method": "GET",
            "path": "/api/resources/e277f6ed-a4fd-42c1-9d0a-932f27d91c80",
        },
        gate_passed=True,
        confirmation_status="confirmed",
    )

    result = compute_benchmark(
        "test", [finding], root=tmp_path, ground_truth_path=str(gt_path)
    )

    assert result["true_positives"] == 0


def test_no_path_gt_rejects_generic_family_vocabulary(tmp_path: Path) -> None:
    gt_path = tmp_path / "ground_truth" / "bugs.json"
    _write_json(gt_path, {"bugs": [{
        "bug_id": "AUTH-GENERIC",
        "title": "authorization permission failure",
        "type": "auth",
        "match_keywords": ["authorization", "permission", "access"],
        "trigger": "role is accepted",
    }]})
    finding = _canonical_finding(
        "cdef_unrelated_auth",
        title="authorization permission access accepted",
        category="permission",
        method="GET",
        path="/api/unrelated",
        gate_passed=True,
        confirmation_status="confirmed",
    )

    result = compute_benchmark(
        "test", [finding], root=tmp_path, ground_truth_path=str(gt_path)
    )

    assert result["true_positives"] == 0


def test_no_path_gt_accepts_explainable_action_identity(tmp_path: Path) -> None:
    gt_path = tmp_path / "ground_truth" / "bugs.json"
    _write_json(gt_path, {"bugs": [{
        "bug_id": "ACTION-ROLE",
        "title": "privileged transition accepts actor alpha",
        "type": "auth",
        "match_keywords": [
            "privileged-transition", "actor-beta", "actor-alpha",
        ],
        "trigger": "actor alpha invokes privileged transition",
    }]})
    finding = _canonical_finding(
        "cdef_privileged_transition",
        title="privileged-transition accepted actor-alpha instead of actor-beta",
        category="permission",
        method="POST",
        path="/api/resources/privileged-transition",
        gate_passed=True,
        confirmation_status="confirmed",
    )

    result = compute_benchmark(
        "test", [finding], root=tmp_path, ground_truth_path=str(gt_path)
    )

    assert result["true_positives"] == 1
    evidence = result["matched_bugs"][0]["match_evidence"]
    assert "semantic_identity_without_gt_endpoint" in evidence["criteria"]
    assert "privileged-transition" in evidence["strong_identity_keyword_hits"]


def test_raw_runtime_payload_values_are_not_gt_identity_signals(
    tmp_path: Path,
) -> None:
    gt_path = tmp_path / "ground_truth" / "bugs.json"
    _write_json(gt_path, {"bugs": [{
        "bug_id": "STATE-001",
        "title": "blocked resource still receives credential marker",
        "type": "auth",
        "match_keywords": [
            "blocked", "state", "credential", "blocked-resource-alpha",
        ],
        "trigger": "blocked resource requests a credential",
    }]})
    finding = _canonical_finding(
        "cdef_unrelated_export",
        title="unrelated export accepted unauthorized actor",
        description=(
            "captured response payload: blocked state credential "
            "blocked-resource-alpha"
        ),
        category="permission",
        path="/api/unrelated/export",
        method="GET",
        gate_passed=True,
        confirmation_status="confirmed",
        contract_evidence={
            "raw_response": {
                "state": "blocked",
                "credential": "incidental-credential",
                "resource": "blocked-resource-alpha",
            }
        },
        runtime_observation={
            "response_body": (
                "blocked state credential blocked-resource-alpha"
            )
        },
    )

    result = compute_benchmark(
        "test", [finding], root=tmp_path, ground_truth_path=str(gt_path)
    )

    assert result["true_positives"] == 0


def test_compute_benchmark_no_false_fabrication(tmp_path: Path) -> None:
    """Without ground truth, must return empty — never fabricate numbers."""
    # Use a nested subdirectory so root.parent doesn't accidentally hit
    # the benchmark_mall written by a prior test in the same session.
    isolated = tmp_path / "nested" / "project_dir"
    isolated.mkdir(parents=True)
    result = compute_benchmark("empty", [], root=isolated)
    assert result["benchmark_active"] is False
    assert result["ground_truth_available"] is False
    assert "recall" not in result
    assert result["coverage_matrix"]["unclassified_signal_count"] == 0
    # Even with unclassified findings, benchmark rates remain absent and the
    # signal is reported only as an honest coverage-matrix remainder.
    result = compute_benchmark("empty", [{"title": "fake"}], root=isolated)
    assert result["benchmark_active"] is False
    assert "recall" not in result
    assert result["coverage_matrix"]["unclassified_signal_count"] == 1
