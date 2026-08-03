"""
Runtime Adaptive Engine - Real-time strategy adjustment during probe execution.

This module provides real-time adaptation of discovery strategy based on:
- Probe execution results (success/failure patterns)
- Oracle assertion outcomes
- Model response quality indicators
- Execution time and cost metrics
- Risk type performance distribution

Key features:
- Dynamic risk weight adjustment
- Adaptive probe selection
- Cost-aware exploration vs exploitation
- Early stopping for low-yield paths
- Runtime feedback loop

Usage:
    from .runtime_adaptive_engine import RuntimeAdaptiveEngine
    
    engine = RuntimeAdaptiveEngine(project="my_project")
    
    # During probe execution
    for probe in probes:
        result = execute_probe(probe)
        
        # Real-time adaptation
        engine.record_result(probe, result)
        adjusted_probes = engine.reorder_remaining_probes()
        
        # Get runtime recommendations
        insights = engine.get_runtime_insights()
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class RuntimeMetrics:
    """Real-time execution metrics."""
    probe_id: str
    success: bool
    execution_time_ms: float
    cost_score: float  # 0-1, lower is better
    signal_value: float  # 0-1, how valuable was this probe
    failure_reason: Optional[str] = None


@dataclass
class RiskTypePerformance:
    """Performance stats for a risk type."""
    risk_type: str
    total_probes: int = 0
    successful_probes: int = 0
    avg_execution_time: float = 0.0
    avg_signal_value: float = 0.0
    recent_failure_rate: float = 0.0  # Last 10 probes


class RuntimeAdaptiveEngine:
    """Real-time adaptive discovery engine."""
    
    def __init__(self, project: str):
        self.project = project
        self.metrics_history: list[RuntimeMetrics] = []
        self.risk_type_stats: dict[str, RiskTypePerformance] = {}
        self.exploration_budget = 100  # Remaining exploration budget
        self.exploitation_threshold = 0.7  # Signal value threshold for exploitation
        
        # Performance tracking
        self.probe_yield_rate: dict[str, float] = {}  # probe_type -> yield rate
        self.recent_results: list[RuntimeMetrics] = []  # Last 20 results
        
    def record_result(self, probe: dict, result: dict) -> None:
        """Record a single probe execution result."""
        try:
            metric = RuntimeMetrics(
                probe_id=probe.get("id", "unknown"),
                success=result.get("success", False),
                execution_time_ms=result.get("execution_time_ms", 0.0),
                cost_score=result.get("cost_score", 0.5),
                signal_value=result.get("signal_value", 0.0),
                failure_reason=result.get("failure_reason")
            )
            
            self.metrics_history.append(metric)
            self._update_recent_results(metric)
            self._update_risk_type_stats(metric, probe)
            
            # Adjust exploration budget
            if not metric.success:
                self.exploration_budget -= 1
                
        except Exception as e:
            logger.warning("Failed to record probe result: %s", e)
            
    def _update_recent_results(self, metric: RuntimeMetrics, window: int = 20) -> None:
        """Keep only last N results."""
        self.recent_results.append(metric)
        if len(self.recent_results) > window:
            self.recent_results = self.recent_results[-window:]
            
    def _update_risk_type_stats(self, metric: RuntimeMetrics, probe: dict) -> None:
        """Update statistics for the probe's risk type."""
        risk_type = probe.get("risk_type", "unknown")
        
        if risk_type not in self.risk_type_stats:
            self.risk_type_stats[risk_type] = RiskTypePerformance(risk_type=risk_type)
        
        stats = self.risk_type_stats[risk_type]
        stats.total_probes += 1
        
        if metric.success:
            stats.successful_probes += 1
            
        # Moving average for execution time
        stats.avg_execution_time = (
            (stats.avg_execution_time * (stats.total_probes - 1) + metric.execution_time_ms) 
            / stats.total_probes
        )
        
        # Update recent failure rate (last 10 probes)
        recent_failures = sum(
            1 for m in self.recent_results[-10:] 
            if m.probe_id.endswith(f"_{risk_type}") and not m.success
        )
        stats.recent_failure_rate = recent_failures / min(len(self.recent_results), 10)
        
    def reorder_remaining_probes(self, remaining_probes: list[dict]) -> list[dict]:
        """Reorder probes based on runtime performance."""
        if not self.risk_type_stats:
            return remaining_probes
            
        # Calculate priority for each probe
        def probe_priority(probe: dict) -> float:
            risk_type = probe.get("risk_type", "unknown")
            stats = self.risk_type_stats.get(risk_type)
            
            if not stats:
                return 0.5  # Default priority for unknown types
                
            # Priority based on:
            # 1. Success rate (higher is better)
            success_rate = stats.successful_probes / max(stats.total_probes, 1)
            
            # 2. Recent performance (penalize high failure rates)
            recent_penalty = stats.recent_failure_rate * 0.3
            
            # 3. Signal value potential (high-risk types get priority)
            risk_weight = {"critical": 1.2, "high": 1.1, "medium": 1.0, "low": 0.9}.get(
                probe.get("severity", "medium"), 1.0
            )
            
            priority = (success_rate * 0.6 + risk_weight * 0.4) - recent_penalty
            return max(0.0, min(1.0, priority))  # Clamp to [0, 1]
            
        # Sort by priority (descending)
        return sorted(remaining_probes, key=probe_priority, reverse=True)
        
    def get_runtime_insights(self) -> dict[str, Any]:
        """Get real-time insights and recommendations."""
        if not self.metrics_history:
            return {"message": "No execution data yet"}
            
        # Calculate overall success rate
        total = len(self.metrics_history)
        successful = sum(1 for m in self.metrics_history if m.success)
        overall_success_rate = successful / total if total > 0 else 0.0
        
        # Identify underperforming risk types
        underperforming = [
            rt for rt, stats in self.risk_type_stats.items()
            if stats.total_probes >= 3 and stats.recent_failure_rate > 0.6
        ]
        
        # Identify high-performing risk types
        high_performing = [
            rt for rt, stats in self.risk_type_stats.items()
            if stats.total_probes >= 3 and stats.successful_probes / stats.total_probes > 0.7
        ]
        
        insights = {
            "overall_success_rate": overall_success_rate,
            "total_probes_executed": total,
            "exploration_budget_remaining": self.exploration_budget,
            "underperforming_risk_types": underperforming,
            "high_performing_risk_types": high_performing,
            "recommendations": []
        }
        
        # Generate recommendations
        if underperforming:
            insights["recommendations"].append({
                "type": "reduce_investment",
                "message": f"Reduce probe investment in: {', '.join(underperforming)}",
                "risk_types": underperforming
            })
            
        if self.exploration_budget < 20:
            insights["recommendations"].append({
                "type": "budget_exhaustion",
                "message": "Exploration budget nearly exhausted. Consider increasing budget or focusing on high-yield areas.",
                "budget": self.exploration_budget
            })
            
        return insights
        
    def should_early_stop(self, probe_type: str, consecutive_failures: int = 5) -> bool:
        """Check if should early stop a probe type due to repeated failures."""
        if probe_type not in self.risk_type_stats:
            return False
            
        stats = self.risk_type_stats[probe_type]
        if stats.total_probes < 3:
            return False
            
        return stats.recent_failure_rate > 0.8 and consecutive_failures >= 5
