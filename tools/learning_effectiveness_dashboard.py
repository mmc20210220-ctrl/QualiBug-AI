"""Learning Effectiveness Dashboard - Quantify self-improvement ROI per round.

This module provides metrics and visualizations for tracking:
- Round-over-round recall improvement
- Learning ROI (discovered bugs per learning investment)
- Signal effectiveness by type
- Diminishing returns analysis
- Capability growth trajectory

Usage:
    python tools/learning_effectiveness_dashboard.py --project my_project
    
Output:
    - platform_outputs/{project}/learning_metrics.json
    - platform_outputs/{project}/learning_trend.png (optional, requires matplotlib)
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
class RoundMetrics:
    """Metrics for a single discovery round."""
    round_num: int
    timestamp: str
    total_probes: int
    successful_probes: int
    failed_probes: int
    confirmed_bugs: int
    false_positives: int
    model_calls: int
    execution_time_seconds: float
    risk_type_breakdown: dict[str, dict] = field(default_factory=dict)
    probe_source_breakdown: dict[str, int] = field(default_factory=dict)
    
    # Derived metrics
    recall_estimate: float = 0.0
    precision: float = 0.0
    bugs_per_call: float = 0.0
    bugs_per_minute: float = 0.0
    cost_efficiency: float = 0.0


@dataclass
class LearningEffectivenessReport:
    """Comprehensive learning effectiveness report across rounds."""
    project_id: str
    generated_at: str
    rounds: list[RoundMetrics]
    
    # Aggregate metrics
    total_rounds: int = 0
    total_bugs_discovered: int = 0
    overall_recall_improvement: float = 0.0
    avg_precision: float = 0.0
    avg_cost_efficiency: float = 0.0
    
    # Learning signals analysis
    signal_effectiveness: dict[str, dict] = field(default_factory=dict)
    diminishing_returns_detected: bool = False
    capability_growth_rate: float = 0.0
    
    # Recommendations
    recommendations: list[str] = field(default_factory=list)
    
    def save(self, output_dir: Path):
        """Save report to JSON."""
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "learning_effectiveness_report.json"
        
        data = {
            "project_id": self.project_id,
            "generated_at": self.generated_at,
            "total_rounds": self.total_rounds,
            "total_bugs_discovered": self.total_bugs_discovered,
            "overall_recall_improvement": self.overall_recall_improvement,
            "avg_precision": self.avg_precision,
            "avg_cost_efficiency": self.avg_cost_efficiency,
            "rounds": [
                {
                    "round_num": r.round_num,
                    "timestamp": r.timestamp,
                    "total_probes": r.total_probes,
                    "confirmed_bugs": r.confirmed_bugs,
                    "recall_estimate": r.recall_estimate,
                    "precision": r.precision,
                    "bugs_per_minute": r.bugs_per_minute,
                    "cost_efficiency": r.cost_efficiency,
                }
                for r in self.rounds
            ],
            "signal_effectiveness": self.signal_effectiveness,
            "recommendations": self.recommendations,
        }
        
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        logger.info("Saved learning effectiveness report to %s", path)


def load_scan_results(project: str, root: Path) -> list[dict]:
    """Load scan results from multiple rounds."""
    output_dir = root / "platform_outputs" / project
    
    if not output_dir.exists():
        logger.warning("Output directory not found: %s", output_dir)
        return []
    
    results = []
    for scan_file in sorted(output_dir.glob("scan_*.json")):
        try:
            data = json.load(open(scan_file, encoding="utf-8"))
            results.append({
                "file": str(scan_file),
                "timestamp": scan_file.stat().st_mtime,
                "data": data,
            })
        except Exception as e:
            logger.error("Failed to load %s: %s", scan_file, e)
    
    return results


def compute_round_metrics(scan_result: dict) -> RoundMetrics:
    """Compute metrics for a single scan result.

    Field mapping honours the mainline v12 schema: legacy keys
    (``high_value_summary.*``, ``benchmark_metrics.total_probes`` etc.) are
    not written by real scans and previously produced all-zero metrics. The
    authoritative sources are ``formal_count_projection``,
    ``pipeline_health`` and ``obligation_attempt_ledger``; legacy keys remain
    as fallbacks for older fixtures.

    NOTE: ``recall_estimate`` here is an internal yield ratio (confirmed bugs
    per executed obligation), NOT evaluator recall — internal counts are
    diagnostic only and must never be presented as recall/precision.
    """
    data = scan_result["data"]

    # ── v12 schema mapping ──
    benchmark = data.get("benchmark_metrics", {})
    summary = data.get("high_value_summary", {})
    projection = data.get("formal_count_projection", {})
    if not isinstance(projection, dict):
        projection = {}
    health = data.get("pipeline_health", {})
    if not isinstance(health, dict):
        health = {}
    ledger = data.get("obligation_attempt_ledger", {})
    if not isinstance(ledger, dict):
        ledger = {}

    total_probes = int(
        benchmark.get("total_probes", 0)
        or health.get("executed_obligation_count")
        or ledger.get("execution_count")
        or 0
    )
    successful_probes = int(benchmark.get("successful_probes", 0) or 0)
    failed_probes = int(
        benchmark.get("failed_probes", 0)
        or health.get("blocked_obligation_count")
        or 0
    )
    confirmed_bugs = int(
        summary.get("total_confirmed_bugs", 0)
        or projection.get("formal_customer_deliverable_count")
        or projection.get("canonical_defect_count")
        or 0
    )
    false_positives = int(summary.get("false_positive_count", 0) or 0)
    
    # Model usage
    model_usage = benchmark.get("model_usage", {})
    total_model_calls = sum(model_usage.values()) if model_usage else 0
    
    # Execution time
    total_time_ms = benchmark.get("total_execution_time_ms", 0)
    execution_time_seconds = total_time_ms / 1000.0
    
    # Risk type breakdown
    risk_breakdown = benchmark.get("risk_type_breakdown", {})
    
    # Probe source breakdown
    probe_sources = {}
    for key in ["business_knowledge_probe_count", "risk_learning_profile_probe_count", 
                "high_value_attack_plan_probe_count", "capability_gap_probe_count"]:
        count = benchmark.get(key, 0)
        if count > 0:
            probe_sources[key.replace("_probe_count", "")] = count
    
    # Compute derived metrics
    recall_estimate = confirmed_bugs / total_probes if total_probes > 0 else 0.0
    precision = confirmed_bugs / (confirmed_bugs + false_positives) if (confirmed_bugs + false_positives) > 0 else 0.0
    bugs_per_call = confirmed_bugs / total_model_calls if total_model_calls > 0 else 0.0
    bugs_per_minute = (confirmed_bugs / execution_time_seconds * 60) if execution_time_seconds > 0 else 0.0
    cost_efficiency = bugs_per_minute / max(total_model_calls, 1)  # Normalized
    
    return RoundMetrics(
        round_num=int(benchmark.get("round_number", 1)),
        timestamp=datetime.fromtimestamp(scan_result["timestamp"]).isoformat(),
        total_probes=total_probes,
        successful_probes=successful_probes,
        failed_probes=failed_probes,
        confirmed_bugs=confirmed_bugs,
        false_positives=false_positives,
        model_calls=total_model_calls,
        execution_time_seconds=execution_time_seconds,
        risk_type_breakdown=risk_breakdown,
        probe_source_breakdown=probe_sources,
        recall_estimate=recall_estimate,
        precision=precision,
        bugs_per_call=bugs_per_call,
        bugs_per_minute=bugs_per_minute,
        cost_efficiency=cost_efficiency,
    )


def analyze_learning_effectiveness(rounds: list[RoundMetrics]) -> LearningEffectivenessReport:
    """Analyze learning effectiveness across rounds."""
    if not rounds:
        return LearningEffectivenessReport(
            project_id="unknown",
            generated_at=datetime.now().isoformat(),
            rounds=[],
        )
    
    # Sort by round number
    rounds = sorted(rounds, key=lambda r: r.round_num)
    
    # Compute aggregate metrics
    total_bugs = sum(r.confirmed_bugs for r in rounds)
    avg_precision = sum(r.precision for r in rounds) / len(rounds)
    avg_cost_efficiency = sum(r.cost_efficiency for r in rounds) / len(rounds)
    
    # Recall improvement (first vs last round)
    if len(rounds) >= 2:
        first_recall = rounds[0].recall_estimate
        last_recall = rounds[-1].recall_estimate
        recall_improvement = last_recall - first_recall
    else:
        recall_improvement = 0.0
    
    # Signal effectiveness analysis
    signal_effectiveness = {}
    for round_obj in rounds:
        for source, count in round_obj.probe_source_breakdown.items():
            if source not in signal_effectiveness:
                signal_effectiveness[source] = {
                    "total_probes": 0,
                    "bugs_found": 0,
                    "effectiveness": 0.0,
                }
            signal_effectiveness[source]["total_probes"] += count
    
    # Estimate bugs per signal type (simplified - would need more granular data)
    for source in signal_effectiveness:
        total = signal_effectiveness[source]["total_probes"]
        if total > 0:
            # Heuristic: assume proportional contribution
            signal_effectiveness[source]["bugs_found"] = int(
                total * avg_precision
            )
            signal_effectiveness[source]["effectiveness"] = (
                signal_effectiveness[source]["bugs_found"] / total
            )
    
    # Detect diminishing returns
    diminishing_returns = False
    if len(rounds) >= 3:
        recent_improvements = [
            rounds[i].recall_estimate - rounds[i-1].recall_estimate
            for i in range(1, len(rounds))
        ]
        if len(recent_improvements) >= 2:
            # Check if improvements are decreasing
            if recent_improvements[-1] < recent_improvements[-2] * 0.5:
                diminishing_returns = True
    
    # Calculate capability growth rate
    if len(rounds) >= 2 and rounds[-1].round_num > rounds[0].round_num:
        capability_growth_rate = (
            rounds[-1].recall_estimate - rounds[0].recall_estimate
        ) / (rounds[-1].round_num - rounds[0].round_num)
    else:
        capability_growth_rate = 0.0
    
    # Generate recommendations
    recommendations = []
    
    if avg_precision < 0.5:
        recommendations.append(
            "Low precision detected. Consider tightening probe validation criteria."
        )
    
    if diminishing_returns:
        recommendations.append(
            "Diminishing returns detected. Try new signal sources or explore unexplored risk types."
        )
    
    if avg_cost_efficiency < 0.001:
        recommendations.append(
            "Low cost efficiency. Optimize model calls or focus on higher-yield probes."
        )
    
    # Find best performing signal
    if signal_effectiveness:
        best_signal = max(signal_effectiveness.items(), key=lambda x: x[1]["effectiveness"])
        if best_signal[1]["effectiveness"] > 0.3:
            recommendations.append(
                f"High-performing signal '{best_signal[0]}' ({best_signal[1]['effectiveness']:.1%}). "
                "Consider increasing its weight in next round."
            )
    
    return LearningEffectivenessReport(
        project_id=rounds[0].round_num,  # Use round num as proxy for now
        generated_at=datetime.now().isoformat(),
        rounds=rounds,
        total_rounds=len(rounds),
        total_bugs_discovered=total_bugs,
        overall_recall_improvement=recall_improvement,
        avg_precision=avg_precision,
        avg_cost_efficiency=avg_cost_efficiency,
        signal_effectiveness=signal_effectiveness,
        diminishing_returns_detected=diminishing_returns,
        capability_growth_rate=capability_growth_rate,
        recommendations=recommendations,
    )


def main(project: str = "default_project", root: Path | None = None):
    """Main entry point."""
    root = root or REPO_ROOT
    output_dir = root / "platform_outputs" / project
    
    print(f"Loading scan results for project: {project}")
    scan_results = load_scan_results(project, root)
    
    if not scan_results:
        print("No scan results found. Run discovery first.")
        return
    
    print(f"Found {len(scan_results)} scan(s)")
    
    # Compute metrics for each round
    rounds = []
    for scan_result in scan_results:
        try:
            metrics = compute_round_metrics(scan_result)
            rounds.append(metrics)
            print(f"  Round {metrics.round_num}: {metrics.confirmed_bugs} bugs, "
                  f"precision={metrics.precision:.1%}, "
                  f"recall={metrics.recall_estimate:.1%}")
        except Exception as e:
            print(f"Failed to process {scan_result['file']}: {e}")
    
    if not rounds:
        print("No valid rounds found.")
        return
    
    # Analyze learning effectiveness
    print("\nAnalyzing learning effectiveness...")
    report = analyze_learning_effectiveness(rounds)
    
    # Save report
    report.save(output_dir)
    
    # Print summary
    print("\n" + "=" * 70)
    print("LEARNING EFFECTIVENESS SUMMARY")
    print("=" * 70)
    print(f"Project: {report.project_id}")
    print(f"Rounds analyzed: {report.total_rounds}")
    print(f"Total bugs discovered: {report.total_bugs_discovered}")
    print(f"Overall recall improvement: {report.overall_recall_improvement:+.1%}")
    print(f"Avg precision: {report.avg_precision:.1%}")
    print(f"Avg cost efficiency: {report.avg_cost_efficiency:.3f}")
    print(f"Capability growth rate: {report.capability_growth_rate:.4f}/round")
    print(f"Diminishing returns detected: {report.diminishing_returns_detected}")
    
    if report.signal_effectiveness:
        print("\nSignal Effectiveness:")
        for signal, metrics in sorted(
            report.signal_effectiveness.items(),
            key=lambda x: x[1]["effectiveness"],
            reverse=True
        ):
            print(f"  {signal}: {metrics['effectiveness']:.1%} "
                  f"({metrics['bugs_found']} bugs / {metrics['total_probes']} probes)")
    
    if report.recommendations:
        print("\nRecommendations:")
        for rec in report.recommendations:
            print(f"  • {rec}")
    
    print("=" * 70)
    print(f"Full report saved to: {output_dir / 'learning_effectiveness_report.json'}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Quantify learning effectiveness across discovery rounds"
    )
    parser.add_argument(
        "--project",
        default="default_project",
        help="Project ID to analyze"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root (default: auto-detect)"
    )
    
    args = parser.parse_args()
    main(project=args.project, root=args.root)
