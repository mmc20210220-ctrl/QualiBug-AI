"""
V12.5 Continuous Evaluation — CI/CD quality gate with trend tracking.

Three components:
  ScanHistoryDB    — SQLite-backed scan result store with timestamps
  DiffEngine       — Compare two scans: new bugs, fixed bugs, regressions
  TrendReporter    — Coverage/bug count/oracle trends over time
"""

from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ═══════════════════════════════════════════════════════
# ScanHistoryDB
# ═══════════════════════════════════════════════════════

@dataclass
class ScanRecord:
    scan_id: str
    project: str
    timestamp_utc: str
    total_findings: int
    coverage: float
    system_grade: str
    overall_score: float
    oracle_count: int
    scenarios: int
    executed: int
    duration_ms: int
    raw_summary: dict = field(default_factory=dict)


class ScanHistoryDB:
    """SQLite-backed scan history for trend analysis."""

    def __init__(self, db_path: Path = None):
        self.db_path = db_path or Path("scan_history.db")
        self._init()

    def _init(self):
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS scans (scan_id TEXT PRIMARY KEY, project TEXT NOT NULL, timestamp_utc TEXT NOT NULL, total_findings INTEGER, coverage REAL, system_grade TEXT, overall_score REAL, oracle_count INTEGER, scenarios INTEGER, executed INTEGER, duration_ms INTEGER, raw_summary TEXT)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_project_time ON scans(project, timestamp_utc)")
            conn.commit()

    def save(self, evaluation_result: Any, v12_result: dict, project: str = "default") -> str:
        scan_id = f"{project}_{int(time.time()*1000)}"
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        r = evaluation_result
        cov = r.coverage.to_dict() if hasattr(r, "coverage") else r.get("coverage", {})
        findings = v12_result.get("findings", [])
        phases = v12_result.get("phases", {})
        sc = phases.get("scenario_generation", {})

        oracles_triggered = len(set(
            f.get("oracle", {}).get("oracle_name", f.get("oracle", {}).get("oracle", "?"))
            for f in findings
        ))

        record = ScanRecord(
            scan_id=scan_id, project=project, timestamp_utc=timestamp,
            total_findings=len(findings),
            coverage=cov.get("overall_coverage", 0) if isinstance(cov, dict) else 0,
            system_grade=r.system_grade if hasattr(r, "system_grade") else "?",
            overall_score=r.overall_score if hasattr(r, "overall_score") else 0,
            oracle_count=oracles_triggered,
            scenarios=sc.get("total_scenarios", 0),
            executed=phases.get("execution", {}).get("executed", 0),
            duration_ms=v12_result.get("total_duration_ms", 0),
            raw_summary={"findings_by_severity": self._count_severity(findings)}
        )

        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""INSERT OR REPLACE INTO scans VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (record.scan_id, record.project, record.timestamp_utc,
                 record.total_findings, record.coverage, record.system_grade,
                 record.overall_score, record.oracle_count, record.scenarios,
                 record.executed, record.duration_ms,
                 json.dumps(record.raw_summary, ensure_ascii=False)))
            conn.commit()
        return scan_id

    def get_history(self, project: str = "default", limit: int = 20) -> list[ScanRecord]:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM scans WHERE project=? ORDER BY timestamp_utc DESC LIMIT ?",
                (project, limit)).fetchall()
        return [ScanRecord(
            scan_id=r["scan_id"], project=r["project"], timestamp_utc=r["timestamp_utc"],
            total_findings=r["total_findings"], coverage=r["coverage"],
            system_grade=r["system_grade"], overall_score=r["overall_score"],
            oracle_count=r["oracle_count"], scenarios=r["scenarios"],
            executed=r["executed"], duration_ms=r["duration_ms"],
            raw_summary=json.loads(r["raw_summary"]) if r["raw_summary"] else {},
        ) for r in rows]

    def _count_severity(self, findings: list) -> dict:
        counts = {"P0": 0, "P1": 0, "P2": 0}
        for f in findings:
            sev = f.get("severity", "P2")
            counts[sev] = counts.get(sev, 0) + 1
        return counts


# ═══════════════════════════════════════════════════════
# Diff Engine
# ═══════════════════════════════════════════════════════

@dataclass
class ScanDiff:
    previous_scan_id: str
    current_scan_id: str
    new_bugs: int = 0
    fixed_bugs: int = 0
    regressions: int = 0
    coverage_delta: float = 0.0
    score_delta: float = 0.0
    details: dict = field(default_factory=dict)


class DiffEngine:
    """Compare two scans to detect new bugs, fixed bugs, and regressions."""

    def diff(self, prev: ScanRecord, curr: ScanRecord, prev_findings: list = None, curr_findings: list = None) -> ScanDiff:
        d = ScanDiff(previous_scan_id=prev.scan_id, current_scan_id=curr.scan_id)

        # Coverage delta
        d.coverage_delta = curr.coverage - prev.coverage
        d.score_delta = curr.overall_score - prev.overall_score

        # Finding-level diff
        if prev_findings and curr_findings:
            prev_titles = {self._fingerprint(f) for f in prev_findings}
            curr_titles = {self._fingerprint(f) for f in curr_findings}

            new_titles = curr_titles - prev_titles
            fixed_titles = prev_titles - curr_titles

            # New bugs
            new_bugs = [f for f in curr_findings if self._fingerprint(f) in new_titles]
            d.new_bugs = len(new_bugs)

            # Fixed bugs (present in prev, gone in curr)
            fixed_bugs = [f for f in prev_findings if self._fingerprint(f) in fixed_titles]
            d.fixed_bugs = len(fixed_bugs)

            # Regressions: P0 bugs that were fixed and came back, or coverage drop
            if d.coverage_delta < -0.05:
                d.regressions = max(d.regressions, 1)

            d.details = {
                "new_bug_titles": [f.get("title", "")[:80] for f in new_bugs[:5]],
                "fixed_bug_titles": [f.get("title", "")[:80] for f in fixed_bugs[:5]],
                "severity_breakdown": {
                    "new": self._count_sev(new_bugs),
                    "fixed": self._count_sev(fixed_bugs),
                },
            }

        return d

    def _fingerprint(self, finding: dict) -> str:
        """Stable fingerprint for finding dedup across scans."""
        title = finding.get("title", "")
        category = finding.get("category", "")
        path = finding.get("path", "")
        return f"{category}:{title[:60]}:{path}"

    def _count_sev(self, findings: list) -> dict:
        c = {"P0": 0, "P1": 0, "P2": 0}
        for f in findings:
            c[f.get("severity", "P2")] = c.get(f.get("severity", "P2"), 0) + 1
        return c


# ═══════════════════════════════════════════════════════
# Trend Reporter
# ═══════════════════════════════════════════════════════

class TrendReporter:
    """Generate trend analysis from scan history."""

    def analyze(self, history: list[ScanRecord], diffs: list[ScanDiff] = None) -> dict:
        if not history:
            return {"status": "no_data"}

        history.sort(key=lambda r: r.timestamp_utc)

        # Coverage trend
        coverage_trend = [{"time": r.timestamp_utc, "value": r.coverage} for r in history]

        # Bug count trend
        bug_trend = [{"time": r.timestamp_utc, "value": r.total_findings} for r in history]

        # Score trend
        score_trend = [{"time": r.timestamp_utc, "value": r.overall_score} for r in history]

        # Compute deltas
        first = history[0]; last = history[-1]
        cov_change = last.coverage - first.coverage
        score_change = last.overall_score - first.overall_score
        bug_change = last.total_findings - first.total_findings

        # Direction assessment
        direction = "improving" if cov_change > 0.05 and score_change > 5 else \
                    "declining" if cov_change < -0.05 else "stable"

        # Average per-scan metrics
        avg_bugs = sum(r.total_findings for r in history) / max(len(history), 1)
        avg_coverage = sum(r.coverage for r in history) / max(len(history), 1)

        return {
            "scans_analyzed": len(history),
            "first_scan": first.timestamp_utc, "last_scan": last.timestamp_utc,
            "direction": direction,
            "coverage_trend": f"{first.coverage:.1%} → {last.coverage:.1%} (Δ{cov_change:+.1%})",
            "score_trend": f"{first.overall_score:.0f} → {last.overall_score:.0f} (Δ{score_change:+.0f})",
            "bug_trend": f"{first.total_findings} → {last.total_findings} (Δ{bug_change:+d})",
            "series": {"coverage": coverage_trend, "bugs": bug_trend, "score": score_trend},
            "averages": {"bugs_per_scan": round(avg_bugs, 1), "coverage": round(avg_coverage, 3)},
        }

    def generate_ci_gate_report(self, scan_diff: ScanDiff) -> dict:
        """Generate CI gate pass/fail decision."""
        passed = True
        alerts = []

        if scan_diff.new_bugs > 0:
            alerts.append(f"{scan_diff.new_bugs} new bugs detected")
        if scan_diff.regressions > 0:
            passed = False
            alerts.append(f"REGRESSION: {scan_diff.regressions} regressions detected")
        if scan_diff.coverage_delta < -0.05:
            passed = False
            alerts.append(f"Coverage dropped by {abs(scan_diff.coverage_delta):.1%}")

        return {
            "passed": passed,
            "alerts": alerts,
            "recommendation": "Deploy approved" if passed else "BLOCKED — fix regressions before deploy",
            "diff": {
                "new_bugs": scan_diff.new_bugs, "fixed_bugs": scan_diff.fixed_bugs,
                "coverage_delta": round(scan_diff.coverage_delta, 3),
                "score_delta": round(scan_diff.score_delta, 1),
            }
        }


# ═══════════════════════════════════════════════════════
# CI Pipeline Integration
# ═══════════════════════════════════════════════════════

def ci_scan_and_evaluate(
    project: str, prd: str, api_doc: str, base_url: str = "", root: Path = None,
    db_path: Path = None,
) -> dict:
    """Run V12 scan, evaluate, save history, diff with previous, return CI gate decision."""
    from .v12_pipeline import run_v12_pipeline
    from .evaluation_engine import EvaluationEngine

    root = root or Path(".")
    db_path = db_path or root / "ci_scan_history.db"

    # Run one replay authority with immutable input and target identities.
    from .policy_registry import get_policy_registry

    source_hash = hashlib.sha256(str(api_doc or "").encode("utf-8")).hexdigest()
    run_id = "RUN_CI_" + hashlib.sha256(
        f"{project}|{source_hash}|{time.time_ns()}".encode("utf-8")
    ).hexdigest()[:24]
    active_policy = get_policy_registry().get_active()
    policy_version = str(
        getattr(active_policy, "policy_version", "")
        or getattr(active_policy, "policy_id", "")
        or ""
    ).strip()
    if not policy_version:
        raise RuntimeError("continuous_evaluation_policy_version_missing")
    v12 = run_v12_pipeline(
        project,
        root,
        prd,
        api_doc,
        base_url=base_url,
        campaign_context={
            "mainline_authority": "experiment_candidate",
            "run_id": run_id,
            "target_id": f"ci-target:{project}",
            "environment_id": f"ci-environment:{project}",
            "environment_ref": f"ci-environment:{project}",
            "scope_id": f"ci-scope:{project}",
            "policy_version": policy_version,
            "evaluation_mode": "replay",
            "execution_mode": "safe_read_only",
            "source_manifest": {
                "source_id": f"ci-input:{project}",
                "source_hash": source_hash,
                "source_origin": "continuous_evaluation_input",
            },
        },
    )
    from .discovery_evaluator_projection import build_evaluator_only_projection

    evaluator_projection = build_evaluator_only_projection(v12)
    evaluation_view = {
        **v12,
        "findings": list(evaluator_projection["findings"]),
        "candidate_findings": list(evaluator_projection["candidates"]),
        "evaluator_projection": evaluator_projection,
    }
    result = EvaluationEngine().evaluate(evaluation_view)

    # Save history
    history_db = ScanHistoryDB(db_path)
    scan_id = history_db.save(result, v12, project)

    # Diff with previous
    history = history_db.get_history(project, limit=2)
    diff = None
    ci_gate = {"passed": True, "alerts": []}

    if len(history) >= 2:
        diff_engine = DiffEngine()
        diff = diff_engine.diff(history[1], history[0])
        ci_gate = TrendReporter().generate_ci_gate_report(diff)

    return {
        "scan_id": scan_id,
        "grade": getattr(result, "system_grade", "?"),
        "score": getattr(result, "overall_score", 0),
        "findings": len(v12.get("findings", [])),
        "ci_gate": ci_gate,
        "diff": diff.__dict__ if diff else None,
    }
