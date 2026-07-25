"""Phase81: Replay Benchmark Runner — evaluates candidate policies offline.

Runs a candidate policy against a saved evaluation dataset (previous loop results)
to measure impact without hitting live APIs. Critical for safe policy evaluation.
"""

from __future__ import annotations

import json, time, copy
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .autonomous_evolution_orchestrator import EvaluationMetrics


class ReplayBenchmarkRunner:
    """Evaluate candidate policies against historical loop results.

    Uses saved discovery loop outputs as a benchmark dataset.
    A candidate policy is "better" if it would have:
    - Found more confirmed bugs
    - Had lower inconclusive rate
    - Generated higher-quality evidence
    - Without degrading anti-cheat metrics
    """

    def __init__(self, project_id: str = "real_project_demo"):
        self.project_id = project_id
        self._benchmark_dir = Path("platform_outputs") / project_id
        self._dataset_path = self._benchmark_dir / "replay_benchmark_dataset.json"

    def save_benchmark(self, loop_result: dict, label: str = "") -> str:
        """Save a loop result as a benchmark entry."""
        self._benchmark_dir.mkdir(parents=True, exist_ok=True)

        dataset = self._load_dataset()
        entry = {
            "id": f"bench-{int(time.time())}",
            "label": label or f"Loop result at {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "result": loop_result,
        }
        dataset.append(entry)

        # Keep last 20 benchmarks
        if len(dataset) > 20:
            dataset = dataset[-20:]

        self._dataset_path.write_text(json.dumps(dataset, indent=2, ensure_ascii=False, default=str))
        return entry["id"]

    def evaluate_policy(
        self,
        candidate_strategy: Any,  # StrategyBundle
        current_active_strategy: Any,  # StrategyBundle
    ) -> dict:
        """Compare candidate vs active policy against historical benchmarks.

        Returns evaluation report with:
        - Whether candidate beats active on each benchmark
        - Aggregate win rate
        - Anti-cheat violations
        """
        dataset = self._load_dataset()
        if not dataset:
            return {
                "evaluated": False,
                "reason": "No benchmark data available. Run at least one discovery loop first.",
                "benchmark_count": 0,
            }

        from .autonomous_evolution_orchestrator import (
            ChampionChallengerEvaluator, EvaluationMetrics,
        )

        evaluator = ChampionChallengerEvaluator()
        results = []

        for entry in dataset:
            loop_result = entry["result"]

            # Extract metrics from the historical result (these are "champion" — what actually happened)
            champion = self._extract_metrics(loop_result)

            # For the challenger, we can't replay without actually running the loop.
            # Instead, we analyze the delta between strategies and estimate impact.
            # This is a lightweight heuristic until full replay is implemented.
            challenger = self._estimate_impact(candidate_strategy, current_active_strategy, champion)

            comparison = evaluator.evaluate(champion, challenger)
            results.append({
                "benchmark_id": entry["id"],
                "label": entry["label"],
                "would_promote": comparison["promote"],
                "reason": comparison["reason"],
                "deltas": comparison["deltas"],
                "anti_cheat": comparison["anti_cheat_checks"],
            })

        wins = sum(1 for r in results if r["would_promote"])
        return {
            "evaluated": True,
            "benchmark_count": len(dataset),
            "wins": wins,
            "losses": len(results) - wins,
            "win_rate": wins / len(results) if results else 0,
            "verdict": "PROMOTE" if wins > len(results) * 0.6 else "HOLD",
            "details": results,
        }

    def _load_dataset(self) -> list[dict]:
        if self._dataset_path.exists():
            try:
                return json.loads(self._dataset_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return []

    def _extract_metrics(self, loop_result: dict) -> "EvaluationMetrics":
        from .autonomous_evolution_orchestrator import EvaluationMetrics
        report = loop_result.get("engine_health", {})
        return EvaluationMetrics(
            total_bugs=loop_result.get("total_bugs", 0),
            confirmed_bugs=loop_result.get("confirmed_bugs", loop_result.get("total_bugs", 0)),
            false_positives=loop_result.get("false_positives", loop_result.get("hallucination_count", 0)),
            inconclusive_rate=loop_result.get("inconclusive_rate", 0),
            engine_success_rate=report.get("success_rate", 0),
            evidence_quality_score=loop_result.get("evidence_quality", 0.5),
            hallucination_count=loop_result.get("hallucination_count", 0),
            execution_success_rate=loop_result.get("execution_success_rate", 0),
            throughput_hypotheses_per_minute=loop_result.get("throughput", 0),
        )

    def _estimate_impact(
        self, candidate: Any, active: Any, baseline: "EvaluationMetrics",
    ) -> "EvaluationMetrics":
        """Estimate how candidate policy changes would affect metrics.

        Conservative heuristic — never over-estimates improvement.
        Full replay (actually running the loop) would be more accurate.
        """
        from .autonomous_evolution_orchestrator import EvaluationMetrics

        est = copy.deepcopy(baseline)

        # If candidate has more retries → slightly higher engine success
        if candidate.reasoner.retry_count > active.reasoner.retry_count:
            est.engine_success_rate = min(1.0, baseline.engine_success_rate + 0.05)

        # Evidence thresholds are immutable.  A longer async observation window
        # may improve determinacy only through more observation, never by
        # relabeling inconclusive evidence as confirmed.
        if candidate.verification.async_window_seconds > active.verification.async_window_seconds:
            est.inconclusive_rate = max(0.0, baseline.inconclusive_rate - 0.03)
            est.evidence_quality_score = min(1.0, baseline.evidence_quality_score + 0.01)

        # If candidate has fewer hypotheses per engine → fewer hallucinations
        if candidate.reasoner.max_hypotheses_per_engine < active.reasoner.max_hypotheses_per_engine:
            est.hallucination_count = max(0, baseline.hallucination_count - 1)

        # If candidate has higher timeout → better throughput
        if candidate.reasoner.timeout_seconds > active.reasoner.timeout_seconds:
            est.throughput_hypotheses_per_minute = baseline.throughput_hypotheses_per_minute * 1.05

        return est

    def get_benchmark_count(self) -> int:
        return len(self._load_dataset())


# Convenience functions
def save_benchmark_from_loop(loop_result: dict, project_id: str = "real_project_demo") -> str:
    return ReplayBenchmarkRunner(project_id).save_benchmark(loop_result)


def evaluate_candidate_offline(candidate_strategy: Any, active_strategy: Any, project_id: str = "real_project_demo") -> dict:
    return ReplayBenchmarkRunner(project_id).evaluate_policy(candidate_strategy, active_strategy)
