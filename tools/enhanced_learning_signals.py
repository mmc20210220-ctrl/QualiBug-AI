"""Enhanced Learning Signal Extractor - Extract learning signals from all execution artifacts.

Current signal sources (limited):
- confirmed_bugs (only)

Enhanced signal sources (comprehensive):
- confirmed_bugs (high value)
- failed_probes (what didn't work)
- engine_failures (systematic gaps)
- partial_executions (incomplete paths)
- assertion_failures (oracle gaps)
- binding_failures (identity gaps)
- cleanup_failures (compensation gaps)
- timeout_events (performance issues)
- model_error_patterns (LLM limitations)

This module extracts and categorizes these signals for learning pipeline.
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
class LearningSignal:
    """A single learning signal extracted from execution."""
    signal_type: str  # e.g., "confirmed_bug", "failed_probe", "engine_failure"
    source_file: str
    round_number: int
    timestamp: str
    severity: str  # "critical", "high", "medium", "low"
    category: str  # e.g., "authorization", "validation", "state"
    
    # Raw data (sanitized)
    raw_data: dict[str, Any]
    
    # Derived features
    risk_types: list[str] = field(default_factory=list)
    related_apis: list[str] = field(default_factory=list)
    probe_sources: list[str] = field(default_factory=list)
    
    # Learning potential score (0-1)
    learning_potential: float = 0.0
    
    @classmethod
    def from_confirmed_bug(cls, bug: dict, source_file: str, round_num: int):
        """Create signal from confirmed bug."""
        return cls(
            signal_type="confirmed_bug",
            source_file=source_file,
            round_number=round_num,
            timestamp=datetime.now().isoformat(),
            severity="critical" if bug.get("value_tier") in {"S", "A"} else "high",
            category=bug.get("defect_family", "unknown"),
            raw_data={
                "title": bug.get("title", "")[:200],
                "severity": bug.get("severity", ""),
                "risk_type": bug.get("risk_type", ""),
                "related_apis": bug.get("related_apis", [])[:5],
                "evidence_summary": bug.get("evidence_summary", "")[:300],
            },
            risk_types=[bug.get("risk_type", "")],
            related_apis=bug.get("related_apis", [])[:5],
            probe_sources=[bug.get("probe_source", "unknown")],
            learning_potential=1.0,  # Highest potential
        )
    
    @classmethod
    def from_failed_probe(cls, probe_result: dict, source_file: str, round_num: int):
        """Create signal from failed probe."""
        error_type = probe_result.get("error_type", "unknown")
        severity = "high" if error_type in {"timeout", "crash", "5xx"} else "medium"
        
        return cls(
            signal_type="failed_probe",
            source_file=source_file,
            round_number=round_num,
            timestamp=datetime.now().isoformat(),
            severity=severity,
            category=probe_result.get("risk_type", "unknown"),
            raw_data={
                "probe_id": probe_result.get("probe_id", "")[:50],
                "error_type": error_type,
                "error_message": str(probe_result.get("error", ""))[:500],
                "probe_source": probe_result.get("probe_source", "unknown"),
                "api_path": probe_result.get("api_path", ""),
            },
            risk_types=[probe_result.get("risk_type", "")],
            related_apis=[probe_result.get("api_path", "")],
            probe_sources=[probe_result.get("probe_source", "unknown")],
            learning_potential=0.7,  # High potential - tells us what doesn't work
        )
    
    @classmethod
    def from_engine_failure(cls, failure: dict, source_file: str, round_num: int):
        """Create signal from engine failure."""
        return cls(
            signal_type="engine_failure",
            source_file=source_file,
            round_number=round_num,
            timestamp=datetime.now().isoformat(),
            severity="high",
            category=failure.get("engine_name", "unknown"),
            raw_data={
                "engine": failure.get("engine_name", ""),
                "error_class": failure.get("error_class", ""),
                "error_code": failure.get("error_code", ""),
                "failure_count": failure.get("failure_count", 1),
            },
            risk_types=[],
            related_apis=[],
            probe_sources=[],
            learning_potential=0.6,  # Medium-high - indicates systematic gaps
        )
    
    @classmethod
    def from_binding_failure(cls, failure: dict, source_file: str, round_num: int):
        """Create signal from binding resolution failure."""
        return cls(
            signal_type="binding_failure",
            source_file=source_file,
            round_number=round_num,
            timestamp=datetime.now().isoformat(),
            severity="medium",
            category="binding",
            raw_data={
                "placeholder": failure.get("placeholder", ""),
                "target_path": failure.get("target_path", ""),
                "resolver_type": failure.get("resolver_type", ""),
                "reason": failure.get("reason", ""),
            },
            risk_types=[],
            related_apis=[failure.get("target_path", "")],
            probe_sources=[],
            learning_potential=0.5,  # Medium - helps improve binding
        )
    
    @classmethod
    def from_cleanup_failure(cls, failure: dict, source_file: str, round_num: int):
        """Create signal from cleanup compensation failure."""
        return cls(
            signal_type="cleanup_failure",
            source_file=source_file,
            round_number=round_num,
            timestamp=datetime.now().isoformat(),
            severity="high",
            category="cleanup",
            raw_data={
                "step_id": failure.get("step_id", ""),
                "cleanup_method": failure.get("cleanup_method", ""),
                "cleanup_path": failure.get("cleanup_path", ""),
                "failure_reason": failure.get("failure_reason", ""),
                "residue_detected": failure.get("residue_detected", False),
            },
            risk_types=[],
            related_apis=[failure.get("cleanup_path", "")],
            probe_sources=[],
            learning_potential=0.8,  # High - critical for state management
        )


@dataclass
class EnhancedLearningSignals:
    """Comprehensive set of learning signals from a scan."""
    project_id: str
    scan_file: str
    round_number: int
    extracted_at: str
    
    # Signals by type
    confirmed_bugs: list[LearningSignal] = field(default_factory=list)
    failed_probes: list[LearningSignal] = field(default_factory=list)
    engine_failures: list[LearningSignal] = field(default_factory=list)
    binding_failures: list[LearningSignal] = field(default_factory=list)
    cleanup_failures: list[LearningSignal] = field(default_factory=list)
    
    # Summary statistics
    total_signals: int = 0
    critical_signals: int = 0
    avg_learning_potential: float = 0.0
    
    # Risk distribution
    risk_type_distribution: dict[str, int] = field(default_factory=dict)
    category_distribution: dict[str, int] = field(default_factory=dict)
    
    # Recommendations
    top_recommendations: list[str] = field(default_factory=list)
    
    def compute_statistics(self):
        """Compute summary statistics from all signals."""
        all_signals = (
            self.confirmed_bugs + 
            self.failed_probes + 
            self.engine_failures + 
            self.binding_failures + 
            self.cleanup_failures
        )
        
        self.total_signals = len(all_signals)
        self.critical_signals = sum(
            1 for s in all_signals if s.severity == "critical"
        )
        
        if all_signals:
            self.avg_learning_potential = sum(
                s.learning_potential for s in all_signals
            ) / len(all_signals)
        
        # Compute distributions
        for signal in all_signals:
            for risk in signal.risk_types:
                if risk:
                    self.risk_type_distribution[risk] = \
                        self.risk_type_distribution.get(risk, 0) + 1
            
            self.category_distribution[signal.category] = \
                self.category_distribution.get(signal.category, 0) + 1
        
        # Generate recommendations
        self._generate_recommendations()
    
    def _generate_recommendations(self):
        """Generate actionable recommendations from signals."""
        recommendations = []
        
        # High-priority recommendations
        if self.critical_signals > 10:
            recommendations.append(
                f"High number of critical signals ({self.critical_signals}). "
                "Prioritize investigation of recurring patterns."
            )
        
        # Risk-based recommendations
        if self.risk_type_distribution:
            top_risks = sorted(
                self.risk_type_distribution.items(),
                key=lambda x: x[1],
                reverse=True
            )[:3]
            
            if top_risks[0][1] > 5:
                recommendations.append(
                    f"Concentrated failures in '{top_risks[0][0]}'. "
                    "Consider specialized probes for this risk type."
                )
        
        # Category-based recommendations
        if "cleanup" in self.category_distribution:
            count = self.category_distribution["cleanup"]
            if count > 3:
                recommendations.append(
                    f"{count} cleanup failures detected. "
                    "Review compensation strategies and DELETE/PUT/PATCH bindings."
                )
        
        if "binding" in self.category_distribution:
            count = self.category_distribution["binding"]
            if count > 5:
                recommendations.append(
                    f"{count} binding failures detected. "
                    "Improve identity resolver coverage for path parameters."
                )
        
        # Low learning potential warning
        if self.avg_learning_potential < 0.3 and self.total_signals > 0:
            recommendations.append(
                "Low average learning potential. "
                "Focus on higher-quality signals (confirmed bugs, cleanup failures)."
            )
        
        self.top_recommendations = recommendations
    
    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "project_id": self.project_id,
            "scan_file": self.scan_file,
            "round_number": self.round_number,
            "extracted_at": self.extracted_at,
            "total_signals": self.total_signals,
            "critical_signals": self.critical_signals,
            "avg_learning_potential": self.avg_learning_potential,
            "risk_type_distribution": self.risk_type_distribution,
            "category_distribution": self.category_distribution,
            "recommendations": self.top_recommendations,
            "signals_by_type": {
                "confirmed_bugs": len(self.confirmed_bugs),
                "failed_probes": len(self.failed_probes),
                "engine_failures": len(self.engine_failures),
                "binding_failures": len(self.binding_failures),
                "cleanup_failures": len(self.cleanup_failures),
            },
        }


class EnhancedSignalExtractor:
    """Extract enhanced learning signals from scan results."""
    
    def __init__(self, project: str, root: Path):
        self.project = project
        self.root = root
        self.output_dir = root / "platform_outputs" / project
    
    def extract_from_scan(self, scan_file: Path) -> EnhancedLearningSignals:
        """Extract signals from a single scan result file."""
        try:
            data = json.load(open(scan_file, encoding="utf-8"))
        except Exception as e:
            logger.error("Failed to load %s: %s", scan_file, e)
            return self._empty_signal(scan_file)
        
        round_num = self._extract_round_number(data)
        
        signals = EnhancedLearningSignals(
            project_id=self.project,
            scan_file=str(scan_file),
            round_number=round_num,
            extracted_at=datetime.now().isoformat(),
        )
        
        # Extract confirmed bugs
        self._extract_confirmed_bugs(data, signals)
        
        # Extract failed probes
        self._extract_failed_probes(data, signals)
        
        # Extract engine failures
        self._extract_engine_failures(data, signals)
        
        # Extract binding failures
        self._extract_binding_failures(data, signals)
        
        # Extract cleanup failures
        self._extract_cleanup_failures(data, signals)
        
        # Compute statistics
        signals.compute_statistics()
        
        return signals
    
    def _extract_round_number(self, data: dict) -> int:
        """Extract round number from scan data."""
        benchmark = data.get("benchmark_metrics", {})
        return int(benchmark.get("round_number", 1))
    
    def _extract_confirmed_bugs(self, data: dict, signals: EnhancedLearningSignals):
        """Extract confirmed bug signals."""
        summary = data.get("high_value_summary", {})
        bugs = summary.get("confirmed_bugs_list", [])
        
        if not isinstance(bugs, list):
            # Try alternative structure
            bugs = summary.get("discovered", [])
        
        for bug in bugs:
            if isinstance(bug, dict):
                signals.confirmed_bugs.append(
                    LearningSignal.from_confirmed_bug(bug, signals.scan_file, signals.round_number)
                )
    
    def _extract_failed_probes(self, data: dict, signals: EnhancedLearningSignals):
        """Extract failed probe signals."""
        execution = data.get("probe_execution_result", [])
        
        if not isinstance(execution, list):
            return
        
        for item in execution:
            if not isinstance(item, dict):
                continue
            
            # Check if probe failed
            if item.get("assertion_result") == "failed":
                signals.failed_probes.append(
                    LearningSignal.from_failed_probe(
                        item, signals.scan_file, signals.round_number
                    )
                )
    
    def _extract_engine_failures(self, data: dict, signals: EnhancedLearningSignals):
        """Extract engine failure signals."""
        benchmark = data.get("benchmark_metrics", {})
        stage_failures = data.get("stage_failures", [])
        
        for failure in stage_failures:
            if isinstance(failure, str) and ":" in failure:
                # Parse format: "ENGINE_NAME:ErrorClass:message"
                parts = failure.split(":")
                if len(parts) >= 2:
                    signals.engine_failures.append(
                        LearningSignal.from_engine_failure({
                            "engine_name": parts[0],
                            "error_class": parts[1],
                            "failure_count": 1,
                        }, signals.scan_file, signals.round_number)
                    )
    
    def _extract_binding_failures(self, data: dict, signals: EnhancedLearningSignals):
        """Extract binding failure signals."""
        compile_receipt = data.get("compile_receipt", {})
        
        if compile_receipt.get("status") != "COMPILED":
            reason = compile_receipt.get("reason_code", "")
            detail = compile_receipt.get("detail", "")
            
            if "BLOCKED_MISSING_BINDING" in reason:
                signals.binding_failures.append(
                    LearningSignal.from_binding_failure({
                        "placeholder": "{id}",
                        "target_path": detail[:100] if detail else "",
                        "resolver_type": "owner_identity_read",
                        "reason": reason,
                    }, signals.scan_file, signals.round_number)
                )
    
    def _extract_cleanup_failures(self, data: dict, signals: EnhancedLearningSignals):
        """Extract cleanup failure signals."""
        experiments = data.get("v12", {}).get("experiment_compile", {}).get("experiments", [])
        
        for exp in experiments:
            if not isinstance(exp, dict):
                continue
            
            # Check cleanup status
            for phase in ["control_plan", "treatment_plan"]:
                for step in exp.get(phase, []):
                    if not isinstance(step, dict):
                        continue
                    
                    # Look for cleanup indicators
                    if step.get("cleanup_status") == "FAILED":
                        signals.cleanup_failures.append(
                            LearningSignal.from_cleanup_failure({
                                "step_id": step.get("step_id", ""),
                                "cleanup_method": step.get("cleanup_method", "DELETE"),
                                "cleanup_path": step.get("cleanup_path", ""),
                                "failure_reason": step.get("cleanup_reason", ""),
                                "residue_detected": True,
                            }, signals.scan_file, signals.round_number)
                        )
    
    def _empty_signal(self, scan_file: Path) -> EnhancedLearningSignals:
        """Return empty signals structure."""
        return EnhancedLearningSignals(
            project_id=self.project,
            scan_file=str(scan_file),
            round_number=0,
            extracted_at=datetime.now().isoformat(),
        )
    
    def extract_all(self, limit: int = 5) -> list[EnhancedLearningSignals]:
        """Extract signals from recent scans."""
        signals_list = []
        
        for scan_file in sorted(
            self.output_dir.glob("scan_*.json"),
            reverse=True
        )[:limit]:
            signals = self.extract_from_scan(scan_file)
            if signals.total_signals > 0:
                signals_list.append(signals)
        
        return signals_list


def main(project: str = "default_project", root: Path | None = None, limit: int = 3):
    """Main entry point."""
    root = root or REPO_ROOT
    output_dir = root / "platform_outputs" / project
    
    print("=" * 70)
    print("ENHANCED LEARNING SIGNAL EXTRACTION")
    print("=" * 70)
    print(f"Project: {project}")
    print(f"Output directory: {output_dir}")
    print(f"Scanning up to {limit} recent scans...")
    
    extractor = EnhancedSignalExtractor(project, root)
    signals_list = extractor.extract_all(limit=limit)
    
    if not signals_list:
        print("\n⚠️  No signals found. Run discovery first.")
        return
    
    print(f"\nFound {len(signals_list)} scan(s) with signals")
    
    # Print summary for each scan
    for signals in signals_list:
        print(f"\n{'-'*70}")
        print(f"Round {signals.round_number}: {signals.scan_file.name}")
        print(f"  Total signals: {signals.total_signals}")
        print(f"  Critical: {signals.critical_signals}")
        print(f"  Avg learning potential: {signals.avg_learning_potential:.2f}")
        
        print(f"\n  Signals by type:")
        print(f"    Confirmed bugs: {len(signals.confirmed_bugs)}")
        print(f"    Failed probes: {len(signals.failed_probes)}")
        print(f"    Engine failures: {len(signals.engine_failures)}")
        print(f"    Binding failures: {len(signals.binding_failures)}")
        print(f"    Cleanup failures: {len(signals.cleanup_failures)}")
        
        if signals.risk_type_distribution:
            print(f"\n  Top risk types:")
            for risk, count in sorted(
                signals.risk_type_distribution.items(),
                key=lambda x: x[1],
                reverse=True
            )[:5]:
                print(f"    • {risk}: {count}")
        
        if signals.top_recommendations:
            print(f"\n  Recommendations:")
            for rec in signals.top_recommendations[:3]:
                print(f"    • {rec}")
    
    # Save comprehensive report
    report_path = output_dir / "enhanced_learning_signals.json"
    all_data = {
        "project_id": project,
        "generated_at": datetime.now().isoformat(),
        "scans_analyzed": len(signals_list),
        "signals": [s.to_dict() for s in signals_list],
    }
    
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(all_data, f, indent=2, ensure_ascii=False)
    
    print(f"\n{'='*70}")
    print(f"Full report saved to: {report_path}")
    print("="*70)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Extract enhanced learning signals from scan results"
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
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="Number of recent scans to analyze"
    )
    
    args = parser.parse_args()
    main(project=args.project, root=args.root, limit=args.limit)
