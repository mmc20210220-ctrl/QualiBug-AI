"""Tests for blocker attribution evidence refinement — SPEC v1.2.1 §11."""
import pytest
from ai_test_asset_center.blocker_attribution import attribute_blocker


class TestBlockerAttributionEvidenceRefinement:
    def test_two_phase_attribution(self):
        """Result includes primary_attribution and secondary_contributors."""
        obl = {"obligation_id": "obl_1", "source_refs": [{"id": "src_1"}]}
        exp = {
            "experiment_id": "exp_1",
            "compile_receipt": {"status": "BLOCKED", "reason_code": "BLOCKED_MISSING_OBSERVER"},
        }
        result = attribute_blocker(
            obligation=obl,
            experiment=exp,
            execution_result=None,
            behavior_ir={"operations": [], "relations": [], "actors": []},
        )
        assert "primary_attribution" in result
        assert "primary_reason" in result
        assert "secondary_contributors" in result
        assert result["primary_attribution"] == "OBSERVER_CAPABILITY_GAP"

    def test_evidence_refinement_present(self):
        """Result includes evidence_refinement with checks."""
        obl = {"obligation_id": "obl_1"}
        exp = {
            "experiment_id": "exp_1",
            "compile_receipt": {"status": "BLOCKED", "reason_code": "BLOCKED_MISSING_BINDING"},
        }
        result = attribute_blocker(
            obligation=obl,
            experiment=exp,
            execution_result=None,
            behavior_ir={"operations": [{"id": "op_1"}], "relations": [], "actors": []},
        )
        assert "evidence_refinement" in result
        assert result["evidence_refinement"] is not None
        assert "evidence_passed" in result["evidence_refinement"]
        assert "evidence_total" in result["evidence_refinement"]

    def test_available_evidence_populated(self):
        """available_evidence contains evidence checks."""
        obl = {"obligation_id": "obl_1"}
        exp = {
            "experiment_id": "exp_1",
            "compile_receipt": {"status": "BLOCKED", "reason_code": "BLOCKED_MISSING_FIXTURE"},
        }
        result = attribute_blocker(
            obligation=obl,
            experiment=exp,
            execution_result=None,
            behavior_ir={"operations": [], "relations": [], "actors": []},
        )
        assert "available_evidence" in result
        assert len(result["available_evidence"]) > 0

    def test_not_blocked_empty_attribution(self):
        """Non-blocked obligation returns empty attribution."""
        obl = {"obligation_id": "obl_1"}
        exp = {"experiment_id": "exp_1", "compile_receipt": {"status": "COMPILED"}}
        result = attribute_blocker(
            obligation=obl,
            experiment=exp,
            execution_result=None,
            behavior_ir={},
        )
        assert result["primary_attribution"] == ""
        assert result["reason_code"] == ""
