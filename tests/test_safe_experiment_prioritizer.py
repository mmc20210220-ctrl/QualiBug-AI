"""Tests for safe_experiment_prioritizer.py — SPEC v1.2 §12."""
import pytest
from ai_test_asset_center.safe_experiment_prioritizer import (
    ALLOWED_FACTORS,
    score_experiment_priority,
    prioritize_experiments,
)


def _make_obligation(risk_family="conservation", mechanism="", source_refs=None):
    return {
        "obligation_id": "obl_1",
        "risk_family": risk_family,
        "mechanism": mechanism or risk_family,
        "source_refs": source_refs or [{"kind": "api_doc", "locator": "POST /orders"}],
        "required_operations": ["op_create", "op_delete"],
        "property": {"from_state": "active", "to_state": "cancelled"},
    }


def _make_experiment(observers=None, bindings=None, proof_status="PROVEN"):
    return {
        "experiment_id": "exp_1",
        "obligation_id": "obl_1",
        "safety_contract": {"governed_write": True},
        "write_reversibility_proof": {"proof_status": proof_status},
        "observers": observers or [
            {"observer_id": "obs_1", "kind": "before_state"},
            {"observer_id": "obs_2", "kind": "after_state"},
        ],
        "binding_plan": bindings or [
            {"target": "orderId", "source_kind": "PRIMARY_RESPONSE", "status": "runtime_resolvable"},
        ],
    }


class TestScoring:
    def test_deep_family_high_score(self):
        """Deep risk families (conservation, concurrency) get highest risk_depth."""
        result = score_experiment_priority(
            experiment=_make_experiment(),
            obligation=_make_obligation(risk_family="conservation"),
            behavior_ir={},
        )
        assert result["factors"]["risk_depth"] == 1.0
        assert result["score"] > 5.0

    def test_medium_family_moderate_score(self):
        result = score_experiment_priority(
            experiment=_make_experiment(),
            obligation=_make_obligation(risk_family="authorization"),
            behavior_ir={},
        )
        assert result["factors"]["risk_depth"] == 0.6

    def test_shallow_family_low_score(self):
        result = score_experiment_priority(
            experiment=_make_experiment(),
            obligation=_make_obligation(risk_family="format_validation"),
            behavior_ir={},
        )
        assert result["factors"]["risk_depth"] == 0.3

    def test_observer_readiness(self):
        """More observers = higher readiness."""
        exp = _make_experiment(observers=[
            {"observer_id": f"obs_{i}", "kind": "state_read"} for i in range(4)
        ])
        result = score_experiment_priority(
            experiment=exp,
            obligation=_make_obligation(),
            behavior_ir={},
        )
        assert result["factors"]["observer_readiness"] == 1.0

    def test_binding_readiness_all_resolved(self):
        result = score_experiment_priority(
            experiment=_make_experiment(),
            obligation=_make_obligation(),
            behavior_ir={},
        )
        assert result["factors"]["binding_readiness"] == 1.0

    def test_binding_readiness_unresolved(self):
        exp = _make_experiment(bindings=[
            {"target": "x", "source_kind": "PRIMARY_RESPONSE", "status": "unresolved"},
            {"target": "y", "source_kind": "FIXTURE_RECEIPT", "status": "unresolved"},
        ])
        result = score_experiment_priority(
            experiment=exp,
            obligation=_make_obligation(),
            behavior_ir={},
        )
        assert result["factors"]["binding_readiness"] == 0.0

    def test_proof_readiness_proven(self):
        result = score_experiment_priority(
            experiment=_make_experiment(proof_status="PROVEN"),
            obligation=_make_obligation(),
            behavior_ir={},
        )
        assert result["factors"]["proof_readiness"] == 1.0

    def test_proof_readiness_not_proven(self):
        result = score_experiment_priority(
            experiment=_make_experiment(proof_status="UNPROVEN"),
            obligation=_make_obligation(),
            behavior_ir={},
        )
        assert result["factors"]["proof_readiness"] == 0.0

    def test_novelty_new_mechanism(self):
        """Novel mechanism not in history gets high novelty."""
        result = score_experiment_priority(
            experiment=_make_experiment(),
            obligation=_make_obligation(mechanism="race_condition"),
            behavior_ir={},
            historical_findings=[{"mechanism": "missing_auth"}],
        )
        assert result["factors"]["root_cause_novelty"] == 1.0

    def test_novelty_already_found(self):
        result = score_experiment_priority(
            experiment=_make_experiment(),
            obligation=_make_obligation(mechanism="missing_auth"),
            behavior_ir={},
            historical_findings=[{"mechanism": "missing_auth"}],
        )
        assert result["factors"]["root_cause_novelty"] == 0.2

    def test_all_factors_in_allowed_set(self):
        result = score_experiment_priority(
            experiment=_make_experiment(),
            obligation=_make_obligation(),
            behavior_ir={},
        )
        for key in result["factors"]:
            assert key in ALLOWED_FACTORS, f"Factor {key} not in ALLOWED_FACTORS"

    def test_missing_auth_declaration_is_not_anonymous(self):
        exp = _make_experiment()
        exp["treatment_plan"] = [{"method": "POST", "path": "/api/resources"}]
        result = score_experiment_priority(
            experiment=exp,
            obligation=_make_obligation(),
            behavior_ir={},
        )
        assert result["factors"]["anonymous_write_risk"] == 0.0

    def test_explicit_anonymous_write_is_prioritized(self):
        exp = _make_experiment()
        exp["write_requires_auth"] = False
        exp["treatment_plan"] = [{"method": "POST", "path": "/api/resources"}]
        result = score_experiment_priority(
            experiment=exp,
            obligation=_make_obligation(),
            behavior_ir={},
        )
        assert result["factors"]["anonymous_write_risk"] == 1.0


class TestPrioritization:
    def test_ordering_by_score(self):
        """Higher-scored experiments come first."""
        obls = [
            {"obligation_id": "obl_deep", "risk_family": "conservation", "source_refs": [1, 2, 3]},
            {"obligation_id": "obl_shallow", "risk_family": "format", "source_refs": []},
        ]
        exps = [
            {"experiment_id": "exp_shallow", "obligation_id": "obl_shallow", "observers": [], "binding_plan": []},
            {"experiment_id": "exp_deep", "obligation_id": "obl_deep", "observers": [{"observer_id": "o"}], "binding_plan": []},
        ]
        result = prioritize_experiments(
            experiments=exps,
            obligations=obls,
            behavior_ir={},
        )
        assert result["prioritized"][0]["experiment_id"] == "exp_deep"
        assert result["prioritized"][0]["execution_rank"] == 1

    def test_budget_boundary(self):
        exps = [
            {"experiment_id": f"exp_{i}", "obligation_id": f"obl_{i}", "observers": [], "binding_plan": []}
            for i in range(10)
        ]
        obls = [{"obligation_id": f"obl_{i}", "risk_family": "test"} for i in range(10)]
        result = prioritize_experiments(
            experiments=exps,
            obligations=obls,
            behavior_ir={},
            budget=3,
        )
        assert result["within_budget_count"] == 3
        within = [p for p in result["prioritized"] if p["within_budget"]]
        assert len(within) == 3

    def test_schema_version(self):
        result = prioritize_experiments(
            experiments=[],
            obligations=[],
            behavior_ir={},
        )
        assert result["schema_version"] == "qualibug.experiment-priority.v1"

    def test_empty_inputs(self):
        result = prioritize_experiments(
            experiments=[],
            obligations=[],
            behavior_ir={},
        )
        assert result["total_scored"] == 0
        assert result["prioritized"] == []
