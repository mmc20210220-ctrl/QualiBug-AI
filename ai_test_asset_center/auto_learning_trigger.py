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
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _as_text(value: Any) -> str:
    return str(value or "").strip()


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
        """Check if learning should be triggered based on scan results.

        Signals are evaluated independently with OR semantics. The previous
        gate read legacy fields that no mainline v12 scan ever writes
        (``high_value_summary.total_confirmed_bugs``,
        ``benchmark_metrics.coverage_gain``, top-level
        ``probe_execution_result``), so the pipeline never fired. Signals now
        read the authoritative v12 fields:

        - ``formal_count_projection.formal_customer_deliverable_count`` — the
          verified customer-deliverable defect count (falls back to
          ``canonical_defect_count``);
        - ``pipeline_health.blocked_obligation_count`` — binding/execution
          blockage is itself a learning opportunity (binding experience
          extraction feeds the next round), even when few defects surfaced.

        The decision reason is explicit and receipt-friendly.
        """
        projection = {}
        if isinstance(current_scan_result.get("formal_count_projection"), dict):
            projection = current_scan_result["formal_count_projection"]
        health = {}
        if isinstance(current_scan_result.get("pipeline_health"), dict):
            health = current_scan_result["pipeline_health"]

        confirmed_bugs = int(projection.get("formal_customer_deliverable_count") or 0)
        if not confirmed_bugs:
            confirmed_bugs = int(projection.get("canonical_defect_count") or 0)
        blocked_obligations = int(health.get("blocked_obligation_count") or 0)

        signals: list[str] = []
        if confirmed_bugs >= self.config.min_confirmed_bugs:
            signals.append(
                f"confirmed_bugs={confirmed_bugs}>={self.config.min_confirmed_bugs}"
            )
        if blocked_obligations > 0:
            signals.append(f"blocked_obligations={blocked_obligations}>0")

        if signals:
            return True, "SIGNALS_MET:" + ",".join(signals)

        return False, (
            "NO_SIGNALS:"
            f"confirmed_bugs={confirmed_bugs}<{self.config.min_confirmed_bugs},"
            f"blocked_obligations={blocked_obligations}<=0"
        )
    
    def execute(self, scan_result: dict | None = None) -> LearningResult:
        """Execute the learning pipeline.

        Extracts reusable detection signals from this scan's confirmed
        defects and persists them back into the SQLite knowledge base
        (category ``risk_pattern``), so the next scan's planning boost and
        reasoner memory block consume them. The legacy ``learned_probes.json``
        output had no mainline consumer (probe-pool files are not part of the
        v12 Behavior IR mainline) and is no longer produced; learned signals
        now flow through the same SQLite read side as closed-loop patterns.
        """
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
            # Step 1: Locate this scan's confirmed findings
            if scan_result is None:
                recent = self._load_recent_scans(limit=1)
                scan_result = recent[0] if recent else {}
            findings = self._extract_confirmed_findings(scan_result)
            if not findings:
                return LearningResult(
                    success=False,
                    rounds_analyzed=len(self._load_recent_scans(limit=3)),
                    patterns_extracted=0,
                    new_probes_generated=0,
                    risk_weights_updated={},
                    execution_time_seconds=(datetime.now() - t0).total_seconds(),
                    error_message="No confirmed findings in scan result",
                )

            # Step 2: Build pattern memory and extract detection signals
            from .bug_pattern_memory import BugPatternMemory

            memory = BugPatternMemory()
            for finding in findings:
                memory.add(finding)
            signals = memory.extract_detection_signals(min_frequency=1)

            # Step 3: Persist signals back into the SQLite knowledge base so
            # the next scan's planning boost / reasoner memory block consume
            # them (single read-side SSOT: LearningPatternBridge).
            stored_count = self._persist_signals_to_kb(signals)
            risk_weights = {
                str(signal.get("category") or "unknown"): float(signal.get("frequency") or 1)
                for signal in signals
            }

            # Step 4: Keep an observable profile artifact (reporting only)
            self.workspace_dir.mkdir(parents=True, exist_ok=True)
            profile = {
                "schema_version": "qualibug.auto-learning-profile.v2",
                "generated_at": datetime.now().isoformat(),
                "project": self.project,
                "signals": signals,
                "kb_patterns_stored": stored_count,
                "source": "auto_learning_trigger",
            }
            (self.workspace_dir / "high_value_pattern_memory.json").write_text(
                json.dumps(profile, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            return LearningResult(
                success=True,
                rounds_analyzed=len(self._load_recent_scans(limit=3)),
                patterns_extracted=len(signals),
                new_probes_generated=0,
                risk_weights_updated=risk_weights,
                execution_time_seconds=(datetime.now() - t0).total_seconds(),
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

    def _extract_confirmed_findings(self, scan_result: dict) -> list[dict]:
        """Extract confirmed (customer-deliverable) findings from a v12 scan.

        Reads the authoritative registry / projection fields that mainline
        scans actually emit — never the legacy ``high_value_summary`` shape.
        """
        findings: list[dict] = []

        registry = scan_result.get("canonical_defect_registry") or {}
        if isinstance(registry, dict):
            for item in registry.get("canonical_defects") or []:
                if not isinstance(item, dict):
                    continue
                identity = item.get("identity") if isinstance(item.get("identity"), dict) else {}
                operation = identity.get("operation") if isinstance(identity.get("operation"), dict) else {}
                findings.append({
                    "title": _as_text(operation.get("source_locator") or item.get("canonical_defect_id")),
                    "category": _as_text(identity.get("property") or "uncategorized"),
                    "severity": _as_text(item.get("severity") or "P2"),
                    "canonical_defect_id": _as_text(item.get("canonical_defect_id")),
                    "occurrence_count": int(item.get("occurrence_count") or 0),
                })

        projection = scan_result.get("formal_count_projection") or {}
        if isinstance(projection, dict):
            for item in projection.get("canonical_representative_findings") or []:
                if not isinstance(item, dict):
                    continue
                findings.append({
                    "title": _as_text(item.get("title")),
                    "category": _as_text(
                        item.get("category") or item.get("risk_family") or "uncategorized"
                    ),
                    "severity": _as_text(item.get("severity") or "P2"),
                    "obligation_id": _as_text(item.get("obligation_id")),
                    "experiment_id": _as_text(item.get("experiment_id")),
                })

        # De-duplicate on canonical_defect_id where present, then on title.
        seen_titles: set[str] = set()
        deduped: list[dict] = []
        for finding in findings:
            key = _as_text(finding.get("canonical_defect_id")) or _as_text(finding.get("title"))
            if key and key in seen_titles:
                continue
            if key:
                seen_titles.add(key)
            deduped.append(finding)
        return deduped

    def _persist_signals_to_kb(self, signals: list[dict]) -> int:
        """Write detection signals into the SQLite knowledge base.

        Product-owned confirmed-defect signals only; never benchmark or
        customer vocabulary. Each signal becomes a ``risk_pattern`` entry so
        the existing planning boost / reasoner memory read side consumes it.
        """
        if not signals:
            return 0
        from .learning_pattern_bridge import LearningPatternBridge

        patterns = []
        for signal in signals:
            category = _as_text(signal.get("category") or "uncategorized")
            pattern_name = _as_text(signal.get("pattern_name") or f"learned_{category}")
            patterns.append({
                "signature": f"learned:{pattern_name}",
                "type": f"learned:{category}",
                "entity": "",
                "mutation_hint": "",
                "count": int(signal.get("frequency") or 1),
                "_source": "auto_learning_trigger",
            })
        bridge = LearningPatternBridge(project=self.project)
        return bridge.store_patterns(
            patterns,
            scan_id="auto_learning",
            confidence=0.8,
        )
    
    def _load_recent_scans(self, limit: int = 3) -> list[dict]:
        """Load recent scan results.

        Reads the per-round immutable trace ledgers
        (``platform_outputs/<project>/discovery_evolution/trace_ledgers/``)
        plus the latest ``scan_result.json`` — the real persisted history.
        The previous glob of ``scan_*.json`` matched only the overwritten
        ``scan_result.json``, so ``rounds_analyzed`` was always 1.

        P0-4 Dual Read (SPEC §33): artifactized ledgers (Run Manifest
        trace_refs → ArtifactStore) are loaded first; legacy files remain the
        fallback for older runs.
        """
        scans: list[dict] = []
        try:
            from .trace_artifactization import load_round_trace_ledgers

            store_ledgers = load_round_trace_ledgers(
                self.project, self.root, limit=limit
            )
            scans.extend(store_ledgers[-limit:])
        except Exception as e:
            logger.warning("Failed to load artifactized trace ledgers: %s", e)
        if len(scans) >= limit:
            return scans[:limit]
        ledger_dir = (
            self.output_dir
            / "discovery_evolution"
            / "trace_ledgers"
        )
        ledger_files: list[Path] = []
        if ledger_dir.exists():
            ledger_files = sorted(
                ledger_dir.glob("*/*.trace-ledger.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        for ledger_path in ledger_files[:limit]:
            try:
                data = json.loads(ledger_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    scans.append(data)
            except Exception as e:
                logger.warning("Failed to load %s: %s", ledger_path, e)

        latest = self.output_dir / "scan_result.json"
        if latest.exists():
            try:
                from .scan_result_store import load_scan_result

                data = load_scan_result(
                    latest,
                    keys=[
                        "findings", "candidate_findings", "delivery_occurrences",
                        "canonical_defect_registry", "formal_count_projection",
                        "trace_ledger", "obligation_attempt_ledger",
                    ],
                )
                if isinstance(data, dict):
                    scans.append(data)
            except Exception as e:
                logger.warning("Failed to load %s: %s", latest, e)
        return scans[:limit]
    
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
