"""
V12.5 Evaluation System — QualiBug Intelligence Metrics.

Six modules:
  coverage_analyzer     — state/scenario/oracle coverage ratios
  bug_scoring_engine    — Bug Intelligence Score (0-10)
  oracle_effectiveness  — oracle hit rates & contribution
  scenario_efficiency   — value density & cost efficiency
  evaluation_engine     — core aggregation engine
  intelligence_reporter — business-grade report (A+/A/B/C)
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ═══════════════════════════════════════════════════════
# 1. Coverage Analyzer
# ═══════════════════════════════════════════════════════

@dataclass
class CoverageReport:
    state_coverage: float = 0.0      # visited_states / total_states
    scenario_coverage: float = 0.0   # executed_paths / total_paths
    oracle_coverage: float = 0.0     # triggered_oracles / total_oracles
    entity_coverage: float = 0.0     # entities_with_bugs / total_entities
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "state_coverage": round(self.state_coverage, 3),
            "scenario_coverage": round(self.scenario_coverage, 3),
            "oracle_coverage": round(self.oracle_coverage, 3),
            "entity_coverage": round(self.entity_coverage, 3),
            "overall_coverage": round(
                (self.state_coverage + self.scenario_coverage + self.oracle_coverage + self.entity_coverage) / 4, 3),
            "details": self.details,
        }


class CoverageAnalyzer:
    """Compute coverage metrics from V12 pipeline output."""

    def analyze(self, v12_result: dict, state_graphs: dict | None = None) -> CoverageReport:
        phases = v12_result.get("phases", {})
        findings = v12_result.get("findings", [])

        # State coverage from graph stats
        sg = phases.get("state_graph", {})
        sg_summary = sg.get("summary", {})
        total_states = sum(s.get("total_states", 0) for s in sg_summary.values())
        total_transitions = sum(s.get("total_transitions", 0) for s in sg_summary.values())

        # Scenario coverage
        sc = phases.get("scenario_generation", {})
        total_scenarios = sc.get("total_scenarios", 1)
        forbidden = sc.get("forbidden_paths", 0)

        # Execution coverage
        ex = phases.get("execution", {})
        executed = ex.get("executed", 0)

        # Oracle coverage
        oracle_phase = phases.get("oracle", {})
        oracles_triggered = len(set(
            f.get("oracle", {}).get("oracle_name", "")
            for f in findings if f.get("oracle")
        ))
        total_oracles = 26  # All registered oracles

        # Entity coverage: how many entities have at least one bug
        entities_with_bugs = len(set(
            f.get("title", "").split(":")[0].split(" ")[0]
            for f in findings if f.get("confidence_score", 0) >= 0.8
        ))
        total_entities = len(sg_summary) or 1

        return CoverageReport(
            state_coverage=min(executed / max(total_transitions, 1), 1.0),
            scenario_coverage=min(executed / max(total_scenarios, 1), 1.0),
            oracle_coverage=min(oracles_triggered / max(total_oracles, 1), 1.0),
            entity_coverage=min(entities_with_bugs / max(total_entities, 1), 1.0),
            details={
                "total_states": total_states, "total_transitions": total_transitions,
                "total_scenarios": total_scenarios, "forbidden_scenarios": forbidden,
                "executed_scenarios": executed, "oracles_triggered": oracles_triggered,
                "entities_with_bugs": entities_with_bugs, "total_entities": total_entities,
            }
        )


# ═══════════════════════════════════════════════════════
# 2. Bug Scoring Engine
# ═══════════════════════════════════════════════════════

@dataclass
class BugScore:
    bug_id: str
    score: float = 0.0           # 0-10
    severity: str = "P1"
    impact_score: float = 0.0    # 0-5
    reproducibility: float = 0.0 # 0-1
    cross_module: float = 0.0    # 0-2
    oracle_confidence: float = 0.0
    description: str = ""

    @property
    def grade(self) -> str:
        if self.score >= 8: return "A+"
        if self.score >= 6: return "A"
        if self.score >= 4: return "B"
        if self.score >= 2: return "C"
        return "D"

    def to_dict(self) -> dict:
        return {"bug_id": self.bug_id, "score": round(self.score, 1),
                "grade": self.grade, "severity": self.severity,
                "impact": self.impact_score, "reproducibility": self.reproducibility,
                "cross_module": self.cross_module, "confidence": self.oracle_confidence,
                "description": self.description[:100]}


class BugScoringEngine:
    """Score each bug on 0-10 scale: impact × reproducibility × cross_module."""

    def score_finding(self, finding: dict) -> BugScore:
        title = finding.get("title", "unknown")
        severity = finding.get("severity", "P1")
        confidence = finding.get("confidence_score", 0.5)
        oracle = finding.get("oracle", {})
        category = finding.get("category", "")

        # Impact score (0-5) based on severity + oracle type
        impact = 2.0
        if severity == "P0": impact = 4.5
        elif severity == "P1": impact = 3.0
        elif severity == "P2": impact = 1.5

        # Money/permission/concurrency bugs have higher impact
        if category in ("money", "permission", "concurrency"):
            impact = min(5.0, impact + 1.0)

        # Reproducibility (0-1) based on evidence quality
        repro = 0.5
        if oracle.get("confidence", 0) > 0.9: repro = 0.95
        elif confidence > 0.85: repro = 0.8
        elif confidence > 0.7: repro = 0.6

        # Cross-module factor (0-2)
        cross = 0.0
        if "/" in finding.get("path", ""):
            segments = finding["path"].strip("/").split("/")
            if len(segments) >= 3: cross = 1.0  # Nested path → affects multiple modules
        if "cross" in title.lower() or "tenant" in title.lower():
            cross = 1.5

        score = min(10.0, impact * 2.0 + repro * 3.0 + cross * 2.0)
        if score < 0.5: score = 0.5

        return BugScore(
            bug_id=finding.get("evidence_id", title[:30]),
            score=score, severity=severity,
            impact_score=impact, reproducibility=repro,
            cross_module=cross, oracle_confidence=confidence,
            description=title[:100],
        )

    def score_all(self, findings: list[dict]) -> list[BugScore]:
        return [self.score_finding(f) for f in findings]

    def top_bugs(self, scores: list[BugScore], n: int = 10) -> list[BugScore]:
        return sorted(scores, key=lambda s: s.score, reverse=True)[:n]

    def distribution(self, scores: list[BugScore]) -> dict:
        dist = {"A+": 0, "A": 0, "B": 0, "C": 0, "D": 0}
        for s in scores: dist[s.grade] = dist.get(s.grade, 0) + 1
        return dist


# ═══════════════════════════════════════════════════════
# 3. Oracle Effectiveness
# ═══════════════════════════════════════════════════════

@dataclass
class OracleMetric:
    oracle_name: str
    layer: str
    evaluations: int = 0
    violations: int = 0
    hit_rate: float = 0.0
    contribution: float = 0.0  # % of total violations

    def to_dict(self) -> dict:
        return {"oracle": self.oracle_name, "layer": self.layer,
                "evaluations": self.evaluations, "violations": self.violations,
                "hit_rate": round(self.hit_rate, 3),
                "contribution": round(self.contribution, 3)}


class OracleEffectiveness:
    """Compute per-oracle hit rates and contribution."""

    def analyze(self, oracle_results: list[dict], v12_findings: list[dict]) -> list[OracleMetric]:
        by_oracle = defaultdict(lambda: {"evaluations": 0, "violations": 0, "layer": ""})

        for r in oracle_results:
            name = r.get("oracle_name", r.get("oracle", "unknown"))
            layer = r.get("layer", "?")
            by_oracle[name]["evaluations"] += 1
            by_oracle[name]["layer"] = layer or by_oracle[name]["layer"]
            if not r.get("passed", True):
                by_oracle[name]["violations"] += 1

        # Also count from findings
        for f in v12_findings:
            o = f.get("oracle", {})
            name = o.get("oracle_name", o.get("oracle", ""))
            if name and name not in by_oracle:
                by_oracle[name]["evaluations"] += 1
                by_oracle[name]["violations"] += 1

        total_violations = sum(v["violations"] for v in by_oracle.values()) or 1
        metrics = []
        for name, data in by_oracle.items():
            hit_rate = data["violations"] / max(data["evaluations"], 1)
            metrics.append(OracleMetric(
                oracle_name=name, layer=data["layer"],
                evaluations=data["evaluations"], violations=data["violations"],
                hit_rate=hit_rate, contribution=data["violations"] / total_violations,
            ))
        return sorted(metrics, key=lambda m: m.hit_rate, reverse=True)

    def redundant_oracles(self, metrics: list[OracleMetric]) -> list[str]:
        """Identify oracles with zero violations — candidates for optimization."""
        return [m.oracle_name for m in metrics if m.violations == 0 and m.evaluations > 10]


# ═══════════════════════════════════════════════════════
# 4. Scenario Efficiency
# ═══════════════════════════════════════════════════════

@dataclass
class ScenarioEfficiency:
    total_scenarios: int = 0
    executed: int = 0
    bugs_found: int = 0
    total_cost_ms: int = 0
    bugs_per_scenario: float = 0.0
    cost_per_bug_ms: float = 0.0
    value_density: float = 0.0  # bugs / executed
    efficiency_grade: str = "C"

    def to_dict(self) -> dict:
        return {
            "total_scenarios": self.total_scenarios, "executed": self.executed,
            "bugs_found": self.bugs_found, "total_cost_ms": self.total_cost_ms,
            "bugs_per_scenario": round(self.bugs_per_scenario, 3),
            "cost_per_bug_ms": round(self.cost_per_bug_ms, 1),
            "value_density": round(self.value_density, 3),
            "efficiency_grade": self.efficiency_grade,
        }

    def compute(self):
        if self.executed > 0:
            self.bugs_per_scenario = self.bugs_found / max(self.executed, 1)
            self.value_density = self.bugs_found / max(self.executed, 1)
        if self.bugs_found > 0:
            self.cost_per_bug_ms = self.total_cost_ms / max(self.bugs_found, 1)
        if self.value_density >= 0.5: self.efficiency_grade = "A+"
        elif self.value_density >= 0.3: self.efficiency_grade = "A"
        elif self.value_density >= 0.15: self.efficiency_grade = "B"
        else: self.efficiency_grade = "C"


class ScenarioEfficiencyAnalyzer:
    """Compute scenario value density and cost efficiency."""

    def analyze(self, v12_result: dict) -> ScenarioEfficiency:
        phases = v12_result.get("phases", {})
        findings = v12_result.get("findings", [])

        sc = phases.get("scenario_generation", {})
        total = sc.get("total_scenarios", 0)
        ex = phases.get("execution", {})
        executed = ex.get("executed", 0)
        exec_ms = ex.get("duration_ms", 0)

        high_confidence = [f for f in findings if f.get("confidence_score", 0) >= 0.8]

        eff = ScenarioEfficiency(
            total_scenarios=total, executed=executed,
            bugs_found=len(high_confidence), total_cost_ms=exec_ms,
        )
        eff.compute()
        return eff


# ═══════════════════════════════════════════════════════
# 5. Evaluation Engine
# ═══════════════════════════════════════════════════════

@dataclass
class EvaluationResult:
    coverage: CoverageReport
    bug_scores: list[BugScore]
    oracle_metrics: list[OracleMetric]
    scenario_efficiency: ScenarioEfficiency
    system_grade: str = "B"
    overall_score: float = 0.0  # 0-100
    summary: str = ""
    generated_at_utc: str = ""

    def to_dict(self) -> dict:
        return {
            "system_grade": self.system_grade,
            "overall_score": round(self.overall_score, 1),
            "coverage": self.coverage.to_dict(),
            "bug_scores": [b.to_dict() for b in self.bug_scores],
            "bug_distribution": BugScoringEngine().distribution(self.bug_scores),
            "oracle_effectiveness": [o.to_dict() for o in self.oracle_metrics[:15]],
            "scenario_efficiency": self.scenario_efficiency.to_dict(),
            "summary": self.summary,
            "generated_at_utc": self.generated_at_utc,
        }


class EvaluationEngine:
    """V12.5 core: aggregate all metrics into a single EvaluationResult."""

    def evaluate(self, v12_result: dict, state_graphs: dict | None = None) -> EvaluationResult:
        findings = v12_result.get("findings", [])
        phases = v12_result.get("phases", {})
        oracle_results = self._collect_oracle_results(findings, phases)

        # 1. Coverage
        coverage = CoverageAnalyzer().analyze(v12_result, state_graphs)

        # 2. Bug scores
        scorer = BugScoringEngine()
        bug_scores = scorer.score_all(findings)

        # 3. Oracle effectiveness
        oracle_eff = OracleEffectiveness().analyze(oracle_results, findings)

        # 4. Scenario efficiency
        sc_eff = ScenarioEfficiencyAnalyzer().analyze(v12_result)

        # 5. System grade: weighted average
        cov_score = coverage.to_dict()["overall_coverage"] * 30
        bug_score = (sum(s.score for s in bug_scores) / max(len(bug_scores), 1)) * 6 if bug_scores else 15
        oracle_score = sum(m.hit_rate for m in oracle_eff) / max(len(oracle_eff), 1) * 10
        eff_score = min(sc_eff.value_density * 20, 20) if sc_eff.value_density else 10

        overall = min(100, cov_score + bug_score + oracle_score + eff_score)
        grade = "A+" if overall >= 85 else "A" if overall >= 70 else "B" if overall >= 50 else "C"

        return EvaluationResult(
            coverage=coverage, bug_scores=bug_scores, oracle_metrics=oracle_eff,
            scenario_efficiency=sc_eff, system_grade=grade, overall_score=overall,
            summary=f"覆盖率{coverage.to_dict()['overall_coverage']:.1%}, {len(bug_scores)}Bug, {len(oracle_eff)}Oracle生效, 场景效率{sc_eff.efficiency_grade}",
            generated_at_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

    def _collect_oracle_results(self, findings: list[dict], phases: dict) -> list[dict]:
        results = []
        for f in findings:
            o = f.get("oracle", {})
            if o:
                results.append({"oracle_name": o.get("oracle_name", o.get("oracle", "?")),
                                "layer": o.get("layer", "?"),
                                "passed": o.get("passed", True)})
        return results


# ═══════════════════════════════════════════════════════
# 6. Intelligence Reporter
# ═══════════════════════════════════════════════════════

class IntelligenceReporter:
    """Generate business-grade QualiBug Intelligence Report."""

    def generate(self, evaluation: EvaluationResult) -> str:
        """Generate a Markdown intelligence report."""
        cov = evaluation.coverage.to_dict()
        dist = BugScoringEngine().distribution(evaluation.bug_scores)
        top_bugs = sorted(evaluation.bug_scores, key=lambda s: s.score, reverse=True)[:5]
        eff = evaluation.scenario_efficiency.to_dict()

        lines = [
            "# QualiBug V12.5 Intelligence Report",
            f"**System Grade: {evaluation.system_grade}** | Overall Score: {evaluation.overall_score:.0f}/100",
            f"Generated: {evaluation.generated_at_utc}",
            "",
            "## 1. Coverage Analysis",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| State Coverage | {cov['state_coverage']:.1%} |",
            f"| Scenario Coverage | {cov['scenario_coverage']:.1%} |",
            f"| Oracle Coverage | {cov['oracle_coverage']:.1%} |",
            f"| Entity Coverage | {cov['entity_coverage']:.1%} |",
            f"| **Overall** | **{cov['overall_coverage']:.1%}** |",
            "",
            "## 2. Bug Intelligence Scores",
            f"| Grade | Count |",
            f"|-------|-------|",
        ]
        for grade in ["A+", "A", "B", "C", "D"]:
            lines.append(f"| {grade} | {dist.get(grade, 0)} |")

        lines += [
            "",
            "### Top 5 Bugs",
            "| Score | Severity | Description |",
            "|-------|----------|-------------|",
        ]
        for b in top_bugs:
            lines.append(f"| {b.score:.1f} | {b.severity} | {b.description[:80]} |")

        lines += [
            "",
            "## 3. Oracle Effectiveness",
            "| Oracle | Layer | Hit Rate | Violations | Contribution |",
            "|--------|-------|----------|------------|-------------|",
        ]
        for m in evaluation.oracle_metrics[:10]:
            lines.append(f"| {m.oracle_name} | {m.layer} | {m.hit_rate:.1%} | {m.violations} | {m.contribution:.1%} |")

        lines += [
            "",
            "## 4. Scenario Efficiency",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total Scenarios | {eff['total_scenarios']} |",
            f"| Executed | {eff['executed']} |",
            f"| Bugs Found | {eff['bugs_found']} |",
            f"| Bugs/Scenario | {eff['bugs_per_scenario']:.2f} |",
            f"| Cost/Bug | {eff['cost_per_bug_ms']:.0f}ms |",
            f"| Efficiency Grade | **{eff['efficiency_grade']}** |",
            "",
            "## 5. System Assessment",
            f"**Grade {evaluation.system_grade}** — {evaluation.summary}",
            "",
            "*QualiBug V12.5 — Bug Intelligence Evaluation System*",
        ]

        return "\n".join(lines)

    def save_report(self, evaluation: EvaluationResult, output_path: Path):
        report = self.generate(evaluation)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
        evaluation_data = evaluation.to_dict()
        json_path = output_path.with_suffix(".json")
        json_path.write_text(json.dumps(evaluation_data, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(output_path)
