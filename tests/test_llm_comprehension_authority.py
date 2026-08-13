from __future__ import annotations

import pytest

from ai_test_asset_center.llm_comprehension_authority import (
    RECEIPT_SCHEMA,
    build_comprehension_authority_receipt,
    resolve_provider,
)


def _provider(available: bool = True) -> dict:
    return {
        "available": available,
        "basis": "configured_provider" if available else "provider_not_configured",
        "model": "test-model" if available else "",
    }


def test_receipt_aggregates_all_three_channels() -> None:
    receipt = build_comprehension_authority_receipt(
        provider=_provider(),
        knowledge_asset={
            "rule_promotion_gates": {"gates_met": True},
            "rule_promotion_receipts": [
                {
                    "promoted_count": 5,
                    "skipped_counts": {"inferred": 2, "no_evidence": 1},
                }
            ],
            "semantic_extraction_receipts": [
                {
                    "schema_version": "qualibug.semantic-rule-extraction-mode.v1",
                    "effective_mode": "augment",
                },
                {
                    "rule_funnel": {
                        "llm_rule_candidates": 8,
                        "llm_rule_validation_passed": 6,
                        "llm_rule_validation_rejected": 2,
                        "explicit_count": 5,
                        "inferred_count": 1,
                        "rejected_reason_counts": {
                            "REJECTED_INFERRED_AS_EXPLICIT": 1,
                            "REJECTED_UNGROUNDED_TERM": 1,
                        },
                    },
                },
            ],
        },
        semantic_link_receipt={
            "status": "OK",
            "accepted_relationship_count": 12,
            "failed_unit_count": 1,
            "semantic_linking_degraded_to_source_only": False,
        },
        mainline_reasoner_report={
            "status": "ok",
            "hypotheses_generated": 40,
            "obligations_added": 7,
            "bridge_funnel": {"bound": 7, "dropped_no_endpoint": 33},
        },
    )

    assert receipt["schema_version"] == RECEIPT_SCHEMA
    assert receipt["provider"]["available"] is True
    assert receipt["recall"]["status"] == "AUGMENT_ACTIVE"
    # The honest funnel (rule_funnel) is the primary recall signal, not the
    # legacy promotion-receipt-only reading.
    assert receipt["recall"]["llm_rule_candidates"] == 8
    assert receipt["recall"]["explicit_count"] == 5
    assert receipt["recall"]["funnel_observed"] is True
    assert receipt["recall"]["promoted_rules"] == 5
    assert receipt["recall"]["gates_met"] is True
    assert receipt["recall"]["rejected_reason_counts"]["REJECTED_UNGROUNDED_TERM"] == 1
    assert receipt["binding"]["accepted_edges"] == 12
    assert receipt["depth"]["hypotheses"] == 40
    assert receipt["degraded"] is False


def test_missing_channels_are_named_not_silently_zero() -> None:
    receipt = build_comprehension_authority_receipt(
        provider=_provider(),
        knowledge_asset={},
        semantic_link_receipt={},
        mainline_reasoner_report={},
    )

    assert receipt["recall"]["status"] == "NOT_REQUESTED"
    assert receipt["binding"]["status"] == "NOT_REQUESTED"
    assert receipt["depth"]["status"] == "NOT_REQUESTED"
    assert receipt["degraded"] is False


def test_provider_unavailable_degrades_visibly() -> None:
    receipt = build_comprehension_authority_receipt(
        provider=_provider(available=False),
        knowledge_asset={},
        semantic_link_receipt={},
        mainline_reasoner_report={},
    )

    assert receipt["provider"]["available"] is False
    assert receipt["degraded"] is True
    assert "provider_unavailable" in receipt["degraded_reasons"]


def test_semantic_link_degradation_is_visible() -> None:
    receipt = build_comprehension_authority_receipt(
        provider=_provider(),
        knowledge_asset={},
        semantic_link_receipt={
            "status": "FAILED",
            "reason_code": "agent_semantic_linking_failed",
            "accepted_relationship_count": 0,
            "failed_unit_count": 0,
            "semantic_linking_degraded_to_source_only": True,
        },
        mainline_reasoner_report={},
    )

    assert receipt["binding"]["degraded_to_source_only"] is True
    assert "semantic_link_degraded" in receipt["degraded_reasons"]


def test_resolve_provider_uses_config_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    from ai_test_asset_center.llm_reasoning import ReasoningConfig

    monkeypatch.setattr(
        ReasoningConfig,
        "from_env",
        classmethod(
            lambda cls: ReasoningConfig(
                base_url="https://provider.example/v1",
                api_key="k",
                model="m",
            )
        ),
    )
    assert resolve_provider()["available"] is True

    monkeypatch.setattr(
        ReasoningConfig,
        "from_env",
        classmethod(lambda cls: ReasoningConfig()),
    )
    fact = resolve_provider()
    assert fact["available"] is False
    assert fact["basis"] == "provider_not_configured"
