"""Tests for blocker_attribution.py — SPEC v1.2 §6."""
import pytest
from ai_test_asset_center.blocker_attribution import (
    ATTRIBUTION_CATEGORIES,
    RECOVERABILITY_VALUES,
    attribute_blocker,
    attribute_all_blockers,
)


def _make_obligation(oid="obl_1", family="authorization"):
    return {"obligation_id": oid, "risk_family": family, "source_refs": []}


def _make_blocked_experiment(oid="obl_1", reason="BLOCKED_MISSING_OBSERVER", detail=""):
    return {
        "obligation_id": oid,
        "experiment_id": f"exp_{oid}",
        "compile_receipt": {"status": "BLOCKED", "reason_code": reason, "detail": detail},
    }


class TestAttributionCategories:
    def test_all_categories_defined(self):
        # Category set is intentionally additive: the chain-positioning
        # extension registered hypothesis-stage families (LLM_PROVIDER_GAP,
        # HYPOTHESIS_GENERATION_GAP, CONTRACT_DERIVATION_GAP,
        # EMBEDDING_CAPABILITY_GAP, LEARNING_FEEDBACK_GAP) plus the
        # non-blocking families the registry already used (PERMISSION_GAP,
        # NORMAL_OUTCOME, DISCOVERY_DIAGNOSTIC, EXECUTION_BUDGET,
        # PLANNING_DEFERRED, UNREGISTERED).
        assert "SOURCE_GAP" in ATTRIBUTION_CATEGORIES
        assert "COMPILER_GAP" in ATTRIBUTION_CATEGORIES
        assert "UNKNOWN" in ATTRIBUTION_CATEGORIES
        assert "LLM_PROVIDER_GAP" in ATTRIBUTION_CATEGORIES
        assert "HYPOTHESIS_GENERATION_GAP" in ATTRIBUTION_CATEGORIES
        assert "CONTRACT_DERIVATION_GAP" in ATTRIBUTION_CATEGORIES

    def test_recoverability_values(self):
        assert "RECOVERABLE" in RECOVERABILITY_VALUES
        assert "PERMANENTLY_BLOCKED" in RECOVERABILITY_VALUES


class TestAttributeBlocker:
    def test_not_blocked(self):
        result = attribute_blocker(
            obligation=_make_obligation(),
            experiment={"obligation_id": "obl_1", "compile_receipt": {"status": "COMPILED"}},
            execution_result=None,
            behavior_ir={},
        )
        assert result["reason_code"] == ""
        assert result["attribution"] == ""

    def test_missing_observer(self):
        result = attribute_blocker(
            obligation=_make_obligation(),
            experiment=_make_blocked_experiment(reason="BLOCKED_MISSING_OBSERVER"),
            execution_result=None,
            behavior_ir={},
        )
        assert result["attribution"] == "OBSERVER_CAPABILITY_GAP"
        assert result["recoverability"] == "RECOVERABLE"
        assert result["must_remain_blocked"] is False

    def test_missing_actor_is_source_gap(self):
        result = attribute_blocker(
            obligation=_make_obligation(),
            experiment=_make_blocked_experiment(reason="BLOCKED_MISSING_ACTOR"),
            execution_result=None,
            behavior_ir={},
        )
        assert result["attribution"] == "SOURCE_GAP"
        assert result["recoverability"] == "SOURCE_DEPENDENT"

    def test_environment_is_permanent(self):
        result = attribute_blocker(
            obligation=_make_obligation(),
            experiment=_make_blocked_experiment(
                reason="BLOCKED_UNSUPPORTED_ADAPTER",
                detail="non_production_environment_required",
            ),
            execution_result=None,
            behavior_ir={},
        )
        assert result["attribution"] == "ADAPTER_CAPABILITY_GAP"
        assert result["must_remain_blocked"] is False

    def test_conflicting_source_permanent(self):
        result = attribute_blocker(
            obligation=_make_obligation(),
            experiment=_make_blocked_experiment(reason="BLOCKED_CONFLICTING_SOURCE"),
            execution_result=None,
            behavior_ir={},
        )
        assert result["must_remain_blocked"] is True

    def test_schema_version(self):
        result = attribute_blocker(
            obligation=_make_obligation(),
            experiment=_make_blocked_experiment(),
            execution_result=None,
            behavior_ir={},
        )
        assert result["schema_version"] == "qualibug.blocker-attribution.v1"


class TestBatchAttribution:
    def test_batch_counts(self):
        result = attribute_all_blockers(
            obligations=[_make_obligation("obl_1"), _make_obligation("obl_2")],
            experiments=[
                _make_blocked_experiment("obl_1", "BLOCKED_MISSING_OBSERVER"),
                _make_blocked_experiment("obl_2", "BLOCKED_MISSING_ACTOR"),
            ],
            execution_results=[],
            behavior_ir={},
        )
        assert result["total_blocked"] == 2
        assert result["recoverable_count"] == 1  # observer is recoverable
        assert result["attribution_counts"]["OBSERVER_CAPABILITY_GAP"] == 1
        assert result["attribution_counts"]["SOURCE_GAP"] == 1
