from __future__ import annotations

from ai_test_asset_center.behavior_ir_hypothesis_coverage import (
    build_source_backed_coverage_hypotheses,
    build_source_backed_coverage_obligations,
)


def _gaps(count: int) -> dict:
    return {
        "uncovered_nodes": [
            {
                "coverage_id": f"cov-{index:04d}",
                "node_type": "operation",
                "ir_node_id": f"op-{index:04d}",
                "risk_family": "invariant",
                "operation_path": f"/resources/{index}",
                "operation_method": "GET",
                "source_refs": [{
                    "kind": "openapi",
                    "locator": f"/resources/{index}",
                    "quote": f"GET /resources/{index}",
                }],
            }
            for index in range(count)
        ]
    }


def _behavior_ir(count: int) -> dict:
    return {
        "operations": [
            {
                "id": f"op-{index:04d}",
                "method": "GET",
                "path": f"/resources/{index}",
                "entity_refs": [],
            }
            for index in range(count)
        ],
        "entities": [],
        "actors": [],
        "invariants": [],
        "relations": [],
    }


def test_default_coverage_generation_does_not_drop_nodes_after_500() -> None:
    gaps = _gaps(501)
    generated = build_source_backed_coverage_hypotheses(_behavior_ir(501), gaps)

    assert len(generated) == 501
    assert gaps["coverage_budget_receipt"] == {
        "mode": "UNBOUNDED_SOURCE_COVERAGE",
        "source_uncovered_count": 501,
        "effective_limit": 501,
        "budget_skipped_count": 0,
        "truncated": False,
        "reason_code": "NO_STRUCTURAL_COVERAGE_HYPOTHESIS_CAP",
    }
    assert all("_coverage_budget_receipt" in row for row in generated)


def test_explicit_coverage_budget_remains_supported_and_receipted() -> None:
    gaps = _gaps(501)
    generated = build_source_backed_coverage_hypotheses(
        _behavior_ir(501),
        gaps,
        max_hypotheses=500,
    )

    assert len(generated) == 500
    assert gaps["coverage_budget_receipt"]["mode"] == "EXPLICIT_OPERATOR_COVERAGE_BUDGET"
    assert gaps["coverage_budget_receipt"]["requested_limit"] == 500
    assert gaps["coverage_budget_receipt"]["budget_skipped_count"] == 1
    assert gaps["coverage_budget_receipt"]["truncated"] is True
    assert gaps["coverage_budget_receipt"]["reason_code"] == "EXPLICIT_OPERATOR_COVERAGE_BUDGET_APPLIED"
    assert all(row["_coverage_budget_receipt"]["budget_skipped_count"] == 1 for row in generated)


def test_default_obligation_generation_also_routes_the_full_uncovered_pool() -> None:
    gaps = _gaps(501)
    generated = build_source_backed_coverage_obligations(_behavior_ir(501), gaps)

    assert len(generated) == 501
    assert gaps["coverage_budget_receipt"]["budget_skipped_count"] == 0
    assert gaps["coverage_budget_receipt"]["reason_code"] == "NO_STRUCTURAL_COVERAGE_HYPOTHESIS_CAP"
