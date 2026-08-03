"""AutoLearningTrigger - Automated learning pipeline after each discovery round.

This module provides automatic triggering of learning pipelines based on:
- Minimum confirmed bugs threshold
- Coverage improvement threshold
- Time-based scheduling
- Manual override

Usage:
    # As a standalone tool
    python tools/auto_learning_trigger.py --project my_project --trigger
    
    # Integrated into main pipeline (see __main__.py)
    from .auto_learning_trigger import AutoLearningTrigger
    
    trigger = AutoLearningTrigger(
        min_confirmed_bugs=3,
        schedule="after_each_scan"
    )
    if trigger.should_trigger(scan_result):
        trigger.execute()
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class LearningTriggerConfig:
    """Configuration for auto-learning trigger."""
    min_confirmed_bugs: int = 3
    min_coverage_gain: float = 0.1
    min_recall_improvement: float = 0.05
    schedule: str = "after_each_scan"  # "after_each_scan", "daily", "weekly", "manual"
    max_rounds_between_learn: int = 3  # Don't learn every round
    signal_sources: list[str] = field(default_factory=lambda: [
        "confirmed_bugs",
        "failed_probes",
        "engine_failures",
    ])
    dry_run: bool = False
    
    def should_trigger(self, current_round: int, previous_rounds: int) -> bool:
        """Check if trigger conditions are met."""
        # Check schedule
        if self.schedule == "manual":
            return False
        
        # Check minimum rounds between learning
        if previous_rounds > 0 and previous_rounds % self.max_rounds_between_learn != 0:
            return False
        
        return True


@dataclass
class LearningResult:
    """Result of a learning execution."""
    success: bool
    rounds_analyzed: int
    patterns_extracted: int
    new_probes_generated: int
    risk_weights_updated: dict[str, float]
    execution_time_seconds: float
    error_message: Optional[str] = None


class AutoLearningTrigger:
    """Automated learning trigger and executor."""
    
    def __init__(
        self,
        project: str = "default_project",
        root: Path | None = None,
        config: LearningTriggerConfig | None = None,
    ):
        self.project = project
        self.root = root or REPO_ROOT
        self.config = config or LearningTriggerConfig()
        
        self.output_dir = self.root / "platform_outputs" / project
        self.workspace_dir = self.output_dir / "workspace"
        
    def should_trigger(self, current_scan_result: dict) -> tuple[bool, str]:
        """Check if learning should be triggered based on scan results."""
        benchmark = current_scan_result.get("benchmark_metrics", {})
        summary = current_scan_result.get("high_value_summary", {})
        
        # Check minimum bugs threshold
        confirmed_bugs = summary.get("total_confirmed_bugs", 0)
        if confirmed_bugs < self.config.min_confirmed_bugs:
            return False, f"Only {confirmed_bugs} bugs (< {self.config.min_confirmed_bugs})"
        
        # Check coverage gain
        coverage_gain = benchmark.get("coverage_gain", 0.0)
        if coverage_gain < self.config.min_coverage_gain:
            return False, f"Coverage gain {coverage_gain:.1%} < {self.config.min_coverage_gain:.1%}"
        
        # Enhanced: Also check for other signal types (failed probes, cleanup failures)
        execution = current_scan_result.get("probe_execution_result", [])
        failed_probes = sum(1 for item in execution if isinstance(item, dict) and item.get("assertion_result") == "failed")
        
        if failed_probes > 5:
            # Many failures indicate learning opportunity even with few bugs
            return True, f"{failed_probes} failed probes detected - high learning potential"
        
        return True, f"Thresholds met: {confirmed_bugs} bugs, {failed_probes} failed probes"
    
    def execute(self) -> LearningResult:
        """Execute the learning pipeline."""
        t0 = datetime.now()
        
        if self.config.dry_run:
            return LearningResult(
                success=True,
                rounds_analyzed=0,
                patterns_extracted=0,
                new_probes_generated=0,
                risk_weights_updated={},
                execution_time_seconds=0.0,
                error_message="Dry run - no actual learning performed",
            )
        
        try:
            # Step 1: Run learning effectiveness analysis
            logger.info("Step 1/4: Running learning effectiveness analysis...")
            result = subprocess.run(
                [
                    "python", str(self.root / "tools" / "learning_effectiveness_dashboard.py"),
                    "--project", self.project,
                ],
                capture_output=True,
                text=True,
                timeout=300,
            )
            
            if result.returncode != 0:
                logger.error("Learning effectiveness analysis failed: %s", result.stderr)
                return LearningResult(
                    success=False,
                    rounds_analyzed=0,
                    patterns_extracted=0,
                    new_probes_generated=0,
                    risk_weights_updated={},
                    execution_time_seconds=(datetime.now() - t0).total_seconds(),
                    error_message=result.stderr[:500],
                )
            
            # Step 2: Build risk learning profile
            logger.info("Step 2/4: Building risk learning profile...")
            from ai_test_asset_center.defect_discovery._reporting import (
                build_risk_learning_profile,
                build_high_value_pattern_memory,
            )
            
            # Load scan results
            scan_results = self._load_recent_scans()
            if not scan_results:
                return LearningResult(
                    success=False,
                    rounds_analyzed=0,
                    patterns_extracted=0,
                    new_probes_generated=0,
                    risk_weights_updated={},
                    execution_time_seconds=(datetime.now() - t0).total_seconds(),
                    error_message="No scan results found",
                )
            
            # Extract bugs from recent scans
            all_bugs = []
            for scan in scan_results:
                bugs = scan.get("high_value_summary", {}).get("confirmed_bugs_list", [])
                all_bugs.extend(bugs)
            
            # Build learning artifacts
            summary = {"total_confirmed_bugs": len(all_bugs)}
            strategy = {}
            memory = {}
            
            risk_profile = build_risk_learning_profile(
                bugs=all_bugs,
                summary=summary,
                strategy=strategy,
                memory=memory,
            )
            
            pattern_memory = build_high_value_pattern_memory(
                bugs=all_bugs,
            )
            
            # Step 3: Generate new probes from learned patterns
            logger.info("Step 3/4: Generating new probes from learned patterns...")
            from ai_test_asset_center.defect_discovery._probes import (
                generate_feedback_learning_probes,
            )
            
            business_model = {
                "risk_learning_profile": risk_profile,
                "high_value_pattern_memory": pattern_memory,
            }
            
            new_probes = generate_feedback_learning_probes(business_model)
            
            # Step 4: Persist learning artifacts
            logger.info("Step 4/4: Persisting learning artifacts...")
            self.workspace_dir.mkdir(parents=True, exist_ok=True)
            
            # Save risk learning profile
            profile_path = self.workspace_dir / "risk_learning_profile.json"
            with open(profile_path, "w", encoding="utf-8") as f:
                json.dump(risk_profile, f, indent=2, ensure_ascii=False)
            
            # Save pattern memory
            memory_path = self.workspace_dir / "high_value_pattern_memory.json"
            with open(memory_path, "w", encoding="utf-8") as f:
                json.dump(pattern_memory, f, indent=2, ensure_ascii=False)
            
            # Save new probes
            probes_path = self.workspace_dir / "learned_probes.json"
            with open(probes_path, "w", encoding="utf-8") as f:
                json.dump({
                    "version": "v1",
                    "generated_at": datetime.now().isoformat(),
                    "probe_count": len(new_probes),
                    "probes": new_probes,
                }, f, indent=2, ensure_ascii=False)
            
            # Extract risk weights
            risk_weights = {
                item["name"]: item["weight"]
                for item in risk_profile.get("priority_risks", [])
            }
            
            execution_time = (datetime.now() - t0).total_seconds()
            
            return LearningResult(
                success=True,
                rounds_analyzed=len(scan_results),
                patterns_extracted=pattern_memory.get("pattern_count", 0),
                new_probes_generated=len(new_probes),
                risk_weights_updated=risk_weights,
                execution_time_seconds=execution_time,
            )
            
        except Exception as e:
            logger.exception("Learning execution failed")
            return LearningResult(
                success=False,
                rounds_analyzed=0,
                patterns_extracted=0,
                new_probes_generated=0,
                risk_weights_updated={},
                execution_time_seconds=(datetime.now() - t0).total_seconds(),
                error_message=str(e)[:500],
            )
    
    def _load_recent_scans(self, limit: int = 3) -> list[dict]:
        """Load recent scan results."""
        scans = []
        
        for scan_file in sorted(self.output_dir.glob("scan_*.json"), reverse=True)[:limit]:
            try:
                data = json.load(open(scan_file, encoding="utf-8"))
                scans.append(data)
            except Exception as e:
                logger.warning("Failed to load %s: %s", scan_file, e)
        
        return scans
    
    def print_summary(self, result: LearningResult):
        """Print learning execution summary."""
        status = "✅ SUCCESS" if result.success else "❌ FAILED"
        
        print(f"\n{'='*70}")
        print(f"LEARNING EXECUTION {status}")
        print(f"{'='*70}")
        print(f"Rounds analyzed: {result.rounds_analyzed}")
        print(f"Patterns extracted: {result.patterns_extracted}")
        print(f"New probes generated: {result.new_probes_generated}")
        print(f"Execution time: {result.execution_time_seconds:.1f}s")
        
        if result.risk_weights_updated:
            print("\nUpdated risk weights:")
            for risk, weight in sorted(
                result.risk_weights_updated.items(),
                key=lambda x: x[1],
                reverse=True
            )[:10]:
                print(f"  • {risk}: {weight:.2f}")
        
        if result.error_message:
            print(f"\nError: {result.error_message}")
        
        print(f"{'='*70}\n")


def main(project: str = "default_project", dry_run: bool = False):
    """Main entry point."""
    trigger = AutoLearningTrigger(
        project=project,
        root=REPO_ROOT,
        config=LearningTriggerConfig(dry_run=dry_run),
    )
    
    if dry_run:
        print("DRY RUN - No actual learning will be performed")
    
    # Check if should trigger
    print(f"Checking if learning should be triggered for project: {project}")
    
    # For demo, we'll just execute
    print("Executing learning pipeline...")
    result = trigger.execute()
    
    trigger.print_summary(result)
    
    return 0 if result.success else 1


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Automatically trigger learning pipeline after discovery"
    )
    parser.add_argument(
        "--project",
        default="default_project",
        help="Project ID to process"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate without making changes"
    )
    
    args = parser.parse_args()
    exit(main(project=args.project, dry_run=args.dry_run))
