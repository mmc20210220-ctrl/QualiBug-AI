"""
Phase81: Evolution Orchestrator — state machine + champion/challenger.

Unifies the existing Loops under a single evolution framework.
Prevents false evolution (lowering evidence bar to boost numbers).
"""

from __future__ import annotations

import calendar, json, time, hashlib, os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .policy_registry import (
    PolicyRegistry, PolicyRecord, StrategyBundle,
    get_policy_registry, get_active_policy,
)


# ═══════════════════════════════════════════════════════════════════
# Evolution Job State Machine
# ═══════════════════════════════════════════════════════════════════

VALID_STATES = {
    "PLANNED", "COLLECTING_SIGNALS", "DIAGNOSING",
    "CANDIDATE_GENERATED", "VALIDATING_CANDIDATE",
    "REPLAY_EVALUATING", "SHADOW_EVALUATING", "COMPARING",
    "PROMOTED", "ACTIVE_MONITORING", "ROLLED_BACK",
    "BLOCKED_BY_SAFETY", "BLOCKED_BY_CONFIGURATION",
    "FAILED_RETRYABLE", "FAILED_TERMINAL", "CANCELLED",
}

VALID_TRANSITIONS = {
    "PLANNED": {"COLLECTING_SIGNALS", "CANCELLED"},
    "COLLECTING_SIGNALS": {"DIAGNOSING", "FAILED_RETRYABLE", "CANCELLED"},
    "DIAGNOSING": {"CANDIDATE_GENERATED", "BLOCKED_BY_CONFIGURATION", "FAILED_RETRYABLE"},
    "CANDIDATE_GENERATED": {"VALIDATING_CANDIDATE", "CANCELLED"},
    "VALIDATING_CANDIDATE": {"REPLAY_EVALUATING", "BLOCKED_BY_SAFETY", "FAILED_RETRYABLE"},
    "REPLAY_EVALUATING": {"SHADOW_EVALUATING", "FAILED_RETRYABLE"},
    "SHADOW_EVALUATING": {"COMPARING", "FAILED_RETRYABLE"},
    "COMPARING": {"PROMOTED", "CANCELLED", "FAILED_RETRYABLE"},
    "PROMOTED": {"ACTIVE_MONITORING", "ROLLED_BACK"},
    "ACTIVE_MONITORING": {"COLLECTING_SIGNALS", "ROLLED_BACK", "CANCELLED"},
    "ROLLED_BACK": {"COLLECTING_SIGNALS", "CANCELLED"},
    "BLOCKED_BY_SAFETY": {"CANCELLED", "PLANNED"},
    "BLOCKED_BY_CONFIGURATION": {"CANCELLED", "PLANNED"},
    "FAILED_RETRYABLE": {"PLANNED", "FAILED_TERMINAL"},
    "FAILED_TERMINAL": set(),
    "CANCELLED": set(),
}


@dataclass
class EvolutionJob:
    job_id: str
    project_id: str
    current_policy_version: str
    candidate_policy_version: str
    state: str = "PLANNED"
    trigger_reason: str = ""
    signals: list[dict] = field(default_factory=list)
    evaluation_dataset_version: str = ""
    evaluation_result: dict = field(default_factory=dict)
    promotion_decision: str = ""
    rollback_reason: str = ""
    started_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    ended_at: str = ""
    retry_count: int = 0
    lease_owner: str = ""
    lease_expires_at: str = ""

    def transition(self, new_state: str) -> bool:
        if new_state not in VALID_STATES:
            raise ValueError(f"Invalid state: {new_state}")
        allowed = VALID_TRANSITIONS.get(self.state, set())
        if new_state not in allowed:
            raise ValueError(f"Cannot transition {self.state} → {new_state}. Allowed: {allowed}")
        self.state = new_state
        if new_state in ("PROMOTED", "FAILED_TERMINAL", "CANCELLED", "ROLLED_BACK"):
            self.ended_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return True

    def acquire_lease(self, owner: str, ttl_seconds: int = 300) -> bool:
        now = time.time()
        if self.lease_owner and self.lease_expires_at:
            expiry = calendar.timegm(
                time.strptime(self.lease_expires_at, "%Y-%m-%dT%H:%M:%SZ")
            )
            if now < expiry and self.lease_owner != owner:
                return False  # Still leased by someone else
        self.lease_owner = owner
        self.lease_expires_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + ttl_seconds))
        return True

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id, "project_id": self.project_id,
            "current_policy_version": self.current_policy_version,
            "candidate_policy_version": self.candidate_policy_version,
            "state": self.state, "trigger_reason": self.trigger_reason,
            "signals": self.signals,
            "evaluation_dataset_version": self.evaluation_dataset_version,
            "evaluation_result": self.evaluation_result,
            "promotion_decision": self.promotion_decision,
            "rollback_reason": self.rollback_reason,
            "started_at": self.started_at, "ended_at": self.ended_at,
            "retry_count": self.retry_count,
            "lease_owner": self.lease_owner, "lease_expires_at": self.lease_expires_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EvolutionJob":
        return cls(**{k: d.get(k, "") for k in cls.__dataclass_fields__})


# ═══════════════════════════════════════════════════════════════════
# Champion vs Challenger Evaluator
# ═══════════════════════════════════════════════════════════════════

@dataclass
class EvaluationMetrics:
    """Metrics that matter. False evolution is when these improve
    but the underlying bug-finding quality degrades."""
    total_bugs: int = 0
    confirmed_bugs: int = 0
    false_positives: int = 0
    inconclusive_rate: float = 0.0
    engine_success_rate: float = 0.0
    evidence_quality_score: float = 0.0  # % of confirmed bugs with runtime evidence
    avg_evidence_depth: float = 0.0  # avg number of evidence pieces per confirmed bug
    hallucination_count: int = 0
    execution_success_rate: float = 0.0
    throughput_hypotheses_per_minute: float = 0.0


class ChampionChallengerEvaluator:
    """Compare two policies against a shared evaluation dataset.

    Key anti-cheat: if 'confirmed_bugs' increased but 'evidence_quality_score'
    dropped, that's FALSE EVOLUTION — the system is just lowering the bar.
    """

    ANTI_CHEAT_RULES = [
        # (check, description) — if check fails, promotion is BLOCKED
        ("evidence_quality_score >= baseline * 0.95",
         "Evidence quality must not drop more than 5%"),
        ("false_positives <= baseline * 1.2",
         "False positives must not increase more than 20%"),
        ("hallucination_count <= baseline * 1.1",
         "Hallucinations must not increase more than 10%"),
    ]

    def evaluate(
        self,
        champion_metrics: EvaluationMetrics,
        challenger_metrics: EvaluationMetrics,
    ) -> dict:
        """Compare challenger vs champion. Returns promotion decision."""
        result = {
            "promote": False,
            "reason": "",
            "champion": champion_metrics.__dict__,
            "challenger": challenger_metrics.__dict__,
            "deltas": {},
            "anti_cheat_checks": [],
        }

        # Compute deltas
        for field in EvaluationMetrics.__dataclass_fields__:
            c_val = getattr(champion_metrics, field)
            ch_val = getattr(challenger_metrics, field)
            result["deltas"][field] = ch_val - c_val

        # Primary metric: confirmed bugs with quality guard
        bugs_delta = result["deltas"]["confirmed_bugs"]
        inconc_delta = result["deltas"]["inconclusive_rate"]

        # Anti-cheat: evidence quality must not degrade
        for rule_expr, desc in self.ANTI_CHEAT_RULES:
            # Parse: "field op baseline * multiplier"
            parts = rule_expr.split()
            field = parts[0]
            op = parts[1]  # >= or <=
            # Parse threshold expression like "baseline * 0.95" or "baseline * 1.2"
            baseline_val = getattr(champion_metrics, field)
            actual_val = getattr(challenger_metrics, field)
            # Extract multiplier: last numeric token in the expression
            try:
                multiplier = float(parts[-1])
            except ValueError:
                multiplier = 1.0
            threshold = baseline_val * multiplier

            if op == ">=":
                passed = actual_val >= threshold
            elif op == "<=":
                passed = actual_val <= threshold
            else:
                passed = True  # Unknown op, skip

            result["anti_cheat_checks"].append({
                "rule": desc, "baseline": baseline_val, "actual": actual_val,
                "threshold": round(threshold, 4), "passed": passed,
            })

        anti_cheat_passed = all(c["passed"] for c in result["anti_cheat_checks"])

        if bugs_delta > 0 and inconc_delta < 0 and anti_cheat_passed:
            result["promote"] = True
            result["reason"] = f"More bugs (+{bugs_delta}) with lower inconclusive ({inconc_delta:+.1%}) and quality preserved"
        elif bugs_delta > 0 and not anti_cheat_passed:
            result["promote"] = False
            result["reason"] = "FALSE EVOLUTION: bug count increased but evidence quality degraded"
        elif inconc_delta >= 0:
            result["promote"] = False
            result["reason"] = f"Inconclusive rate did not improve ({inconc_delta:+.1%})"
        else:
            result["promote"] = False
            result["reason"] = "No significant improvement in confirmed bugs"

        return result


# ═══════════════════════════════════════════════════════════════════
# Evolution Orchestrator
# ═══════════════════════════════════════════════════════════════════

class EvolutionOrchestrator:
    """Unified orchestrator for autonomous policy evolution.

    Replaces the fragmented run_loop1/run_loop2/run_continuous_loop/run_self_improving
    with a single entry point that manages the full evolution lifecycle.
    """

    def __init__(self, project_id: str = "real_project_demo", root: Path | None = None):
        self.project_id = project_id
        self.registry = get_policy_registry()
        self.evaluator = ChampionChallengerEvaluator()

    def collect_signals(self, loop_result: dict) -> list[dict]:
        """Extract evolution signals from a discovery loop result."""
        signals = []
        report = loop_result.get("engine_health", {})

        # Engine failures
        failed = report.get("failed_engines", [])
        if failed:
            signals.append({
                "type": "engine_failure",
                "severity": "high",
                "detail": f"{len(failed)} engines failed: {failed}",
                "data": {"failed_engines": failed},
            })

        # High inconclusive rate
        inconc_rate = loop_result.get("inconclusive_rate", 0)
        if inconc_rate > 0.70:
            signals.append({
                "type": "high_inconclusive",
                "severity": "high",
                "detail": f"Inconclusive rate {inconc_rate:.0%} > 70%",
                "data": {"rate": inconc_rate},
            })

        raw_confirmed = int(loop_result.get("raw_confirmed_signals", 0) or 0)
        needs_more = int(loop_result.get("needs_more_evidence", 0) or 0)
        validated = int(loop_result.get("validated_candidates", 0) or 0)
        if raw_confirmed > 0 and needs_more >= max(1, raw_confirmed // 2) and validated == 0:
            signals.append({
                "type": "evidence_gate_gap",
                "severity": "high",
                "detail": f"{raw_confirmed} runtime-confirmed signals are blocked at needs_more_evidence",
                "data": {"raw_confirmed_signals": raw_confirmed, "needs_more_evidence": needs_more, "validated_candidates": validated},
            })

        # Hallucinations
        h_count = loop_result.get("hallucination_count", 0)
        if h_count > 0:
            signals.append({
                "type": "hallucination",
                "severity": "medium",
                "detail": f"{h_count} hallucinated findings",
                "data": {"count": h_count},
            })

        # Low engine output
        low_output = report.get("engines_with_low_output", [])
        if low_output:
            signals.append({
                "type": "low_engine_output",
                "severity": "low",
                "detail": f"Engines with <3 hypotheses: {low_output}",
                "data": {"engines": low_output},
            })

        return signals

    def should_evolve(self, signals: list[dict]) -> bool:
        """Decide whether to trigger an evolution cycle."""
        if not signals:
            return False
        high_severity = [s for s in signals if s["severity"] == "high"]
        return len(high_severity) >= 1 or len(signals) >= 3

    def generate_candidate(self, signals: list[dict], current: StrategyBundle) -> StrategyBundle:
        """Generate a candidate policy from signals. NEVER lowers evidence bar."""
        import copy
        candidate = copy.deepcopy(current)

        for signal in signals:
            stype = signal["type"]

            if stype == "engine_failure":
                # Try adding more retries or adjusting timeout
                candidate.execution.max_tokens = max(candidate.execution.max_tokens, 32768)
                candidate.reasoner.retry_count = min(candidate.reasoner.retry_count + 1, 3)
                candidate.reasoner.timeout_seconds = max(
                    candidate.reasoner.timeout_seconds + 30, 300
                )

            elif stype == "high_inconclusive":
                # Evidence is incomplete, not too strict.  Improve collection
                # coverage and async observation; never lower the verifier bar.
                candidate.verification.verifier_relaxed = False
                candidate.verification.async_window_seconds = min(
                    max(candidate.verification.async_window_seconds, 5) + 5, 60
                )
                order = list(candidate.verification.evidence_collection_order or [])
                for required in ("binding", "before_after", "multi_observer", "async_settlement"):
                    if required not in order:
                        order.append(required)
                candidate.verification.evidence_collection_order = order
                candidate.reasoner.max_hypotheses_per_engine = min(
                    candidate.reasoner.max_hypotheses_per_engine, 15
                )

            elif stype == "evidence_gate_gap":
                # Strong runtime evidence exists but the business evidence package
                # is not complete enough for customer-visible validated candidates.
                candidate.verification.verifier_relaxed = False
                order = list(candidate.verification.evidence_collection_order or [])
                for required in ("auth_boundary_matrix", "response_sensitivity", "reproduction_trace"):
                    if required not in order:
                        order.append(required)
                candidate.verification.evidence_collection_order = order

            elif stype == "hallucination":
                # Reduce hypothesis count to focus quality
                candidate.reasoner.max_hypotheses_per_engine = max(
                    candidate.reasoner.max_hypotheses_per_engine - 3, 5)
                candidate.discovery.dedicated_threshold = min(
                    candidate.discovery.dedicated_threshold + 0.05, 0.95)

            elif stype == "low_engine_output":
                candidate.reasoner.max_hypothesis_chars = min(
                    candidate.reasoner.max_hypothesis_chars + 100, 1000)

        return candidate

    def evaluate_and_promote_candidate(
        self,
        *,
        candidate_policy_id: str,
        champion_result: dict,
        challenger_result: dict,
        evaluation_evidence: dict,
    ) -> dict:
        """Evaluate supplied metrics without activating a policy.

        Mapping inputs are useful for diagnostics but are not independently
        authenticated artifacts. Activation is therefore reserved for
        ``evaluate_and_promote_observed_candidate``.
        """
        from .policy_evaluation_gate import PolicyPromotionGate

        gate = PolicyPromotionGate()
        decision = gate.evaluate(champion_result, challenger_result, evaluation_evidence)
        candidate = self.registry._policies.get(candidate_policy_id)
        if candidate is None:
            raise ValueError(f"Policy {candidate_policy_id} not found")
        candidate.evaluation_summary = decision
        candidate.status = "candidate"
        self.registry._save()
        if decision["promote"]:
            decision = {
                **decision,
                "promote": False,
                "reason": "BLOCKED_UNAUTHENTICATED_METRIC_INPUT_USE_OBSERVED_RUNNER",
                "diagnostic_gate_would_promote": True,
            }
        return {**decision, "policy_id": candidate_policy_id, "status": "candidate"}

    def evaluate_and_promote_observed_candidate(
        self,
        *,
        candidate_policy_id: str,
        manifest_path: str,
        output_root: str,
        fixture_controller: object,
        scan_executor: object,
        trusted_observation_provider: object,
        evaluation_id: str | None = None,
    ) -> dict:
        """Execute the evaluator-owned four-pass contract, then apply its gate.

        The registry remains unchanged while replay/shadow runs execute. Only a
        measured, non-regressive decision from ``DiscoveryPolicyEvaluationRunner``
        is eligible for the existing candidate -> champion -> active transition.
        """
        from .discovery_policy_evaluation_runner import DiscoveryPolicyEvaluationRunner

        candidate = self.registry._policies.get(candidate_policy_id)
        if candidate is None:
            raise ValueError(f"Policy {candidate_policy_id} not found")
        champion = self.registry.get_active()
        if champion is None:
            raise RuntimeError("No active champion policy is available for observed evaluation")
        runner = DiscoveryPolicyEvaluationRunner(
            manifest_path,
            output_root=output_root,
            fixture_controller=fixture_controller,
            scan_executor=scan_executor,
            trusted_observation_provider=trusted_observation_provider,
        )
        comparison = runner.run(
            champion=champion,
            challenger=candidate,
            evaluation_id=evaluation_id,
        )
        decision = comparison["promotion_decision"]
        comparison_path = Path(comparison["comparison_ref"])
        candidate.evaluation_summary = {
            "schema_version": comparison["schema_version"],
            "comparison_ref": comparison["comparison_ref"],
            "comparison_file_sha256": hashlib.sha256(comparison_path.read_bytes()).hexdigest(),
            "dataset_manifest_fingerprint": comparison["dataset_manifest_fingerprint"],
            "strategy_fingerprint": comparison["challenger"]["strategy_fingerprint"],
            "observed_execution": comparison["observed_execution"],
            "estimated_metrics_used": comparison["estimated_metrics_used"],
            **decision,
        }
        self.registry._save()
        if not decision["promote"]:
            return {
                **comparison,
                "activation_performed": False,
                "active_policy_id": champion.policy_id,
                "candidate_status": candidate.status,
            }

        champion_record = self.registry.promote(
            candidate_policy_id, decision["reason"]
        )
        if champion_record.status != "champion":
            raise RuntimeError("candidate_to_champion_transition_failed")
        promoted = self.registry.promote(
            candidate_policy_id, decision["reason"]
        )
        if promoted.status != "active":
            raise RuntimeError("champion_to_active_transition_failed")
        return {
            **comparison,
            "activation_performed": True,
            "active_policy_id": promoted.policy_id,
            "candidate_status": promoted.status,
        }

    def evaluate_candidate(
        self, champion_result: dict, challenger_result: dict
    ) -> dict:
        """Compare two loop results using ChampionChallenger rules."""
        def _to_metrics(r: dict) -> EvaluationMetrics:
            report = r.get("engine_health", {})
            return EvaluationMetrics(
                total_bugs=r.get("total_bugs", 0),
                confirmed_bugs=r.get("confirmed_bugs", r.get("total_bugs", 0)),
                false_positives=r.get("false_positives", r.get("hallucination_count", 0)),
                inconclusive_rate=r.get("inconclusive_rate", 0),
                engine_success_rate=report.get("success_rate", 0),
                evidence_quality_score=r.get("evidence_quality", 0.5),
                hallucination_count=r.get("hallucination_count", 0),
                execution_success_rate=r.get("execution_success_rate", 0),
                throughput_hypotheses_per_minute=r.get("throughput", 0),
            )

        champion = _to_metrics(champion_result)
        challenger = _to_metrics(challenger_result)
        return self.evaluator.evaluate(champion, challenger)


# ═══════════════════════════════════════════════════════════════════
# Unified entry point — replaces old run_* scripts
# ═══════════════════════════════════════════════════════════════════

def run_evolution_orchestrated(
    project_id: str = "real_project_demo",
    max_evolution_cycles: int = 1,
    *,
    evaluation_manifest_path: str | Path | None = None,
    evaluation_output_root: str | Path | None = None,
    workspace_root: str | Path | None = None,
    trusted_observation_root: str | Path | None = None,
) -> dict:
    """Run discovery loop + evolution cycle under unified orchestration.

    This is the single entry point that replaces:
      run_self_improving.py / run_loop1_sweep.py / run_loop2_improve.py
      / run_continuous_loop.py

    Workflow:
      1. Load active policy from registry
      2. Run discovery loop with active policy
      3. Collect evolution signals from results
      4. If signals warrant evolution: generate candidate, evaluate, promote/rollback
      5. Return full report with policy changes

    When ``evaluation_manifest_path`` (or env ``QUALIBUG_EVALUATION_MANIFEST``)
    points at an evaluator-private commercial-shape manifest, each candidate is
    evaluated through the observed fixture controller + scan executor path.
    Without a private manifest the cycle remains ``AWAITING_OBSERVED_REPLAY_SHADOW``.
    """
    result = {
        "orchestrator_version": "phase81-v1",
        "project_id": project_id,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "discovery_result": None,
        "evolution": None,
        "policy_changes": [],
        "ended_at": "",
    }

    orch = EvolutionOrchestrator(project_id=project_id)

    # 1. Load active policy
    registry = get_policy_registry()
    active = registry.get_active()
    current_version = active.policy_version if active else "v1.0.0-baseline"
    result["active_policy_version"] = current_version

    # 2. Run discovery loop (with active policy applied via policy_wiring)
    try:
        from .self_improving_loop import run_self_improving
        discovery_result = run_self_improving()
        result["discovery_result"] = discovery_result
    except Exception as e:
        result["error"] = f"Discovery loop failed: {e}"
        result["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return result

    # Failed or skipped discovery may not mutate evolution state.  Runtime faults
    # are not evidence that a discovery strategy should be changed.
    terminal = str(discovery_result.get("terminal", ""))
    if terminal.startswith("FAILED") or terminal == "SKIPPED_ALREADY_RUNNING":
        result["evolution"] = {
            "triggered": False,
            "reason": f"Discovery terminal={terminal}; policy evolution blocked",
        }
        result["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return result

    # 3. Collect signals
    signals = orch.collect_signals(discovery_result)
    result["signals"] = signals

    if not orch.should_evolve(signals):
        result["evolution"] = {"triggered": False, "reason": "No evolution signals warrant action"}
        result["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return result

    # 4. Evolution cycle
    evolution_report = {"triggered": True, "cycles_completed": 0, "promotions": [], "rollbacks": []}
    active_strategy = registry.get_active_strategy()

    for cycle in range(max_evolution_cycles):
        # Generate candidate
        candidate = orch.generate_candidate(signals, active_strategy)

        # Register candidate
        candidate_record = PolicyRecord(
            policy_id=f"policy-evo-{int(time.time())}",
            policy_version=f"v{current_version}-candidate-{cycle+1}",
            parent_policy_version=current_version,
            project_scope="global",
            status="candidate",
            created_reason=f"Auto-generated from signals: {[s['type'] for s in signals]}",
            strategy=candidate,
        )
        registry.register(candidate_record)

        # A candidate must be evaluated by a separate replay/shadow gate before
        # promotion.  Never promote a policy merely because the generation code ran.
        manifest_path = Path(
            str(
                evaluation_manifest_path
                or os.environ.get("QUALIBUG_EVALUATION_MANIFEST")
                or ""
            ).strip()
        )
        if manifest_path.is_file():
            from .evaluation_fixture_controller import GovernedHttpResetFixtureController
            from .observed_product_scan_executor import ObservedProductScanExecutor
            from .scan_operational_metrics import collect_observed_scan_operational_metrics
            from .trusted_execution_observation_provider import (
                TrustedObservationDirectoryProvider,
            )

            workspace = Path(
                str(
                    workspace_root
                    or os.environ.get("QUALIBUG_WORKSPACE_ROOT")
                    or Path.cwd()
                )
            ).resolve()
            output_root = Path(
                str(
                    evaluation_output_root
                    or os.environ.get("QUALIBUG_EVALUATION_OUTPUT_ROOT")
                    or (workspace / "_evaluation_runs")
                )
            ).resolve()
            observation_root_value = (
                trusted_observation_root
                or os.environ.get("QUALIBUG_TRUSTED_OBSERVATION_ROOT")
            )
            if not observation_root_value:
                raise RuntimeError(
                    "QUALIBUG_TRUSTED_OBSERVATION_ROOT is required for "
                    "measured observed policy evaluation"
                )
            trusted_provider = TrustedObservationDirectoryProvider(
                observation_root=Path(str(observation_root_value)).resolve(),
                product_workspace_root=workspace,
            )
            controller = GovernedHttpResetFixtureController(workspace_root=workspace)
            executor = ObservedProductScanExecutor(
                workspace_root=workspace,
                operational_metrics_collector=collect_observed_scan_operational_metrics,
            )
            try:
                observed = orch.evaluate_and_promote_observed_candidate(
                    candidate_policy_id=candidate_record.policy_id,
                    manifest_path=str(manifest_path),
                    output_root=str(output_root),
                    fixture_controller=controller,
                    scan_executor=executor,
                    trusted_observation_provider=trusted_provider,
                )
            except Exception as exc:
                evolution_report.setdefault("candidates", []).append({
                    "policy_id": candidate_record.policy_id,
                    "version": candidate_record.policy_version,
                    "status": "FAILED_SAFE",
                    "changes": candidate.__dict__,
                    "reason": f"observed evaluation failed: {type(exc).__name__}: {exc}",
                    "promotion_block": "observed_replay_shadow_failed",
                })
                evolution_report["cycles_completed"] += 1
                continue
            evolution_report.setdefault("candidates", []).append({
                "policy_id": candidate_record.policy_id,
                "version": candidate_record.policy_version,
                "status": str(observed.get("promotion_decision") or "observed"),
                "changes": candidate.__dict__,
                "reason": "observed paired replay/shadow evaluation completed",
                "observed_evaluation": {
                    "evaluation_id": observed.get("evaluation_id"),
                    "comparison_ref": observed.get("comparison_ref"),
                    "promotion_decision": observed.get("promotion_decision"),
                    "activation_performed": bool(observed.get("activation_performed")),
                },
            })
            if observed.get("activation_performed"):
                evolution_report["promotions"].append(candidate_record.policy_id)
                active_strategy = registry.get_active_strategy()
            evolution_report["cycles_completed"] += 1
            continue

        evaluation_request = {
            "schema_version": "qualibug.discovery-policy-evaluation-request.v1",
            "status": "AWAITING_OBSERVED_REPLAY_SHADOW",
            "runner": "DiscoveryPolicyEvaluationRunner",
            "requires_evaluator_private_manifest": True,
            "requires_governed_fixture_controller": True,
            "requires_observed_scan_executor": True,
            "estimated_metrics_allowed": False,
            "hint": (
                "Supply evaluation_manifest_path or QUALIBUG_EVALUATION_MANIFEST "
                "built by tools/build_discovery_evaluation_dataset.py"
            ),
        }
        evolution_report.setdefault("candidates", []).append({
            "policy_id": candidate_record.policy_id,
            "version": candidate_record.policy_version,
            "status": "candidate",
            "changes": candidate.__dict__,
            "reason": "awaiting paired replay and shadow evaluation",
            "evaluation_request": evaluation_request,
            "promotion_block": "PolicyPromotionGate requires observed replay/shadow evidence",
        })
        evolution_report["cycles_completed"] += 1

    result["evolution"] = evolution_report
    result["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return result
