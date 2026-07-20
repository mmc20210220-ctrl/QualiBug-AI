"""Discovery strategy learning and adaptive optimization.

This module tracks discovery performance across scans and learns optimal
strategies for hypothesis generation, engine selection, and execution planning.

Key features:
- Historical hit rate tracking per engine and risk type
- Route matching success/failure pattern learning
- Dynamic engine weight adjustment
- Execution strategy optimization based on past performance
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

# ── Default learning storage path ──
_DEFAULT_LEARNING_DIR = Path.home() / ".qualibug" / "learning"


class DiscoveryLearner:
    """Learns and adapts discovery strategies from historical performance."""

    def __init__(self, project: str = "", learning_dir: Path | None = None):
        self.project = project or os.environ.get("QUALIBUG_PROJECT", "default")
        self.learning_dir = learning_dir or _DEFAULT_LEARNING_DIR
        self.learning_dir.mkdir(parents=True, exist_ok=True)
        self._history = self._load_history()

    def _history_file(self) -> Path:
        return self.learning_dir / f"discovery_history_{self.project}.json"

    def _load_history(self) -> dict[str, Any]:
        """Load historical learning data."""
        path = self._history_file()
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "project": self.project,
            "created_at": time.time(),
            "scans": [],
            "by_engine": {},
            "by_risk_type": {},
            "by_route_pattern": {},
            "engine_weights": {},
            "risk_type_weights": {},
        }

    def _save_history(self) -> None:
        """Persist learning data."""
        path = self._history_file()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._history, f, ensure_ascii=False, indent=2)
        except Exception:
            pass  # Non-fatal

    def record_scan_result(
        self,
        findings: list[dict[str, Any]],
        hypotheses: list[dict[str, Any]],
        execution_results: list[dict[str, Any]],
        scan_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record results from a completed scan for learning.

        Returns a summary of what was learned.
        """
        scan_record = {
            "timestamp": time.time(),
            "metadata": scan_metadata or {},
            "hypothesis_count": len(hypotheses),
            "execution_count": len(execution_results),
            "finding_count": len(findings),
            "confirmed_count": sum(1 for f in findings if f.get("verdict") == "confirmed"),
            "falsified_count": sum(1 for f in findings if f.get("verdict") == "falsified"),
        }

        # Update engine statistics
        engine_stats = self._update_engine_stats(findings, hypotheses)
        scan_record["engine_stats"] = engine_stats

        # Update risk type statistics
        risk_stats = self._update_risk_type_stats(findings)
        scan_record["risk_stats"] = risk_stats

        # Update route pattern statistics
        route_stats = self._update_route_stats(execution_results)
        scan_record["route_stats"] = route_stats

        # Store scan record (keep last 50 scans)
        self._history["scans"].append(scan_record)
        if len(self._history["scans"]) > 50:
            self._history["scans"] = self._history["scans"][-50:]

        # Recompute adaptive weights
        self._recompute_weights()

        # Persist
        self._save_history()

        return {
            "status": "recorded",
            "scan_record": scan_record,
            "engine_weights": self._history.get("engine_weights", {}),
            "risk_type_weights": self._history.get("risk_type_weights", {}),
        }

    def _update_engine_stats(
        self,
        findings: list[dict[str, Any]],
        hypotheses: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Update per-engine hit rate statistics."""
        by_engine = self._history.setdefault("by_engine", {})

        # Map hypotheses to their source engines
        hyp_engines: dict[str, str] = {}
        for h in hypotheses:
            h_id = str(h.get("hypothesis_id") or "")
            engine = str(h.get("engine") or h.get("source_engine") or "unknown")
            hyp_engines[h_id] = engine

        # Update stats based on findings
        for f in findings:
            h_id = str(f.get("hypothesis_id") or "")
            engine = hyp_engines.get(h_id, "unknown")
            verdict = str(f.get("verdict") or "").lower()

            stats = by_engine.setdefault(engine, {
                "total": 0,
                "confirmed": 0,
                "falsified": 0,
                "inconclusive": 0,
                "hit_rate": 0.0,
            })
            stats["total"] += 1
            if verdict == "confirmed":
                stats["confirmed"] += 1
            elif verdict == "falsified":
                stats["falsified"] += 1
            else:
                stats["inconclusive"] += 1

            # Update hit rate
            if stats["total"] > 0:
                stats["hit_rate"] = stats["confirmed"] / stats["total"]

        return by_engine

    def _update_risk_type_stats(self, findings: list[dict[str, Any]]) -> dict[str, Any]:
        """Update per-risk-type hit rate statistics."""
        by_risk = self._history.setdefault("by_risk_type", {})

        for f in findings:
            risk_type = str(f.get("risk_type") or f.get("category") or "unknown").lower()
            verdict = str(f.get("verdict") or "").lower()

            stats = by_risk.setdefault(risk_type, {
                "total": 0,
                "confirmed": 0,
                "falsified": 0,
                "hit_rate": 0.0,
            })
            stats["total"] += 1
            if verdict == "confirmed":
                stats["confirmed"] += 1
            elif verdict == "falsified":
                stats["falsified"] += 1

            if stats["total"] > 0:
                stats["hit_rate"] = stats["confirmed"] / stats["total"]

        return by_risk

    def _update_route_stats(self, execution_results: list[dict[str, Any]]) -> dict[str, Any]:
        """Update route matching success/failure patterns."""
        by_route = self._history.setdefault("by_route_pattern", {})

        for r in execution_results:
            evidence = r.get("evidence", {})
            if not isinstance(evidence, dict):
                continue

            # Extract route pattern
            calls = evidence.get("calls", [])
            for call in calls:
                call_str = str(call.get("call") or "")
                if " " in call_str:
                    method, path = call_str.split(" ", 1)
                    # Normalize path (replace IDs with placeholders)
                    import re
                    normalized = re.sub(r"/\d+", "/{id}", path)
                    normalized = re.sub(r"/[0-9a-f]{8,}", "/{uuid}", normalized, flags=re.IGNORECASE)
                    pattern_key = f"{method.upper()} {normalized}"

                    stats = by_route.setdefault(pattern_key, {
                        "attempts": 0,
                        "successes": 0,
                        "failures": 0,
                    })
                    stats["attempts"] += 1

                    status = call.get("results", {}).get("admin", {}).get("status", 0)
                    if 200 <= status < 400:
                        stats["successes"] += 1
                    elif status >= 400:
                        stats["failures"] += 1

        return by_route

    def _recompute_weights(self) -> None:
        """Recompute adaptive weights from historical data."""
        # Engine weights: higher hit rate → higher weight
        engine_weights = {}
        for engine, stats in self._history.get("by_engine", {}).items():
            total = stats.get("total", 0)
            if total >= 3:
                hit_rate = stats.get("hit_rate", 0.0)
                # Scale to 0.2 - 1.0 range
                weight = 0.2 + 0.8 * min(1.0, hit_rate * 2.0)
                engine_weights[engine] = round(weight, 3)
            else:
                engine_weights[engine] = 0.60  # Default neutral
        self._history["engine_weights"] = engine_weights

        # Risk type weights: higher hit rate → higher priority
        risk_weights = {}
        for risk_type, stats in self._history.get("by_risk_type", {}).items():
            total = stats.get("total", 0)
            if total >= 2:
                hit_rate = stats.get("hit_rate", 0.0)
                weight = 0.3 + 0.7 * min(1.0, hit_rate * 1.5)
                risk_weights[risk_type] = round(weight, 3)
            else:
                risk_weights[risk_type] = 0.50
        self._history["risk_type_weights"] = risk_weights

    def get_engine_weights(self) -> dict[str, float]:
        """Get current adaptive engine weights."""
        return dict(self._history.get("engine_weights", {}))

    def get_risk_type_weights(self) -> dict[str, float]:
        """Get current adaptive risk type weights."""
        return dict(self._history.get("risk_type_weights", {}))

    def get_hypothesis_priorities(self) -> dict[str, Any]:
        """Get learned hypothesis prioritization hints."""
        return {
            "engine_weights": self.get_engine_weights(),
            "risk_type_weights": self.get_risk_type_weights(),
            "by_engine": self._history.get("by_engine", {}),
            "by_risk_type": self._history.get("by_risk_type", {}),
        }

    def get_route_reliability(self, method: str, path_pattern: str) -> float:
        """Get historical reliability score for a route pattern."""
        import re
        normalized = re.sub(r"/\d+", "/{id}", path_pattern)
        normalized = re.sub(r"/[0-9a-f]{8,}", "/{uuid}", normalized, flags=re.IGNORECASE)
        pattern_key = f"{method.upper()} {normalized}"

        stats = self._history.get("by_route_pattern", {}).get(pattern_key, {})
        attempts = stats.get("attempts", 0)
        if attempts < 3:
            return 0.50  # Unknown reliability

        successes = stats.get("successes", 0)
        return successes / attempts

    def suggest_execution_strategy(self) -> dict[str, Any]:
        """Suggest execution strategy based on learned patterns."""
        scans = self._history.get("scans", [])
        if len(scans) < 3:
            return {"strategy": "default", "reason": "insufficient_history"}

        # Analyze recent trends
        recent = scans[-5:]
        avg_hit_rate = sum(
            s.get("confirmed_count", 0) / max(s.get("execution_count", 1), 1)
            for s in recent
        ) / len(recent)

        # Find best performing engines
        engine_weights = self.get_engine_weights()
        top_engines = sorted(engine_weights.items(), key=lambda x: -x[1])[:3]

        # Find best performing risk types
        risk_weights = self.get_risk_type_weights()
        top_risks = sorted(risk_weights.items(), key=lambda x: -x[1])[:3]

        strategy = {
            "strategy": "adaptive",
            "avg_hit_rate": round(avg_hit_rate, 3),
            "recommended_engines": [e for e, _ in top_engines],
            "priority_risk_types": [r for r, _ in top_risks],
            "suggested_budget_multiplier": 1.0 + min(0.5, avg_hit_rate),
        }

        # If hit rate is high, expand coverage
        if avg_hit_rate > 0.20:
            strategy["suggested_budget_multiplier"] = 1.3
            strategy["strategy"] = "expand_coverage"
        # If hit rate is low, focus on high-value targets
        elif avg_hit_rate < 0.05:
            strategy["suggested_budget_multiplier"] = 0.8
            strategy["strategy"] = "focus_high_value"

        return strategy


# ── Module-level singleton for convenience ──
_global_learner: DiscoveryLearner | None = None


def get_discovery_learner(project: str = "") -> DiscoveryLearner:
    """Get or create the global discovery learner instance."""
    global _global_learner
    if _global_learner is None or (project and _global_learner.project != project):
        _global_learner = DiscoveryLearner(project=project)
    return _global_learner


def record_discovery_results(
    findings: list[dict[str, Any]],
    hypotheses: list[dict[str, Any]],
    execution_results: list[dict[str, Any]],
    project: str = "",
) -> dict[str, Any]:
    """Convenience function to record discovery results."""
    learner = get_discovery_learner(project)
    return learner.record_scan_result(findings, hypotheses, execution_results)
