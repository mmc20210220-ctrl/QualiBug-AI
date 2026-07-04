from __future__ import annotations

from ai_test_asset_center import risk_based_probe_planner as planner


def _probe(
    probe_id: str,
    *,
    priority_score: float = 0.7,
    validated_yield_priority_score: float = 0.0,
    execution_policy: str = "direct",
    strict_validation_ready: bool = True,
    evidence_ready_signal: bool = False,
    signal_level: str = "moderate",
    path: str = "/api/orders",
) -> dict[str, object]:
    return {
        "probe_id": probe_id,
        "risk_type": "business_rule",
        "severity": "P1",
        "method": "GET",
        "path": path,
        "source": "real_project_pattern",
        "execution_policy": execution_policy,
        "priority_score": priority_score,
        "validated_yield_priority_score": validated_yield_priority_score,
        "validated_yield_signal_level": signal_level,
        "strict_validation_ready": strict_validation_ready,
        "repro_ready_signal": strict_validation_ready,
        "evidence_ready_signal": evidence_ready_signal,
    }


def test_score_probe_explicitly_prefers_strictly_verifiable_output() -> None:
    direct_probe = {
        "probe_id": "direct",
        "risk_type": "business_rule",
        "severity": "P3",
        "method": "GET",
        "path": "/api/orders",
        "source": "real_project_pattern",
        "execution_policy": "direct",
    }
    candidate_only_probe = {
        **direct_probe,
        "probe_id": "candidate-only",
        "execution_policy": "candidate_only",
    }

    direct_score = planner._score_probe(direct_probe, {}, [])
    candidate_only_score = planner._score_probe(candidate_only_probe, {}, [])

    assert direct_score["validated_yield_priority_score"] > candidate_only_score["validated_yield_priority_score"]
    assert direct_score["priority_score"] > candidate_only_score["priority_score"]
    assert direct_score["strict_validation_ready"] is True
    assert candidate_only_score["strict_validation_ready"] is False
    assert direct_score["validated_yield_signal_level"] in {"moderate", "strong"}
    assert "可直接进入严格验证" in direct_score["validated_yield_priority_reasons"]


def test_budget_selection_and_summary_prefer_validated_yield_over_candidate_scale() -> None:
    strong_probe = _probe(
        "validated-top",
        priority_score=0.8,
        validated_yield_priority_score=0.22,
        execution_policy="direct",
        strict_validation_ready=True,
        evidence_ready_signal=True,
        signal_level="strong",
        path="/api/orders/validated-top",
    )
    weak_probes = [
        _probe(
            f"candidate-{index}",
            priority_score=0.8,
            validated_yield_priority_score=-0.12,
            execution_policy="candidate_only",
            strict_validation_ready=False,
            evidence_ready_signal=False,
            signal_level="weak",
            path=f"/api/orders/candidate-{index}",
        )
        for index in range(12)
    ]
    combined = [strong_probe, *weak_probes]

    selected, skipped = planner._select_probes_by_budget(
        combined,
        mode="safe",
        allow_destructive=False,
        budget={"risk_budget": {"business_rule": 1}},
        max_count=1,
    )
    summary = planner._summarize_validated_yield_priority(combined, selected)

    assert [probe["probe_id"] for probe in selected] == ["validated-top"]
    assert len(skipped) == 0
    assert summary["preference_target"] == "validated_yield"
    assert summary["deprioritized_proxy"] == "candidate_scale"
    assert summary["selection_prefers_strictly_verifiable_output"] is True
    assert summary["selected_average_score"] > summary["candidate_average_score"]
    assert summary["signal_level_selection_rate"]["strong"] == 1.0
    assert summary["signal_level_selection_rate"]["weak"] == 0.0
    assert summary["strict_validation_ready_selection_rate"] == 1.0
    assert summary["candidate_only_selection_rate"] == 0.0
    assert summary["strict_validation_ready_selected_count"] == 1
    assert summary["candidate_only_selected_count"] == 0
