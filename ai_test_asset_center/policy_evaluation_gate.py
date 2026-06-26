"""Strict champion/challenger promotion gate for discovery policies.

A policy candidate may be *generated* automatically, but it is never activated
from heuristic estimates. Promotion requires paired, observed replay and shadow
results plus safety/cleanup evidence. This module deliberately keeps the
requirements data-oriented so it can run in private customer deployments without
sending any evidence outside the environment.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any


@dataclass(frozen=True)
class PolicyRunMetrics:
    """Observed metrics from one policy run on a fixed dataset/environment."""

    confirmed_bugs: int = 0
    total_bugs: int = 0
    false_positives: int = 0
    hallucination_count: int = 0
    inconclusive_rate: float = 0.0
    engine_success_rate: float = 0.0
    evidence_quality_score: float = 0.0
    avg_evidence_depth: float = 0.0
    execution_success_rate: float = 0.0
    throughput_hypotheses_per_minute: float = 0.0
    reproducibility_rate: float = 0.0
    duplicate_rate: float = 0.0
    regression_failures: int = 0
    safety_incidents: int = 0
    production_http_requests: int = 0
    cleanup_failures: int = 0
    dirty_test_environments: int = 0
    sample_count: int = 0

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None) -> "PolicyRunMetrics":
        value = value or {}
        allowed = {item.name for item in fields(cls)}
        normalized: dict[str, Any] = {key: value[key] for key in allowed if key in value}
        # Legacy aliases kept at the edge only; the gate always operates on the
        # stable names above.
        normalized.setdefault("confirmed_bugs", int(value.get("confirmed_bugs", value.get("validated_candidates", 0)) or 0))
        normalized.setdefault("total_bugs", int(value.get("total_bugs", 0) or 0))
        normalized.setdefault("false_positives", int(value.get("false_positives", 0) or 0))
        normalized.setdefault("hallucination_count", int(value.get("hallucination_count", 0) or 0))
        normalized.setdefault("inconclusive_rate", float(value.get("inconclusive_rate", 0) or 0))
        normalized.setdefault("engine_success_rate", float((value.get("engine_health") or {}).get("success_rate", value.get("engine_success_rate", 0)) or 0))
        normalized.setdefault("evidence_quality_score", float(value.get("evidence_quality", value.get("evidence_quality_score", 0)) or 0))
        normalized.setdefault("avg_evidence_depth", float(value.get("avg_evidence_depth", 0) or 0))
        normalized.setdefault("execution_success_rate", float(value.get("execution_success_rate", 0) or 0))
        normalized.setdefault("throughput_hypotheses_per_minute", float(value.get("throughput", value.get("throughput_hypotheses_per_minute", 0)) or 0))
        normalized.setdefault("reproducibility_rate", float(value.get("reproducibility_rate", 0) or 0))
        normalized.setdefault("duplicate_rate", float(value.get("duplicate_rate", 0) or 0))
        normalized.setdefault("regression_failures", int(value.get("regression_failures", 0) or 0))
        normalized.setdefault("safety_incidents", int(value.get("safety_incidents", 0) or 0))
        normalized.setdefault("production_http_requests", int(value.get("production_http_requests", value.get("production_http_count", 0)) or 0))
        normalized.setdefault("cleanup_failures", int(value.get("cleanup_failures", 0) or 0))
        normalized.setdefault("dirty_test_environments", int(value.get("dirty_test_environments", value.get("dirty_environment_count", 0)) or 0))
        normalized.setdefault("sample_count", int(value.get("sample_count", 0) or 0))
        return cls(**normalized)


@dataclass(frozen=True)
class EvaluationEvidence:
    """Evidence that the candidate was exercised, rather than guessed."""

    replay_executed: bool = False
    shadow_executed: bool = False
    dataset_version: str = ""
    replay_run_ids: tuple[str, ...] = ()
    shadow_run_ids: tuple[str, ...] = ()
    same_input_fingerprint: str = ""
    same_fixture_fingerprint: str = ""
    same_context_artifact_id: str = ""
    same_environment_id: str = ""

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None) -> "EvaluationEvidence":
        value = value or {}
        return cls(
            replay_executed=bool(value.get("replay_executed", value.get("executed", False))),
            shadow_executed=bool(value.get("shadow_executed", False)),
            dataset_version=str(value.get("dataset_version") or ""),
            replay_run_ids=tuple(str(x) for x in (value.get("replay_run_ids") or [])),
            shadow_run_ids=tuple(str(x) for x in (value.get("shadow_run_ids") or [])),
            same_input_fingerprint=str(value.get("same_input_fingerprint") or ""),
            same_fixture_fingerprint=str(value.get("same_fixture_fingerprint") or ""),
            same_context_artifact_id=str(value.get("same_context_artifact_id") or ""),
            same_environment_id=str(value.get("same_environment_id") or ""),
        )


class PolicyPromotionGate:
    """Hard gate for policy promotion.

    It rejects absent evidence, safety violations, data-cleanup failures, and
    quality regressions before considering a candidate's incremental yield.
    """

    MIN_SAMPLES = 3

    def evaluate(
        self,
        champion: PolicyRunMetrics | dict[str, Any],
        challenger: PolicyRunMetrics | dict[str, Any],
        evidence: EvaluationEvidence | dict[str, Any],
    ) -> dict[str, Any]:
        champion = champion if isinstance(champion, PolicyRunMetrics) else PolicyRunMetrics.from_mapping(champion)
        challenger = challenger if isinstance(challenger, PolicyRunMetrics) else PolicyRunMetrics.from_mapping(challenger)
        evidence = evidence if isinstance(evidence, EvaluationEvidence) else EvaluationEvidence.from_mapping(evidence)

        hard_checks = [
            self._check("replay_executed", evidence.replay_executed, "candidate replay must execute"),
            self._check("shadow_executed", evidence.shadow_executed, "candidate shadow evaluation must execute"),
            self._check("dataset_version", bool(evidence.dataset_version), "evaluation dataset must be versioned"),
            self._check("same_input_fingerprint", bool(evidence.same_input_fingerprint), "champion and challenger must use identical inputs"),
            self._check("same_fixture_fingerprint", bool(evidence.same_fixture_fingerprint), "champion and challenger must use identical fixtures"),
            self._check("same_context_artifact_id", bool(evidence.same_context_artifact_id), "champion and challenger must use the same context artifact"),
            self._check("sample_count", challenger.sample_count >= self.MIN_SAMPLES, f"at least {self.MIN_SAMPLES} samples are required"),
            self._check("safety_incidents", challenger.safety_incidents == 0, "safety incidents block promotion", challenger.safety_incidents),
            self._check("production_http_requests", challenger.production_http_requests == 0, "production HTTP requests block promotion", challenger.production_http_requests),
            self._check("cleanup_failures", challenger.cleanup_failures == 0, "cleanup failures block promotion", challenger.cleanup_failures),
            self._check("dirty_test_environments", challenger.dirty_test_environments == 0, "dirty environments block promotion", challenger.dirty_test_environments),
            self._check("regression_failures", challenger.regression_failures == 0, "regression failures block promotion", challenger.regression_failures),
        ]

        quality_checks = [
            self._check("evidence_quality", challenger.evidence_quality_score >= champion.evidence_quality_score * 0.95,
                        "evidence quality may not drop more than 5%", challenger.evidence_quality_score),
            self._check("reproducibility", challenger.reproducibility_rate >= champion.reproducibility_rate * 0.95,
                        "reproducibility may not drop more than 5%", challenger.reproducibility_rate),
            self._check("false_positives", challenger.false_positives <= max(champion.false_positives, 1) * 1.2,
                        "false positives may not increase more than 20%", challenger.false_positives),
            self._check("hallucinations", challenger.hallucination_count <= max(champion.hallucination_count, 1) * 1.1,
                        "hallucinations may not increase more than 10%", challenger.hallucination_count),
            self._check("duplicate_rate", challenger.duplicate_rate <= champion.duplicate_rate + 0.03,
                        "duplicate rate may not materially worsen", challenger.duplicate_rate),
            self._check("engine_success", challenger.engine_success_rate + 1e-9 >= champion.engine_success_rate - 0.03,
                        "engine availability may not materially regress", challenger.engine_success_rate),
        ]

        hard_passed = all(item["passed"] for item in hard_checks)
        quality_passed = all(item["passed"] for item in quality_checks)
        yield_improved = challenger.confirmed_bugs > champion.confirmed_bugs or (
            challenger.confirmed_bugs == champion.confirmed_bugs
            and challenger.inconclusive_rate < champion.inconclusive_rate
            and challenger.execution_success_rate >= champion.execution_success_rate
        )
        promote = hard_passed and quality_passed and yield_improved
        if not hard_passed:
            reason = "BLOCKED_BY_SAFETY_OR_EVALUATION_EVIDENCE"
        elif not quality_passed:
            reason = "REJECTED_QUALITY_REGRESSION"
        elif not yield_improved:
            reason = "HOLD_NO_MEASURED_IMPROVEMENT"
        else:
            reason = "PROMOTE_MEASURED_SAFE_IMPROVEMENT"

        return {
            "promote": promote,
            "reason": reason,
            "hard_checks": hard_checks,
            "quality_checks": quality_checks,
            "champion": asdict(champion),
            "challenger": asdict(challenger),
            "evidence": asdict(evidence),
            "deltas": {
                "confirmed_bugs": challenger.confirmed_bugs - champion.confirmed_bugs,
                "inconclusive_rate": challenger.inconclusive_rate - champion.inconclusive_rate,
                "evidence_quality_score": challenger.evidence_quality_score - champion.evidence_quality_score,
                "reproducibility_rate": challenger.reproducibility_rate - champion.reproducibility_rate,
                "duplicate_rate": challenger.duplicate_rate - champion.duplicate_rate,
                "engine_success_rate": challenger.engine_success_rate - champion.engine_success_rate,
            },
        }

    @staticmethod
    def _check(name: str, passed: bool, detail: str, value: Any = None) -> dict[str, Any]:
        return {"name": name, "passed": bool(passed), "detail": detail, "value": value}
