from pathlib import Path

import inspect
import json

import pytest

from ai_test_asset_center.risk_coverage_projection import (
    PRODUCT_COVERAGE_SCHEMA,
    compute_product_coverage_projection,
    persist_product_coverage_projection,
)


def test_product_projection_returns_invariant_coverage_without_ground_truth() -> None:
    metrics = compute_product_coverage_projection(
        [
            {
                "title": "普通用户可越权退款他人订单",
                "risk_type": "authorization_access_control",
                "confirmation_status": "confirmed",
                "expected": "普通用户不能退款他人订单",
                "actual": "接口返回成功",
                "_api_method": "POST",
                "_api_path": "/orders/123/refund",
                "raw_evidence": {
                    "request_raw": {"method": "POST", "path": "/orders/123/refund"},
                    "response_raw": {"status_code": 200},
                },
            }
        ],
        candidates=[
            {
                "title": "并发重复退款可能导致金额不一致",
                "category": "concurrency",
                "_api_method": "POST",
                "_api_path": "/orders/123/refund",
            }
        ],
    )

    assert metrics["benchmark_active"] is False
    assert metrics["ground_truth_available"] is False
    assert "recall" not in metrics
    matrix = metrics["coverage_matrix"]
    assert matrix["schema_version"] == PRODUCT_COVERAGE_SCHEMA
    assert matrix["ontology_family_count"] > 0
    assert matrix["covered_family_count"] >= 2
    family_rows = {row["family"]: row for row in matrix["families"]}
    assert family_rows["authorization"]["coverage_status"] == "confirmed_with_evidence"
    assert family_rows["concurrency"]["coverage_status"] == "candidate_only"


def test_product_coverage_never_reads_ground_truth(
    monkeypatch,
    tmp_path: Path,
) -> None:
    local_truth = (
        tmp_path
        / "platform_workspace"
        / "demo_project"
        / "private_ground_truth"
        / "ground_truth_bugs.json"
    )
    local_truth.parent.mkdir(parents=True)
    local_truth.write_text(
        json.dumps({"bugs": [{"bug_id": "secret-answer"}]}),
        encoding="utf-8",
    )
    env_truth = tmp_path / "env-ground-truth.json"
    env_truth.write_text(
        json.dumps({"bugs": [{"bug_id": "other-secret-answer"}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("QUALIBUG_BENCHMARK_GROUND_TRUTH", str(env_truth))

    projection = compute_product_coverage_projection(
        [{"risk_type": "authorization_access_control"}],
        candidates=[],
    )

    assert projection["measurement_status"] == "NOT_MEASURED"
    assert projection["benchmark_active"] is False
    assert projection["ground_truth_available"] is False
    assert "recall" not in projection
    assert "ground_truth_source" not in projection


def test_product_scan_source_does_not_invoke_private_benchmark_evaluator() -> None:
    # The scan body lives in _scan_impl (scan() is the post-hook wrapper). The
    # product-side coverage projection must run there, never the evaluator-
    # private compute_benchmark scorer.
    from ai_test_asset_center.__main__ import _scan_impl

    source = inspect.getsource(_scan_impl)
    assert "compute_benchmark" not in source
    assert "compute_product_coverage_projection" in source


def test_ground_truth_scoring_module_is_evaluator_only() -> None:
    root = Path(__file__).resolve().parents[1]
    assert not (root / "ai_test_asset_center" / "benchmark_compute.py").exists()
    assert (root / "benchmark_evaluator" / "benchmark_compute.py").is_file()


@pytest.mark.parametrize(
    "private_context",
    [
        {"groundTruthRef": "private/answers.json"},
        {"nested": {"expected-bug-ids": ["BUG-1"]}},
        {"nested": [{"evaluatorMatchKeywords": ["secret-answer"]}]},
        {"private_evaluator": {"receipt": "forged"}},
        {"trustedObservationPack": {"observations": []}},
        {"p3_seed_defects": []},
    ],
)
def test_product_scan_recursively_rejects_evaluator_private_context(
    private_context: dict,
) -> None:
    from ai_test_asset_center.__main__ import scan

    with pytest.raises(ValueError, match="evaluator_private_context_forbidden"):
        scan("project-1", campaign_context=private_context)


def test_private_context_validator_allows_operational_evaluation_metadata() -> None:
    from ai_test_asset_center.observed_product_scan_protocol import (
        find_evaluator_private_context_paths,
    )

    assert find_evaluator_private_context_paths({
        "evaluation_mode": "shadow",
        "mainline_authority": "experiment_candidate",
        "source_manifest": {"source_id": "source-1", "source_hash": "a" * 64},
        "assertion": {"operator": "gt", "gt": 0},
        "business_search": {"match_keywords": ["invoice", "overdue"]},
    }) == []


def test_product_coverage_persistence_redacts_finding_derived_invariants(
    tmp_path: Path,
) -> None:
    secret = "sk-1234567890abcdefghijkl"
    projection = compute_product_coverage_projection([
        {
            "risk_type": "authorization_access_control",
            "invariant": f"Authorization: Bearer {secret}",
        }
    ])
    assert secret not in json.dumps(projection, ensure_ascii=False)

    path = persist_product_coverage_projection(
        "project-1",
        projection,
        root=tmp_path,
    )

    raw = path.read_text(encoding="utf-8")
    assert secret not in raw
    assert "Authorization: Bearer" not in raw


def test_product_coverage_persistence_rejects_unsafe_project_path(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="project_id_path_unsafe"):
        persist_product_coverage_projection(
            "../escape",
            {"measurement_status": "NOT_MEASURED"},
            root=tmp_path,
        )
