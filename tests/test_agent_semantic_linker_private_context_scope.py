"""Regression: product-owned bookkeeping must not block the semantic linker.

The knowledge asset legitimately carries fields whose names contain
``ground_truth`` (``is_ground_truth``, ``ground_truth_loaded``,
``ground_truth_generated_from_product_output``, empty
``ground_truth_fingerprint``). Those are product-internal benchmark
annotations with no hidden-GT content. The evaluator-private classifier must
only reject content-bearing answer-authority fields; otherwise the sanctioned
agent semantic linker fails on every real asset with
``evaluator_private_context_forbidden`` and rule-to-interface permits are
never derived (starving authorization/state obligations).
"""
from __future__ import annotations

from ai_test_asset_center.observed_product_scan_protocol import (
    find_evaluator_private_context_paths,
)


def _asset_with_bookkeeping() -> dict:
    return {
        "rule_library": [
            {
                "rule_id": "rule:src:1",
                "statement": "买家只能查询自己的订单",
                "source_id": "src:business_rules",
            }
        ],
        "interfaces": [
            {
                "interface_id": "markdown_api:GET:/api/orders",
                "path": "/api/orders",
                "method": "GET",
            }
        ],
        "enterprise_identity_benchmark_repository_receipt": {
            "ground_truth_loaded": False,
            "ground_truth_fingerprint": "",
        },
        "enterprise_business_object_benchmark": {
            "ground_truth_generated_from_product_output": False,
        },
        "enterprise_identity_annotation_manifest": {
            "is_ground_truth": False,
        },
    }


def test_product_bookkeeping_ground_truth_named_fields_are_allowed() -> None:
    assert find_evaluator_private_context_paths(_asset_with_bookkeeping()) == []


def test_content_bearing_ground_truth_carriers_still_fail_closed() -> None:
    asset = _asset_with_bookkeeping()
    asset["evaluator"] = {"ground_truth_ref": "C:/private/gt/bugs.json"}
    asset["enterprise_identity_benchmark_repository_receipt"][
        "ground_truth_fingerprint"
    ] = "a" * 64
    paths = find_evaluator_private_context_paths(asset)
    assert any("ground_truth_ref" in path for path in paths)
    assert any("ground_truth_fingerprint" in path for path in paths)


def test_content_bearing_expected_defects_still_fail_closed() -> None:
    asset = _asset_with_bookkeeping()
    asset["evaluator"] = {"expected_defects": ["BUG-001"]}
    assert find_evaluator_private_context_paths(asset) != []


def test_linker_precheck_passes_on_bookkeeping_asset() -> None:
    from ai_test_asset_center.agent_semantic_linker import (
        enrich_knowledge_asset_with_agent_relationships,
    )

    class _NoProvider:
        def complete_json(self, **kwargs):  # pragma: no cover - must not be called
            raise AssertionError("pre-check must reject before provider call")

    # The private-context pre-check runs before batching; with only
    # bookkeeping fields the call must reach the provider step, which we
    # simulate with an empty assessment response.
    class _FakeClient:
        def complete_json(self, **kwargs):
            return {"assessments": []}

        def usage_snapshot(self) -> dict[str, float]:
            return {}

    asset, receipt = enrich_knowledge_asset_with_agent_relationships(
        _asset_with_bookkeeping(),
        client=_FakeClient(),
    )
    assert receipt["status"] not in {"", "FAILED"}
    assert "evaluator_private_context_forbidden" not in str(receipt)
