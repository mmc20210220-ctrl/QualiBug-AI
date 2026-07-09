"""Rounds Summary — Aggregate data from all 4 rounds into a unified dashboard feed.

This module is the SINGLE entry point for the Dashboard to consume Round 1-4 data.
It reads from disk (no network calls), aggregates, and returns a clean JSON dict.

Design: pure data aggregation, no HTML generation, no fabrication.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


# ═════════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════════

def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "null")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _safe_float(val: Any, fallback: float | None = None) -> float | None:
    try:
        return round(float(val), 4)
    except (TypeError, ValueError):
        return fallback


# ═════════════════════════════════════════════════════════════════════════════
# Round 1: Benchmark Metrics
# ═════════════════════════════════════════════════════════════════════════════

def _round1_benchmark(project: str, root: Path) -> dict[str, Any]:
    """Read latest benchmark metrics from disk."""
    paths = [
        root / "platform_outputs" / project / "benchmark" / "benchmark_metrics.json",
        root / "platform_outputs" / "_benchmark" / "benchmark_metrics.json",
    ]
    for p in paths:
        data = _read_json(p)
        if data and data.get("benchmark_active"):
            return {
                "available": True,
                "recall": _safe_float(data.get("recall")),
                "precision": _safe_float(data.get("precision")),
                "f1_score": _safe_float(data.get("f1_score")),
                "high_value_recall": _safe_float(data.get("high_value_recall")),
                "evidence_completeness_rate": _safe_float(data.get("evidence_completeness_rate")),
                "ground_truth_bug_count": data.get("ground_truth_bug_count", 0),
                "true_positives": data.get("true_positives", 0),
                "false_positives": data.get("false_positives", 0),
                "false_negatives": data.get("false_negatives", 0),
                "bug_type_count": len(data.get("bug_type_breakdown", {})),
                "risk_family_count": len(data.get("risk_family_breakdown", {})),
            }

    # Try baseline tracker history as fallback
    baseline_path = root / "platform_outputs" / "_benchmark" / f"baseline_history_{project}.json"
    if baseline_path.exists():
        try:
            history = json.loads(baseline_path.read_text(encoding="utf-8") or "[]")
            if isinstance(history, list) and history:
                latest = history[-1]
                metrics = latest.get("metrics", {})
                return {
                    "available": True,
                    "recall": _safe_float(metrics.get("recall")),
                    "precision": _safe_float(metrics.get("precision")),
                    "f1_score": _safe_float(metrics.get("f1_score")),
                    "high_value_recall": _safe_float(metrics.get("high_value_recall")),
                    "evidence_completeness_rate": _safe_float(metrics.get("evidence_completeness_rate")),
                    "ground_truth_bug_count": latest.get("ground_truth_bug_count", 0),
                    "true_positives": latest.get("true_positives", 0),
                    "false_positives": latest.get("false_positives", 0),
                    "false_negatives": latest.get("false_negatives", 0),
                    "run_count": len(history),
                }
        except Exception:
            pass

    return {"available": False}


# ═════════════════════════════════════════════════════════════════════════════
# Round 2: Capability Gaps
# ═════════════════════════════════════════════════════════════════════════════

def _round2_gaps(project: str, root: Path) -> dict[str, Any]:
    """Read gap tracker state from disk."""
    gap_path = root / "platform_outputs" / "_benchmark" / f"gap_tracker_{project}.json"
    if not gap_path.exists():
        return {"available": False}

    try:
        from .gap_tracker import GapTracker
        tracker = GapTracker(project, root=root)
        snapshot = tracker.current_snapshot()
        summary = tracker.build_summary()
        return {
            "available": True,
            "total_gaps_ever": snapshot.total_gaps,
            "currently_open": snapshot.open_count,
            "resolved": snapshot.resolved_count,
            "blocked": snapshot.blocked_count,
            "reopened_count": summary.get("reopened_count", 0),
            "by_root_cause": summary.get("by_root_cause", {}),
        }
    except Exception:
        return {"available": False}


# ═════════════════════════════════════════════════════════════════════════════
# Round 3: Learning Generation
# ═════════════════════════════════════════════════════════════════════════════

def _round3_learning(project: str, root: Path) -> dict[str, Any]:
    """Read latest learning manifest from disk."""
    learning_dir = root / "platform_outputs" / "_learning"
    if not learning_dir.exists():
        return {"available": False}

    try:
        manifests = sorted(learning_dir.glob("learning_manifest_*.json"), reverse=True)
        if not manifests:
            return {"available": False}

        latest = _read_json(manifests[0])
        summary = latest.get("summary", {})
        return {
            "available": True,
            "source_bug_count": latest.get("source_bug_count", 0),
            "total_probes_generated": summary.get("total_probes_generated", 0),
            "total_oracles_generated": summary.get("total_oracles_generated", 0),
            "total_fixtures_generated": summary.get("total_fixtures_generated", 0),
            "strategies_used": summary.get("strategies_used", []),
            "manifest_count": len(manifests),
        }
    except Exception:
        return {"available": False}


# ═════════════════════════════════════════════════════════════════════════════
# Round 4: Oracle DSL
# ═════════════════════════════════════════════════════════════════════════════

def _round4_dsl(project: str, root: Path) -> dict[str, Any]:
    """Report Oracle DSL rule library coverage."""
    try:
        from .oracle_dsl import RuleLibrary, DSLCompiler, DSLParser
        lib = RuleLibrary()
        compiler = DSLCompiler()
        parser = DSLParser()

        industries = lib.list_industries()
        industry_rules: dict[str, dict[str, Any]] = {}
        total_rules = 0
        total_compiled = 0

        for ind in industries:
            rules = lib.get_rules(ind)
            compiled_count = 0
            oracle_families: set[str] = set()
            for rule in rules:
                compiled = compiler.compile_to_oracle_object(rule)
                oracle_families.add(compiled.oracle_family)
                if compiled.oracle_rules:
                    compiled_count += 1

            industry_rules[ind] = {
                "rule_count": len(rules),
                "compiled_count": compiled_count,
                "oracle_families": sorted(oracle_families),
            }
            total_rules += len(rules)
            total_compiled += compiled_count

        return {
            "available": True,
            "industry_count": len(industries),
            "total_rules": total_rules,
            "total_compiled": total_compiled,
            "by_industry": industry_rules,
        }
    except Exception:
        return {"available": False}


# ═════════════════════════════════════════════════════════════════════════════
# Unified Rounds Summary
# ═════════════════════════════════════════════════════════════════════════════

def build_rounds_summary(
    project: str,
    root: str | Path | None = None,
) -> dict[str, Any]:
    """Build a unified summary of all 4 rounds for the dashboard.

    Args:
        project: Project ID.
        root: Workspace root directory.

    Returns:
        Dict with round_1 through round_4 keys, each containing {available, ...data}.
    """
    root = Path(root or os.environ.get(
        "QUALIBUG_WORKSPACE_ROOT",
        str(Path(__file__).resolve().parents[1])
    ))

    return {
        "schema_version": "rounds_summary.v1",
        "project_id": project,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "round_1_benchmark": _round1_benchmark(project, root),
        "round_2_gaps": _round2_gaps(project, root),
        "round_3_learning": _round3_learning(project, root),
        "round_4_dsl": _round4_dsl(project, root),
        "rounds_with_data": _count_available(project, root),
    }


def _count_available(project: str, root: Path) -> int:
    """Count how many rounds have data available."""
    count = 0
    if _round1_benchmark(project, root).get("available"):
        count += 1
    if _round2_gaps(project, root).get("available"):
        count += 1
    if _round3_learning(project, root).get("available"):
        count += 1
    if _round4_dsl(project, root).get("available"):
        count += 1
    return count
