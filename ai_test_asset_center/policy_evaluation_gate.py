"""Strict, observed champion/challenger promotion gate.

Heuristic estimates and internal ``validated`` counts are not promotion
evidence. A candidate must complete paired replay and shadow runs on the exact
same versioned held-in, held-out, and clean targets. Promotion follows a
non-regression rule: no measured discovery split may get worse and at least one
split must improve. Safety, cleanup, production-write, and dataset-integrity
checks are hard blockers.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any


@dataclass(frozen=True)
class PolicyRunMetrics:
    """Externally measured metrics for one policy across replay and shadow."""

    total_bugs: int = 0
    confirmed_bugs: int = 0
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    held_in_recall: float = 0.0
    held_in_precision: float = 0.0
    held_in_f1: float = 0.0
    held_out_recall: float = 0.0
    held_out_precision: float = 0.0
    held_out_f1: float = 0.0
    shadow_held_in_f1: float = 0.0
    shadow_held_out_f1: float = 0.0
    macro_industry_recall: float = 0.0
    min_industry_recall: float = 0.0
    unique_industry_count: int = 0
    clean_false_positives: int = 0
    clean_critical_high_false_positives: int = 0
    inconclusive_rate: float = 0.0
    engine_success_rate: float = 0.0
    evidence_quality_score: float = 0.0
    avg_evidence_depth: float = 0.0
    hallucination_count: int = 0
    execution_success_rate: float = 0.0
    throughput_hypotheses_per_minute: float = 0.0
    reproducibility_rate: float = 0.0
    duplicate_rate: float = 0.0
    regression_failures: int = 0
    safety_incidents: int = 0
    production_http_requests: int = 0
    cleanup_failures: int = 0
    dirty_test_environments: int = 0
    pipeline_degraded_targets: int = 0
    total_cost_usd: float = 0.0
    cost_per_true_positive_usd: float = 0.0
    wall_clock_seconds: float = 0.0
    sample_count: int = 0
    evaluation_complete: bool = False
    commercial_shape_ready: bool = False
    operational_metrics_complete: bool = False

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None) -> "PolicyRunMetrics":
        value = value or {}
        allowed = {item.name for item in fields(cls)}
        normalized: dict[str, Any] = {key: value[key] for key in allowed if key in value}
        # Legacy names are accepted only as input aliases. They cannot satisfy
        # the new completeness/paired-run hard gates by themselves.
        normalized.setdefault("confirmed_bugs", int(value.get("confirmed_bugs", value.get("validated_candidates", 0)) or 0))
        normalized.setdefault("total_bugs", int(value.get("total_bugs", 0) or 0))
        normalized.setdefault("false_positives", int(value.get("false_positives", 0) or 0))
        normalized.setdefault("hallucination_count", int(value.get("hallucination_count", 0) or 0))
        normalized.setdefault("inconclusive_rate", float(value.get("inconclusive_rate", 0) or 0))
        normalized.setdefault("engine_success_rate", float((value.get("engine_health") or {}).get("success_rate", value.get("engine_success_rate", 0)) or 0))
        normalized.setdefault("evidence_quality_score", float(value.get("evidence_quality", value.get("evidence_quality_score", 0)) or 0))
        normalized.setdefault("throughput_hypotheses_per_minute", float(value.get("throughput", value.get("throughput_hypotheses_per_minute", 0)) or 0))
        normalized.setdefault("sample_count", int(value.get("sample_count", 0) or 0))
        return cls(**normalized)


@dataclass(frozen=True)
class EvaluationEvidence:
    """Evidence that all paired runs used one frozen evaluation contract."""

    replay_executed: bool = False
    shadow_executed: bool = False
    held_in_executed: bool = False
    held_out_executed: bool = False
    clean_executed: bool = False
    dataset_version: str = ""
    dataset_manifest_fingerprint: str = ""
    replay_run_ids: tuple[str, ...] = ()
    shadow_run_ids: tuple[str, ...] = ()
    paired_target_count: int = 0
    same_runtime_fingerprint: str = ""
    same_input_fingerprint: str = ""
    same_fixture_fingerprint: str = ""
    same_context_artifact_id: str = ""
    same_environment_id: str = ""
    target_receipt_fingerprints: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None) -> "EvaluationEvidence":
        value = value or {}
        return cls(
            replay_executed=bool(value.get("replay_executed", value.get("executed", False))),
            shadow_executed=bool(value.get("shadow_executed", False)),
            held_in_executed=bool(value.get("held_in_executed", False)),
            held_out_executed=bool(value.get("held_out_executed", False)),
            clean_executed=bool(value.get("clean_executed", False)),
            dataset_version=str(value.get("dataset_version") or ""),
            dataset_manifest_fingerprint=str(value.get("dataset_manifest_fingerprint") or ""),
            replay_run_ids=tuple(str(item) for item in (value.get("replay_run_ids") or [])),
            shadow_run_ids=tuple(str(item) for item in (value.get("shadow_run_ids") or [])),
            paired_target_count=int(value.get("paired_target_count", 0) or 0),
            same_runtime_fingerprint=str(value.get("same_runtime_fingerprint") or ""),
            same_input_fingerprint=str(value.get("same_input_fingerprint") or ""),
            same_fixture_fingerprint=str(value.get("same_fixture_fingerprint") or ""),
            same_context_artifact_id=str(value.get("same_context_artifact_id") or ""),
            same_environment_id=str(value.get("same_environment_id") or ""),
            target_receipt_fingerprints=tuple(str(item) for item in (value.get("target_receipt_fingerprints") or [])),
        )


class PolicyPromotionGate:
    """Hard gate for measured, non-regressive discovery-harness evolution."""

    MIN_TARGETS = 3
    EPSILON = 1e-9

    def evaluate(
        self,
        champion: PolicyRunMetrics | dict[str, Any],
        challenger: PolicyRunMetrics | dict[str, Any],
        evidence: EvaluationEvidence | dict[str, Any],
    ) -> dict[str, Any]:
        champion = champion if isinstance(champion, PolicyRunMetrics) else PolicyRunMetrics.from_mapping(champion)
        challenger = challenger if isinstance(challenger, PolicyRunMetrics) else PolicyRunMetrics.from_mapping(challenger)
        evidence = evidence if isinstance(evidence, EvaluationEvidence) else EvaluationEvidence.from_mapping(evidence)

        expected_run_ids = evidence.paired_target_count * 2
        hard_checks = [
            self._check("replay_executed", evidence.replay_executed, "paired champion/challenger replay must execute"),
            self._check("shadow_executed", evidence.shadow_executed, "paired champion/challenger shadow evaluation must execute"),
            self._check("held_in_executed", evidence.held_in_executed, "held-in split must execute"),
            self._check("held_out_executed", evidence.held_out_executed, "held-out split must execute"),
            self._check("clean_executed", evidence.clean_executed, "an intentionally clean target must execute"),
            self._check("dataset_version", bool(evidence.dataset_version), "evaluation dataset must be versioned"),
            self._check("dataset_manifest_fingerprint", bool(evidence.dataset_manifest_fingerprint), "frozen evaluator-private manifest fingerprint is required"),
            self._check("same_runtime_fingerprint", bool(evidence.same_runtime_fingerprint), "champion/challenger runtime artifacts must match"),
            self._check("same_input_fingerprint", bool(evidence.same_input_fingerprint), "champion/challenger inputs must match"),
            self._check("same_fixture_fingerprint", bool(evidence.same_fixture_fingerprint), "champion/challenger fixtures must match"),
            self._check("same_context_artifact_id", bool(evidence.same_context_artifact_id), "champion/challenger context must match"),
            self._check("same_environment_id", bool(evidence.same_environment_id), "champion/challenger environment identities must match"),
            self._check("paired_target_count", evidence.paired_target_count >= self.MIN_TARGETS, f"at least {self.MIN_TARGETS} targets are required", evidence.paired_target_count),
            self._check("replay_run_receipts", expected_run_ids > 0 and len(evidence.replay_run_ids) == expected_run_ids, "every replay target needs champion and challenger run receipts", len(evidence.replay_run_ids)),
            self._check("shadow_run_receipts", expected_run_ids > 0 and len(evidence.shadow_run_ids) == expected_run_ids, "every shadow target needs champion and challenger run receipts", len(evidence.shadow_run_ids)),
            self._check("target_receipt_fingerprints", len(evidence.target_receipt_fingerprints) == evidence.paired_target_count, "every target needs an immutable receipt fingerprint", len(evidence.target_receipt_fingerprints)),
            self._check("evaluation_complete", challenger.evaluation_complete, "challenger evaluation must be complete"),
            self._check("commercial_shape_ready", challenger.commercial_shape_ready, "dataset must contain held-in, held-out, clean, and three held-out industries"),
            self._check("operational_metrics_complete", challenger.operational_metrics_complete, "cost, time, execution, cleanup, and safety metrics must be measured"),
            self._check("sample_count", challenger.sample_count >= evidence.paired_target_count * 2, "replay and shadow samples are required for every target", challenger.sample_count),
            self._check("industry_diversity", challenger.unique_industry_count >= 3, "at least three held-out industries must be measured", challenger.unique_industry_count),
            self._check("safety_incidents", challenger.safety_incidents == 0, "safety incidents block promotion", challenger.safety_incidents),
            self._check("production_http_requests", challenger.production_http_requests == 0, "production HTTP requests block promotion", challenger.production_http_requests),
            self._check("cleanup_failures", challenger.cleanup_failures == 0, "cleanup failures block promotion", challenger.cleanup_failures),
            self._check("dirty_test_environments", challenger.dirty_test_environments == 0, "dirty environments block promotion", challenger.dirty_test_environments),
            self._check("pipeline_degraded_targets", challenger.pipeline_degraded_targets == 0, "degraded target pipelines block commercial promotion", challenger.pipeline_degraded_targets),
            self._check("regression_failures", challenger.regression_failures == 0, "regression failures block promotion", challenger.regression_failures),
            self._check("clean_critical_high_false_positives", challenger.clean_critical_high_false_positives == 0, "P0/P1 false positives on clean targets block promotion", challenger.clean_critical_high_false_positives),
        ]

        quality_checks = [
            self._higher_or_equal("held_in_recall", champion.held_in_recall, challenger.held_in_recall),
            self._higher_or_equal("held_in_precision", champion.held_in_precision, challenger.held_in_precision),
            self._higher_or_equal("held_in_f1", champion.held_in_f1, challenger.held_in_f1),
            self._higher_or_equal("held_out_recall", champion.held_out_recall, challenger.held_out_recall),
            self._higher_or_equal("held_out_precision", champion.held_out_precision, challenger.held_out_precision),
            self._higher_or_equal("held_out_f1", champion.held_out_f1, challenger.held_out_f1),
            self._higher_or_equal("shadow_held_in_f1", champion.shadow_held_in_f1, challenger.shadow_held_in_f1),
            self._higher_or_equal("shadow_held_out_f1", champion.shadow_held_out_f1, challenger.shadow_held_out_f1),
            self._higher_or_equal("macro_industry_recall", champion.macro_industry_recall, challenger.macro_industry_recall),
            self._higher_or_equal("min_industry_recall", champion.min_industry_recall, challenger.min_industry_recall),
            self._higher_or_equal("evidence_quality", champion.evidence_quality_score, challenger.evidence_quality_score),
            self._higher_or_equal("reproducibility", champion.reproducibility_rate, challenger.reproducibility_rate),
            self._higher_or_equal("engine_success", champion.engine_success_rate, challenger.engine_success_rate),
            self._higher_or_equal("execution_success", champion.execution_success_rate, challenger.execution_success_rate),
            self._lower_or_equal("duplicate_rate", champion.duplicate_rate, challenger.duplicate_rate),
            self._bounded_cost(champion.cost_per_true_positive_usd, challenger.cost_per_true_positive_usd),
            self._bounded_runtime(champion.wall_clock_seconds, challenger.wall_clock_seconds),
        ]

        hard_passed = all(item["passed"] for item in hard_checks)
        quality_passed = all(item["passed"] for item in quality_checks)
        split_improvements = {
            "held_in_recall": challenger.held_in_recall > champion.held_in_recall + self.EPSILON,
            "held_in_f1": challenger.held_in_f1 > champion.held_in_f1 + self.EPSILON,
            "held_out_recall": challenger.held_out_recall > champion.held_out_recall + self.EPSILON,
            "held_out_f1": challenger.held_out_f1 > champion.held_out_f1 + self.EPSILON,
            "shadow_held_in_f1": challenger.shadow_held_in_f1 > champion.shadow_held_in_f1 + self.EPSILON,
            "shadow_held_out_f1": challenger.shadow_held_out_f1 > champion.shadow_held_out_f1 + self.EPSILON,
            "macro_industry_recall": challenger.macro_industry_recall > champion.macro_industry_recall + self.EPSILON,
            "min_industry_recall": challenger.min_industry_recall > champion.min_industry_recall + self.EPSILON,
        }
        measured_improvement = any(split_improvements.values())
        promote = hard_passed and quality_passed and measured_improvement
        if not hard_passed:
            reason = "BLOCKED_BY_SAFETY_OR_EVALUATION_EVIDENCE"
        elif not quality_passed:
            reason = "REJECTED_QUALITY_REGRESSION"
        elif not measured_improvement:
            reason = "HOLD_NO_MEASURED_SPLIT_IMPROVEMENT"
        else:
            reason = "PROMOTE_MEASURED_NON_REGRESSIVE_IMPROVEMENT"

        delta_fields = (
            "true_positives",
            "false_positives",
            "false_negatives",
            "held_in_recall",
            "held_in_precision",
            "held_in_f1",
            "held_out_recall",
            "held_out_precision",
            "held_out_f1",
            "shadow_held_in_f1",
            "shadow_held_out_f1",
            "macro_industry_recall",
            "min_industry_recall",
            "evidence_quality_score",
            "reproducibility_rate",
            "duplicate_rate",
            "cost_per_true_positive_usd",
            "wall_clock_seconds",
        )
        return {
            "promote": promote,
            "reason": reason,
            "hard_checks": hard_checks,
            "quality_checks": quality_checks,
            "split_improvements": split_improvements,
            "champion": asdict(champion),
            "challenger": asdict(challenger),
            "evidence": asdict(evidence),
            "deltas": {
                field: getattr(challenger, field) - getattr(champion, field)
                for field in delta_fields
            },
        }

    def _higher_or_equal(self, name: str, baseline: float, actual: float) -> dict[str, Any]:
        return self._check(name, actual + self.EPSILON >= baseline, f"{name} may not regress", actual, baseline)

    def _lower_or_equal(self, name: str, baseline: float, actual: float) -> dict[str, Any]:
        return self._check(name, actual <= baseline + self.EPSILON, f"{name} may not regress", actual, baseline)

    def _bounded_cost(self, baseline: float, actual: float) -> dict[str, Any]:
        passed = actual <= baseline * 1.10 + self.EPSILON if baseline > 0 else actual <= self.EPSILON
        return self._check("cost_per_true_positive", passed, "cost per true positive may not increase more than 10%", actual, baseline)

    def _bounded_runtime(self, baseline: float, actual: float) -> dict[str, Any]:
        passed = actual <= baseline * 1.20 + self.EPSILON if baseline > 0 else actual <= self.EPSILON
        return self._check("wall_clock_seconds", passed, "wall-clock time may not increase more than 20%", actual, baseline)

    @staticmethod
    def _check(
        name: str,
        passed: bool,
        detail: str,
        value: Any = None,
        baseline: Any = None,
    ) -> dict[str, Any]:
        return {
            "name": name,
            "passed": bool(passed),
            "detail": detail,
            "value": value,
            "baseline": baseline,
        }
