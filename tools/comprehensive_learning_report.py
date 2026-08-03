"""Comprehensive Learning Report Generator - Consolidate all learning artifacts.

Generates a unified report from:
- Learning effectiveness metrics
- Enhanced learning signals  
- Risk learning profiles
- High-value attack plans
- Capability assessments

Usage:
    python tools/comprehensive_learning_report.py --project my_project
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_json_file(path: Path) -> dict | None:
    """Load JSON file safely."""
    if not path.exists():
        return None
    
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception as e:
        logger.warning("Failed to load %s: %s", path, e)
        return None


def generate_comprehensive_learning_report(
    project: str,
    root: Path,
    output_dir: Path | None = None,
) -> dict:
    """Generate comprehensive learning report from all sources."""
    
    output_dir = output_dir or (root / "platform_outputs" / project)
    
    report = {
        "project_id": project,
        "generated_at": datetime.now().isoformat(),
        "report_version": "v1.0",
    }
    
    # Load learning effectiveness report
    eff_path = output_dir / "learning_effectiveness_report.json"
    eff_data = load_json_file(eff_path)
    if eff_data:
        report["learning_effectiveness"] = {
            "available": True,
            "total_rounds": eff_data.get("total_rounds", 0),
            "total_bugs_discovered": eff_data.get("total_bugs_discovered", 0),
            "overall_recall_improvement": eff_data.get("overall_recall_improvement", 0),
            "avg_precision": eff_data.get("avg_precision", 0),
            "recommendations": eff_data.get("recommendations", []),
        }
    else:
        report["learning_effectiveness"] = {"available": False}
    
    # Load enhanced learning signals
    signals_path = output_dir / "enhanced_learning_signals.json"
    signals_data = load_json_file(signals_path)
    if signals_data and signals_data.get("signals"):
        all_signals = signals_data["signals"]
        total_signals = sum(s.get("total_signals", 0) for s in all_signals)
        critical_signals = sum(s.get("critical_signals", 0) for s in all_signals)
        
        report["enhanced_signals"] = {
            "available": True,
            "scans_analyzed": len(all_signals),
            "total_signals": total_signals,
            "critical_signals": critical_signals,
            "signal_breakdown": {
                "confirmed_bugs": sum(
                    s.get("signals_by_type", {}).get("confirmed_bugs", 0)
                    for s in all_signals
                ),
                "failed_probes": sum(
                    s.get("signals_by_type", {}).get("failed_probes", 0)
                    for s in all_signals
                ),
                "cleanup_failures": sum(
                    s.get("signals_by_type", {}).get("cleanup_failures", 0)
                    for s in all_signals
                ),
            },
        }
    else:
        report["enhanced_signals"] = {"available": False}
    
    # Load risk learning profile
    profile_path = output_dir / "workspace" / "risk_learning_profile.json"
    profile_data = load_json_file(profile_path)
    if profile_data:
        report["risk_learning_profile"] = {
            "available": True,
            "learned_from_findings": profile_data.get("learned_from_findings", 0),
            "priority_risk_count": len(profile_data.get("priority_risks", [])),
            "top_priorities": [
                {"risk": r["name"], "weight": r["weight"]}
                for r in profile_data.get("priority_risks", [])[:5]
            ],
        }
    else:
        report["risk_learning_profile"] = {"available": False}
    
    # Load high value attack plan
    attack_path = output_dir / "workspace" / "high_value_attack_plan.json"
    attack_data = load_json_file(attack_path)
    if attack_data:
        report["attack_plan"] = {
            "available": True,
            "focus_risk_count": attack_data.get("total_focus_risks", 0),
            "strategy": attack_data.get("strategy_summary", ""),
        }
    else:
        report["attack_plan"] = {"available": False}
    
    # Load capability assessment
    cap_path = output_dir / "workspace" / "high_value_capability_assessment.json"
    cap_data = load_json_file(cap_path)
    if cap_data:
        gaps = cap_data.get("capability_gaps", [])
        report["capability_assessment"] = {
            "available": True,
            "gap_count": len(gaps),
            "oracle_coverage_rate": cap_data.get("oracle_coverage_rate", 0),
            "top_gaps": [
                {"area": g.get("area", ""), "severity": g.get("severity", "")}
                for g in gaps[:5]
            ],
        }
    else:
        report["capability_assessment"] = {"available": False}
    
    # Compute composite metrics
    report["composite_metrics"] = _compute_composite_metrics(report)
    
    # Generate recommendations
    report["recommendations"] = _generate_recommendations(report)
    
    return report


def _compute_composite_metrics(report: dict) -> dict:
    """Compute composite learning health metrics."""
    metrics = {}
    
    # Count available components
    available_components = sum([
        report.get("learning_effectiveness", {}).get("available", False),
        report.get("enhanced_signals", {}).get("available", False),
        report.get("risk_learning_profile", {}).get("available", False),
        report.get("attack_plan", {}).get("available", False),
        report.get("capability_assessment", {}).get("available", False),
    ])
    
    metrics["components_available"] = available_components
    metrics["learning_maturity_score"] = min(10, available_components * 2)  # Max 10
    
    # Recall improvement score
    recall_imp = report.get("learning_effectiveness", {}).get("overall_recall_improvement", 0)
    metrics["recall_improvement_score"] = min(10, max(0, recall_imp * 20))  # 50% improvement = 10
    
    # Signal diversity score
    signals = report.get("enhanced_signals", {})
    if signals.get("available"):
        breakdown = signals.get("signal_breakdown", {})
        signal_types_with_data = sum(1 for v in breakdown.values() if v > 0)
        metrics["signal_diversity_score"] = min(10, signal_types_with_data * 3.3)  # 3 types = 10
    
    # Coverage score
    cap = report.get("capability_assessment", {})
    if cap.get("available"):
        coverage = cap.get("oracle_coverage_rate", 0)
        metrics["coverage_score"] = min(10, coverage * 10)
    
    # Overall health score (weighted average)
    weights = {
        "recall_improvement_score": 0.3,
        "signal_diversity_score": 0.25,
        "coverage_score": 0.25,
        "components_available": 0.2,  # Normalized to 0-2 scale
    }
    
    overall = (
        metrics.get("recall_improvement_score", 0) * weights["recall_improvement_score"] +
        metrics.get("signal_diversity_score", 0) * weights["signal_diversity_score"] +
        metrics.get("coverage_score", 0) * weights["coverage_score"] +
        metrics.get("components_available", 0) * weights["components_available"] * 5  # Scale up
    )
    
    metrics["overall_learning_health_score"] = min(10, overall)
    
    return metrics


def _generate_recommendations(report: dict) -> list[str]:
    """Generate actionable recommendations based on comprehensive analysis."""
    recommendations = []
    
    # Check for missing components
    if not report.get("learning_effectiveness", {}).get("available"):
        recommendations.append(
            "Run learning effectiveness analysis first: "
            "python tools/learning_effectiveness_dashboard.py --project {project}"
        )
    
    if not report.get("enhanced_signals", {}).get("available"):
        recommendations.append(
            "Extract enhanced learning signals: "
            "python tools/enhanced_learning_signals.py --project {project}"
        )
    
    # Check recall improvement
    recall_imp = report.get("learning_effectiveness", {}).get("overall_recall_improvement", 0)
    if recall_imp < 0.1 and recall_imp >= 0:
        recommendations.append(
            f"Low recall improvement ({recall_imp:.1%}). Consider increasing probe diversity "
            "or exploring new risk types."
        )
    elif recall_imp < 0:
        recommendations.append(
            f"Negative recall improvement ({recall_imp:+.1%}). Investigate regression causes "
            "and review recent strategy changes."
        )
    
    # Check signal diversity
    signals = report.get("enhanced_signals", {})
    if signals.get("available"):
        breakdown = signals.get("signal_breakdown", {})
        if breakdown.get("failed_probes", 0) == 0:
            recommendations.append(
                "No failed probe signals detected. Enable detailed execution logging "
                "to capture failure patterns."
            )
        
        if breakdown.get("cleanup_failures", 0) > 10:
            recommendations.append(
                f"High cleanup failures ({breakdown['cleanup_failures']}). Review compensation "
                "strategies and DELETE/PUT/PATCH bindings."
            )
    
    # Check capability gaps
    cap = report.get("capability_assessment", {})
    if cap.get("available") and cap.get("gap_count", 0) > 5:
        recommendations.append(
            f"{cap['gap_count']} capability gaps identified. Prioritize addressing "
            "high-severity gaps first."
        )
    
    # Overall health-based recommendations
    health = report.get("composite_metrics", {}).get("overall_learning_health_score", 0)
    if health < 5:
        recommendations.append(
            "Learning health score is low (< 5/10). Focus on building foundational "
            "capabilities before advanced optimization."
        )
    elif health < 7:
        recommendations.append(
            "Learning health score is moderate (5-7/10). Good foundation; now focus "
            "on signal diversity and coverage improvement."
        )
    
    return recommendations


def main(project: str = "default_project", root: Path | None = None):
    """Main entry point."""
    root = root or REPO_ROOT
    output_dir = root / "platform_outputs" / project
    
    print("=" * 70)
    print("COMPREHENSIVE LEARNING REPORT GENERATION")
    print("=" * 70)
    print(f"Project: {project}")
    print(f"Output directory: {output_dir}")
    
    # Generate report
    print("\nGenerating comprehensive report...")
    report = generate_comprehensive_learning_report(project, root, output_dir)
    
    # Print summary
    print("\n" + "=" * 70)
    print("LEARNING HEALTH SUMMARY")
    print("=" * 70)
    
    metrics = report.get("composite_metrics", {})
    print(f"Overall Learning Health Score: {metrics.get('overall_learning_health_score', 0):.1f}/10")
    print(f"Learning Maturity Score: {metrics.get('learning_maturity_score', 0)}/10")
    print(f"Components Available: {metrics.get('components_available', 0)}/5")
    
    print("\nKey Metrics:")
    print(f"  • Recall Improvement: {report.get('learning_effectiveness', {}).get('overall_recall_improvement', 0):+.1%}")
    print(f"  • Avg Precision: {report.get('learning_effectiveness', {}).get('avg_precision', 0):.1%}")
    print(f"  • Total Bugs Discovered: {report.get('learning_effectiveness', {}).get('total_bugs_discovered', 0)}")
    
    signals = report.get("enhanced_signals", {})
    if signals.get("available"):
        print(f"\nSignal Analysis:")
        print(f"  • Total Signals: {signals.get('total_signals', 0)}")
        print(f"  • Critical Signals: {signals.get('critical_signals', 0)}")
        breakdown = signals.get("signal_breakdown", {})
        print(f"    - Confirmed bugs: {breakdown.get('confirmed_bugs', 0)}")
        print(f"    - Failed probes: {breakdown.get('failed_probes', 0)}")
        print(f"    - Cleanup failures: {breakdown.get('cleanup_failures', 0)}")
    
    # Print recommendations
    recommendations = report.get("recommendations", [])
    if recommendations:
        print(f"\nRecommendations ({len(recommendations)}):")
        for i, rec in enumerate(recommendations[:5], 1):
            print(f"  {i}. {rec}")
    
    # Save report
    report_path = output_dir / "comprehensive_learning_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    print(f"\nFull report saved to: {report_path}")
    print("=" * 70)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Generate comprehensive learning report"
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
        help="Repository root"
    )
    
    args = parser.parse_args()
    main(project=args.project, root=args.root)
