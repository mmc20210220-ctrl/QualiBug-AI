"""Regression: linker guard scope matches the linker's actual input.

The evaluator-private vocabulary stays a strict name-level contract (an
answer-authority key is rejected regardless of value). The knowledge asset
legitimately carries product-owned bookkeeping fields whose names contain
``ground_truth`` (``is_ground_truth``, ``ground_truth_loaded``,
``ground_truth_generated_from_product_output``, empty
``ground_truth_fingerprint``). Those sections are NOT linker inputs, so the
agent semantic linker must guard exactly the bounded collections it consumes;
otherwise every real asset fails with ``evaluator_private_context_forbidden``
and rule-to-interface permits are never derived.
"""
from __future__ import annotations

from ai_test_asset_center.observed_product_scan_protocol import (
    find_evaluator_private_context_paths,
)
from ai_test_asset_center.agent_semantic_linker import LINKER_INPUT_COLLECTIONS


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


def test_classifier_keeps_strict_name_level_contract() -> None:
    # Product bookkeeping sections are still flagged by the whole-asset
    # classifier: the vocabulary itself marks answer-authority ownership.
    assert find_evaluator_private_context_paths(_asset_with_bookkeeping()) != []


def test_linker_input_view_excludes_product_bookkeeping_sections() -> None:
    asset = _asset_with_bookkeeping()
    linker_input = {
        key: asset.get(key)
        for key in LINKER_INPUT_COLLECTIONS
        if asset.get(key)
    }
    assert find_evaluator_private_context_paths(linker_input) == []


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


def test_linker_precheck_rejects_private_content_inside_input_collections() -> None:
    from ai_test_asset_center.agent_semantic_linker import (
        AgentSemanticLinkerError,
        enrich_knowledge_asset_with_agent_relationships,
    )

    asset = _asset_with_bookkeeping()
    asset["rule_library"][0]["ground_truth_ref"] = "C:/private/gt/bugs.json"

    class _NoProvider:
        def complete_json(self, **kwargs):  # pragma: no cover - must not run
            raise AssertionError("pre-check must reject before provider call")

    try:
        enrich_knowledge_asset_with_agent_relationships(
            asset,
            client=_NoProvider(),
        )
    except AgentSemanticLinkerError as exc:
        assert "evaluator_private_context_forbidden" in str(exc)
    else:
        raise AssertionError("private content inside linker input was accepted")
