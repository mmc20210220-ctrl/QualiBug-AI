"""
A/B Testing Framework for Learning Strategy Comparison.

This module provides controlled experiments to compare different learning strategies:
- Baseline vs enhanced signal extraction
- Different probe generation algorithms
- Varying risk weight configurations
- Multiple optimization objectives

Key features:
- Controlled replay on identical input
- Shadow execution mode
- Statistical significance testing
- Promotion decision support

Usage:
    from .ab_test_framework import ABTestRunner
    
    runner = ABTestRunner(project="my_project")
    
    # Run A/B test
    results = runner.run_ab_test(
        baseline_strategy="baseline_v1",
        challenger_strategy="enhanced_signals_v2",
        replay_data=scan_results
    )
    
    # Get promotion recommendation
    if results.should_promote_challenger():
        print("Promote challenger strategy!")
"""
from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
import hashlib

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class ABTestResult:
    """Result of an A/B test."""
    test_id: str
    baseline_metrics: dict[str, float]
    challenger_metrics: dict[str, float]
    improvement_percentage: dict[str, float]
    statistical_significance: float  # p-value
    should_promote: bool
    recommendations: list[str]


class ABTestRunner:
    """Controlled A/B testing framework."""
    
    def __init__(self, project: str):
        self.project = project
        self.test_results_dir = REPO_ROOT / "_ab_test_results" / project
        self.test_results_dir.mkdir(parents=True, exist_ok=True)
        
        # Test configurations
        self.strategies = {
            "baseline_v1": self._run_baseline_strategy,
            "enhanced_signals_v2": self._run_enhanced_signals,
            "adaptive_weights_v1": self._run_adaptive_weights,
            "hybrid_approach_v1": self._run_hybrid_approach,
        }
        
    def run_ab_test(
        self,
        baseline_strategy: str,
        challenger_strategy: str,
        replay_data: dict[str, Any],
        n_replays: int = 5
    ) -> ABTestResult:
        """Run controlled A/B test with multiple replays."""
        if baseline_strategy not in self.strategies:
            raise ValueError(f"Unknown baseline strategy: {baseline_strategy}")
        if challenger_strategy not in self.strategies:
            raise ValueError(f"Unknown challenger strategy: {challenger_strategy}")
            
        # Generate unique test ID
        test_id = f"{baseline_strategy}_vs_{challenger_strategy}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        logger.info("Starting A/B test: %s", test_id)
        
        # Collect metrics across multiple replays
        baseline_metrics_list = []
        challenger_metrics_list = []
        
        for i in range(n_replays):
            logger.info("Replay %d/%d", i + 1, n_replays)
            
            # Run baseline
            baseline_result = self.strategies[baseline_strategy](replay_data)
            baseline_metrics_list.append(baseline_result["metrics"])
            
            # Run challenger (on identical input)
            challenger_result = self.strategies[challenger_strategy](replay_data)
            challenger_metrics_list.append(challenger_result["metrics"])
            
        # Aggregate and analyze
        aggregated_baseline = self._aggregate_metrics(baseline_metrics_list)
        aggregated_challenger = self._aggregate_metrics(challenger_metrics_list)
        
        # Calculate improvements
        improvements = {}
        key_metrics = ["recall_improvement", "signal_diversity", "cost_efficiency"]
        for metric in key_metrics:
            baseline_val = aggregated_baseline.get(metric, 0)
            challenger_val = aggregated_challenger.get(metric, 0)
            if baseline_val > 0:
                improvements[metric] = ((challenger_val - baseline_val) / baseline_val) * 100
            else:
                improvements[metric] = 100.0 if challenger_val > 0 else 0.0
                
        # Statistical significance (simplified t-test approximation)
        significance = self._calculate_significance(
            baseline_metrics_list, 
            challenger_metrics_list,
            key_metric="recall_improvement"
        )
        
        # Promotion decision
        should_promote = self._promotion_decision(
            improvements, 
            significance,
            aggregated_challenger
        )
        
        # Generate recommendations
        recommendations = self._generate_recommendations(
            improvements, 
            should_promote,
            aggregated_challenger
        )
        
        result = ABTestResult(
            test_id=test_id,
            baseline_metrics=aggregated_baseline,
            challenger_metrics=aggregated_challenger,
            improvement_percentage=improvements,
            statistical_significance=significance,
            should_promote=should_promote,
            recommendations=recommendations
        )
        
        # Save results
        self._save_test_result(result)
        
        return result
        
    def _run_baseline_strategy(self, data: dict) -> dict:
        """Baseline strategy: simple confirmed_bugs only."""
        # Placeholder - would integrate with actual learning pipeline
        return {
            "metrics": {
                "recall_improvement": 0.65,  # Baseline value
                "signal_diversity": 0.40,
                "cost_efficiency": 0.75,
            },
            "artifacts": []
        }
        
    def _run_enhanced_signals(self, data: dict) -> dict:
        """Enhanced signals strategy: 5 signal types."""
        # Placeholder - would use enhanced_learning_signals.py
        return {
            "metrics": {
                "recall_improvement": 0.78,  # Expected improvement
                "signal_diversity": 0.72,
                "cost_efficiency": 0.70,
            },
            "artifacts": ["enhanced_risk_profiles"]
        }
        
    def _run_adaptive_weights(self, data: dict) -> dict:
        """Adaptive weights strategy: runtime weight adjustment."""
        # Placeholder - would use runtime_adaptive_engine.py
        return {
            "metrics": {
                "recall_improvement": 0.82,
                "signal_diversity": 0.75,
                "cost_efficiency": 0.73,
            },
            "artifacts": ["adaptive_weight_config"]
        }
        
    def _run_hybrid_approach(self, data: dict) -> dict:
        """Hybrid approach: combine enhanced signals + adaptive weights."""
        # Placeholder - best expected performance
        return {
            "metrics": {
                "recall_improvement": 0.87,
                "signal_diversity": 0.80,
                "cost_efficiency": 0.76,
            },
            "artifacts": ["hybrid_config", "enhanced_profiles", "adaptive_weights"]
        }
        
    def _aggregate_metrics(self, metrics_list: list[dict[str, float]]) -> dict[str, float]:
        """Aggregate metrics across replays."""
        if not metrics_list:
            return {}
            
        aggregated = {}
        keys = metrics_list[0].keys()
        
        for key in keys:
            values = [m[key] for m in metrics_list]
            avg = sum(values) / len(values)
            std = (sum((v - avg) ** 2 for v in values) / len(values)) ** 0.5
            aggregated[key] = avg
            aggregated[f"{key}_std"] = std
            
        return aggregated
        
    def _calculate_significance(
        self, 
        baseline_list: list[dict], 
        challenger_list: list[dict],
        key_metric: str
    ) -> float:
        """Calculate statistical significance (simplified)."""
        # Extract values
        baseline_vals = [m[key_metric] for m in baseline_list]
        challenger_vals = [m[key_metric] for m in challenger_list]
        
        if len(baseline_vals) < 2 or len(challenger_vals) < 2:
            return 0.5  # Not enough data for significance testing
        
        # Simplified p-value calculation (would use proper t-test in production)
        mean_diff = sum(challenger_vals) / len(challenger_vals) - sum(baseline_vals) / len(baseline_vals)
        
        # Calculate pooled standard deviation with safety check
        baseline_mean = sum(baseline_vals) / len(baseline_vals)
        challenger_mean = sum(challenger_vals) / len(challenger_vals)
        
        baseline_var = sum((v - baseline_mean)**2 for v in baseline_vals) / (len(baseline_vals) - 1)
        challenger_var = sum((v - challenger_mean)**2 for v in challenger_vals) / (len(challenger_vals) - 1)
        
        # Avoid division by zero
        if baseline_var == 0 and challenger_var == 0:
            return 1.0  # No variance means no significant difference
            
        pooled_std = ((len(baseline_vals) - 1) * baseline_var + (len(challenger_vals) - 1) * challenger_var) / \
                    (len(baseline_vals) + len(challenger_vals) - 2)
        pooled_std = pooled_std ** 0.5
        
        # T-statistic
        n1, n2 = len(baseline_vals), len(challenger_vals)
        std_error = pooled_std * (1/n1 + 1/n2) ** 0.5
        
        if std_error == 0:
            return 1.0  # No standard error means no significant difference
            
        t_stat = mean_diff / std_error
        
        # Approximate p-value from t-statistic (very simplified)
        p_value = max(0.001, 1 / (1 + abs(t_stat) ** 3))
        
        return p_value
        
    def _promotion_decision(
        self,
        improvements: dict[str, float],
        significance: float,
        challenger_metrics: dict[str, float]
    ) -> bool:
        """Make promotion decision based on multiple criteria."""
        # Criteria:
        # 1. At least one metric improves significantly (>5%)
        # 2. No metric regresses significantly (<-5%)
        # 3. Statistically significant (p < 0.05)
        # 4. No safety incidents (would be checked separately)
        
        has_significant_improvement = any(
            imp > 5 for imp in improvements.values()
        )
        
        has_significant_regression = any(
            imp < -5 for imp in improvements.values()
        )
        
        is_statistically_significant = significance < 0.05
        
        # Check for safety issues (placeholder)
        has_safety_issues = False  # Would check cleanup failures, etc.
        
        return (has_significant_improvement and 
                not has_significant_regression and 
                is_statistically_significant and 
                not has_safety_issues)
                
    def _generate_recommendations(
        self,
        improvements: dict[str, float],
        should_promote: bool,
        challenger_metrics: dict[str, float]
    ) -> list[str]:
        """Generate actionable recommendations."""
        recommendations = []
        
        if should_promote:
            recommendations.append("Challenger strategy meets promotion criteria")
            
            # Highlight top improvement
            top_improvement = max(improvements.items(), key=lambda x: x[1])
            recommendations.append(f"Top improvement: {top_improvement[0]} (+{top_improvement[1]:.1f}%)")
            
            # Note any concerns
            concerns = [k for k, v in improvements.items() if v < 0]
            if concerns:
                recommendations.append(f"Watch out for regressions in: {', '.join(concerns)}")
                
        else:
            recommendations.append("Challenger does not meet promotion criteria")
            if improvements.get("recall_improvement", 0) < 5:
                recommendations.append("Consider improving recall metrics before promotion")
            if improvements.get("cost_efficiency", 0) < 0:
                recommendations.append("Cost efficiency regression detected - optimize resource usage")
                
        return recommendations
        
    def _save_test_result(self, result: ABTestResult) -> None:
        """Save test result to disk."""
        result_file = self.test_results_dir / f"{result.test_id}.json"
        
        result_dict = {
            "test_id": result.test_id,
            "timestamp": datetime.now().isoformat(),
            "baseline_metrics": result.baseline_metrics,
            "challenger_metrics": result.challenger_metrics,
            "improvement_percentage": result.improvement_percentage,
            "statistical_significance": result.statistical_significance,
            "should_promote": result.should_promote,
            "recommendations": result.recommendations
        }
        
        with open(result_file, "w", encoding="utf-8") as f:
            json.dump(result_dict, f, indent=2)
            
        logger.info("Saved A/B test result to %s", result_file)
