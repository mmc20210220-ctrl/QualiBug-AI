from __future__ import annotations

"""
Autonomous Quality Pipeline — One-Click閉環.

This is the "just works" moat. Competitors require weeks of setup, configuration,
and manual tuning. QualiBug runs autonomously:

    PRD + API docs → Industry Inference → Oracle Generation →
    Probe Execution → Bug Discovery → Fix Analysis → Evidence Report

Usage:
    python -m ai_test_asset_center.autonomous_pipeline --project=my_project

Or programmatically:
    from ai_test_asset_center.autonomous_pipeline import run_autonomous_pipeline
    result = run_autonomous_pipeline("my_project")
"""

import json
import re
import time
from pathlib import Path
from typing import Any

from .bug_knowledge_graph import EnterprisePatternLibrary
from .bug_pattern_memory import BugPatternMemory
from .confirmed_bug_flywheel import build_confirmed_bug_flywheel
from .fix_co_pilot import analyze_impact, batch_analyze_impact, heuristic_impact_assessment
from .industry_auto_inference import infer_industry
from .real_project_onboarding import ROOT, _read_text, _safe_project_id, config_paths, load_real_project_config
from .safety_boundary import safety_gate, SafetyViolationError


def run_autonomous_pipeline(
    project_id: str = "real_project_demo",
    *,
    root: Path | None = None,
    prd_text: str = "",
    api_spec_text: str = "",
    domain_hint: str = "",
    force_analysis: bool = False,
) -> dict[str, Any]:
    """Run the complete autonomous quality pipeline.

    SAFETY: Only operates on test/staging environments. Production is blocked.
    """
    root = root or ROOT
    project = _safe_project_id(project_id)
    t0 = time.time()

    # Resolve project context
    cfg = load_real_project_config(project, root)
    paths = config_paths(project, root)
    prd = prd_text or (lambda p: _read_text(Path(p)) if p else "")(paths.get("prd"))
    api_doc = api_spec_text or (lambda p: _read_text(Path(p)) if p else "")(paths.get("openapi"))
    # Fallback: if no API spec file configured, load from knowledge center sources
    if not api_doc:
        try:
            from .enterprise_knowledge_center import _load_registry, _paths as _kc_paths
            reg = _load_registry(project_id, root)
            kc_paths = _kc_paths(project_id, root)
            for src in reg.get("sources", []):
                if src.get("status") != "active":
                    continue
                kc_paths = _kc_paths(project_id, root)
                sp = kc_paths["source_dir"] / f"{src['source_id']}_v{src.get('version',1)}_{src.get('original_name','file')}"
                if not sp.exists():
                    continue
                text = sp.read_text(encoding="utf-8", errors="replace")
                source_type = str(src.get("source_type", "")).lower()
                name = str(src.get("original_name", "")).lower()
                looks_openapi = (
                    name.endswith((".json", ".yaml", ".yml"))
                    and ("openapi" in text[:400].lower() or "swagger" in text[:400].lower() or '"paths"' in text[:1000])
                ) or (source_type == "openapi" and not name.endswith((".md", ".txt")))
                if looks_openapi:
                    api_doc = text
                    break
        except Exception:
            pass
    if not prd:
        try:
            from .enterprise_knowledge_center import _load_registry, _paths as _kc_paths
            reg = _load_registry(project_id, root)
            kc_paths = _kc_paths(project_id, root)
            for src in reg.get("sources", []):
                if src.get("status") != "active":
                    continue
                sp = kc_paths["source_dir"] / f"{src['source_id']}_v{src.get('version',1)}_{src.get('original_name','file')}"
                if not sp.exists():
                    continue
                text = sp.read_text(encoding="utf-8", errors="replace")
                source_type = str(src.get("source_type", "")).lower()
                name = str(src.get("original_name", "")).lower()
                looks_prd = name.endswith((".md", ".txt")) or source_type in {"prd", "mrd"}
                if looks_prd:
                    prd = text
                    break
        except Exception:
            pass
    industry_name = str(cfg.get("industry") or domain_hint or "")

    # ================================================================
    # SAFETY GATE — hard block before anything runs
    # ================================================================
    env = str(cfg.get("environment", "")).strip().lower()
    base_url = str(cfg.get("base_url", ""))
    # If base_url not in runtime config, try environment config
    if not base_url:
        try:
            from .enterprise_testops_control_plane import load_environment_config, _environment_by_name
            env_cfg = load_environment_config(project_id, root)
            target = _environment_by_name(env_cfg, env or env_cfg.get("target_environment", "test"))
            base_url = str(target.get("base_url", ""))
        except Exception:
            pass
    gate = safety_gate(project, env, base_url)
    if not gate.validate()["safe_to_proceed"] and not force_analysis:
        return {
            "pipeline": "phase61_autonomous_pipeline_v1",
            "project_id": project,
            "status": "blocked_by_safety_gate",
            "safety": gate.validate(),
        }
    analysis_only = force_analysis or not gate.validate()["safe_to_proceed"]

    report: dict[str, Any] = {
        "pipeline": "phase61_autonomous_pipeline_v1",
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "started_at_utc": _now(),
    }

    # ================================================================
    # STAGE 1: Industry Inference
    # ================================================================
    stage_start = time.time()
    industry_result = infer_industry(prd, api_doc, domain_hint=industry_name)
    report["stage1_industry"] = {
        "source": industry_result["source"],
        "primary_industry": industry_result["primary_industry"],
        "confidence": industry_result["confidence"],
        "object_count": industry_result["object_count"],
        "risk_count": industry_result["risk_count"],
        "invariant_count": industry_result["invariant_count"],
        "state_machine_count": industry_result["state_machine_count"],
        "duration_seconds": round(time.time() - stage_start, 1),
    }

    # Extract recommended oracles
    recommended_oracles = industry_result.get("recommended_oracles", [])

    # ================================================================
    # STAGE 2: Bug Discovery (heuristic engines)
    # ================================================================
    stage_start = time.time()
    all_findings: list[dict[str, Any]] = []

    # Run causality conservation
    try:
        from .business_causality_conservation import run_business_causality_conservation
        causality_result = run_business_causality_conservation(project, root)
        all_findings.extend(causality_result.get("findings", []))
    except Exception:
        pass

    # Run counterexample discovery
    try:
        from .counterexample_discovery import run_counterexample_discovery
        cex_result = run_counterexample_discovery(project, root)
        all_findings.extend(cex_result.get("counterexample_findings", []))
    except Exception:
        pass

    # Run high-signal PRD/OpenAPI defect mining. This is safe by design: it
    # does not execute mutating requests, but it turns contract and requirement
    # gaps into explainable bug candidates.
    deep_mining_summary: dict[str, Any] = {"status": "not_run"}
    try:
        from .deep_bug_mining import run_deep_bug_mining

        deep_result = run_deep_bug_mining(project, root, prd_text=prd, api_spec_text=api_doc)
        deep_findings = deep_result.get("findings", [])
        all_findings.extend(deep_findings)
        deep_mining_summary = {
            "status": "completed",
            "finding_count": len(deep_findings),
            "p0p1_count": (deep_result.get("summary") or {}).get("p0p1_count", 0),
            "static_verified_count": (deep_result.get("summary") or {}).get("static_verified_count", 0),
            "live_validation_required_count": (deep_result.get("summary") or {}).get("live_validation_required_count", 0),
            "auto_validatable_count": (deep_result.get("summary") or {}).get("auto_validatable_count", 0),
            "avg_rank_score": (deep_result.get("summary") or {}).get("avg_rank_score", 0),
            "risk_distribution": (deep_result.get("summary") or {}).get("risk_distribution", {}),
            "verification_distribution": (deep_result.get("summary") or {}).get("verification_distribution", {}),
        }
    except Exception as exc:
        deep_mining_summary = {"status": "failed", "error": str(exc)[:200]}

    validation_queue: dict[str, Any] = {"status": "not_run"}
    validation_execution: dict[str, Any] = {"status": "not_run"}
    try:
        from .bug_validation_queue import apply_validation_results_to_findings, build_bug_validation_queue, execute_bug_validation_queue

        queue = build_bug_validation_queue(project, root, all_findings, base_url_override=base_url)
        execution = execute_bug_validation_queue(project, root, queue)
        all_findings = apply_validation_results_to_findings(all_findings, queue, execution)
        validation_queue = {
            "status": "completed",
            "artifact": str(root / "platform_outputs" / project / "bug_validation_queue" / "bug_validation_queue.json"),
            "summary": queue.get("summary") or {},
            "governance": queue.get("governance") or {},
        }
        validation_execution = {
            "status": "completed",
            "artifact": str(root / "platform_outputs" / project / "bug_validation_queue" / "bug_validation_execution.json"),
            "summary": execution.get("summary") or {},
            "governance": execution.get("governance") or {},
        }
    except Exception as exc:
        validation_queue = {"status": "failed", "error": str(exc)[:200]}
        validation_execution = {"status": "failed", "error": str(exc)[:200]}

    # LLM suggestions are strictly separate from findings. They can expand the
    # future Oracle search space, but cannot affect counts, severities, pattern
    # learning, validation queues, or release gating without deterministic replay.
    llm_oracle_hypotheses: list[dict[str, Any]] = []
    try:
        from .llm_reasoning import compile_oracle_hypotheses

        known_paths = _openapi_paths(api_doc)
        llm_oracle_hypotheses = compile_oracle_hypotheses(
            prd_text=prd,
            api_schema=api_doc,
            heuristic_findings=all_findings,
            known_paths=known_paths,
        )
    except Exception:
        # LLM reasoning remains best-effort and must never obscure deterministic
        # discovery or cause an autonomous scan to fail.
        llm_oracle_hypotheses = []

    report["stage2_discovery"] = {
        "total_findings": len(all_findings),
        "findings": all_findings,  # Store full findings for detail view
        "by_severity": _count_by(all_findings, "severity"),
        "deep_bug_mining": deep_mining_summary,
        "validation_queue": validation_queue,
        "validation_execution": validation_execution,
        "llm_oracle_hypotheses": llm_oracle_hypotheses,
        "llm_oracle_governance": {
            "count": len(llm_oracle_hypotheses),
            "status": "unverified_hypothesis_only",
            "does_not_affect_finding_counts": True,
            "requires_deterministic_replay": True,
        },
        "duration_seconds": round(time.time() - stage_start, 1),
    }

    # ================================================================
    # STAGE 3: Impact Analysis → Runtime Verification → DB Verification
    # ================================================================
    stage_start = time.time()
    impact_analyses: list[dict[str, Any]] = []
    verification_results: dict[str, Any] = {"status": "not_run"}
    db_verification_results: dict[str, Any] = {"status": "not_run"}

    # --- Runtime Verification (Phase61+ moat: API probes) ---
    try:
        from .runtime_verifier import MESRuntimeVerifier
        runtime_v = MESRuntimeVerifier(base_url=base_url or "http://127.0.0.1:8000/api")
        runtime_v.run_all()
        verification_results = {
            "status": "completed",
            "summary": runtime_v.summary(),
            "findings": [{"oracle_id": r.oracle_id, "verdict": r.verdict, "expected": r.expected, "actual": r.actual} for r in runtime_v.results],
        }
        # Merge confirmed runtime bugs into findings
        for r in runtime_v.results:
            if r.verdict == "confirmed":
                all_findings.append({
                    "severity": "P0",
                    "title": f"[Runtime Verified] {r.oracle_id}: {r.expected}",
                    "category": "runtime_verified",
                    "risk_type": "runtime_probe",
                    "description": r.actual,
                    "confidence_score": r.confidence,
                    "source": "runtime_verifier",
                })
    except Exception as exc:
        verification_results = {"status": "failed", "error": str(exc)[:200]}

    # --- DB Verification (Phase61+ moat: SQL probes) ---
    try:
        from .db_verifier import MESDBVerifier
        db_v = MESDBVerifier()
        db_v.run_all()
        db_verification_results = {
            "status": "completed",
            "summary": db_v.summary(),
            "findings": [{"oracle_id": r.oracle_id, "verdict": r.verdict, "description": r.description, "evidence": r.evidence} for r in db_v.results],
        }
        for r in db_v.results:
            if r.verdict == "confirmed":
                all_findings.append({
                    "severity": "P1",
                    "title": f"[DB Verified] {r.oracle_id}: {r.description}",
                    "category": "db_verified",
                    "risk_type": "db_probe",
                    "description": r.evidence,
                    "confidence_score": 0.90,
                    "source": "db_verifier",
                })
    except Exception as exc:
        db_verification_results = {"status": "failed", "error": str(exc)[:200]}

    # For P0/P1 findings, try LLM-powered impact analysis
    critical = [f for f in all_findings if str(f.get("severity", "")) in ("P0", "P1")]
    if critical:
        llm_impacts = batch_analyze_impact(critical, prd[:4000], max_findings=5)
        impact_analyses.extend(llm_impacts)

    # For remaining findings, use heuristic impact templates
    for finding in all_findings:
        if not any(ia.get("bug_title") == finding.get("title") for ia in impact_analyses):
            impact_analyses.append(heuristic_impact_assessment(finding))

    report["stage3_impact_analysis"] = {
        "total_analyses": len(impact_analyses),
        "analyses": impact_analyses,
        "llm_powered": sum(1 for ia in impact_analyses if ia.get("source") == "llm_evidence_impact"),
        "heuristic": sum(1 for ia in impact_analyses if ia.get("source") == "heuristic_template"),
        "duration_seconds": round(time.time() - stage_start, 1),
    }
    report["stage3_runtime_verification"] = verification_results
    report["stage3_db_verification"] = db_verification_results

    # ================================================================
    # STAGE 4: Enterprise Pattern Library
    # ================================================================
    stage_start = time.time()

    library = EnterprisePatternLibrary()
    learn_result = library.learn_from_project(all_findings, project, industry_result["primary_industry"])

    # Cross-project insights within this enterprise
    cross_insights = library.cross_project_insights()

    report["stage4_pattern_library"] = {
        "library_stats": library.stats(),
        "learn_result": learn_result,
        "cross_project_insights": cross_insights[:10],
        "duration_seconds": round(time.time() - stage_start, 1),
    }

    # ================================================================
    # STAGE 5: Flywheel Update
    # ================================================================
    stage_start = time.time()
    try:
        flywheel = build_confirmed_bug_flywheel(project, root)
        pattern_memory = flywheel.get("pattern_memory", {})
    except Exception:
        pattern_memory = {"status": "unavailable"}

    report["stage5_flywheel"] = {
        "pattern_memory": pattern_memory,
        "duration_seconds": round(time.time() - stage_start, 1),
    }

    # ================================================================
    # FINAL: Executive Summary
    # ================================================================
    total_time = round(time.time() - t0, 1)
    report["executive_summary"] = {
        "total_duration_seconds": total_time,
        "industry": industry_result["primary_industry"],
        "total_bugs_found": len(all_findings),
        "critical_bugs": sum(1 for f in all_findings if str(f.get("severity", "")) == "P0"),
        "high_priority_bugs": sum(1 for f in all_findings if str(f.get("severity", "")) == "P1"),
        "impact_analyses": len(impact_analyses),
        "llm_powered_analyses": sum(1 for ia in impact_analyses if ia.get("source") == "llm_evidence_impact"),
        "unverified_llm_oracle_hypotheses": len(llm_oracle_hypotheses),
        "pattern_library_patterns": library.stats()["total_patterns"],
        "pre_seeded_patterns": library.stats()["pre_seeded"],
        "cross_project_insights": len(cross_insights),
        "recommended_oracles": recommended_oracles[:10],
    }

    report["pipeline_completed_at_utc"] = _now()

    return report


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def render_pipeline_report(report: dict[str, Any]) -> str:
    """Render the autonomous pipeline report as HTML."""
    summary = report.get("executive_summary", {})
    s1 = report.get("stage1_industry", {})
    s2 = report.get("stage2_discovery", {})
    s3 = report.get("stage3_impact_analysis", {})
    s4 = report.get("stage4_pattern_library", {})
    lib_stats = s4.get("library_stats", {})

    html = f"""<!doctype html><html lang='zh-CN'>
<meta charset='utf-8'><title>QualiBug Autonomous Pipeline — {report.get('project_name', '')}</title>
<style>
body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#07111d;color:#eaf2ff;margin:0;padding:28px}}
.hero{{background:linear-gradient(135deg,#0a1628,#132638);border:1px solid #2b4260;border-radius:16px;padding:24px;margin-bottom:20px}}
.hero h1{{margin:0 0 8px;font-size:24px}}.hero .badge{{display:inline-block;background:#174e52;color:#b6fff4;border-radius:999px;padding:4px 12px;font-size:12px;margin-right:8px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:20px}}
.card{{background:#101d2c;border:1px solid #2b4260;border-radius:12px;padding:14px}}
.card b{{display:block;font-size:24px;margin:4px 0;color:#5eead4}}
.card span{{color:#9dc4ee;font-size:12px}}
.panel{{background:#101d2c;border:1px solid #2b4260;border-radius:14px;padding:18px;margin-bottom:16px}}
.panel h2{{margin:0 0 12px;font-size:16px;color:#9dc4ee}}
.stage{{display:flex;align-items:center;gap:8px;margin:8px 0;font-size:13px}}
.stage-num{{background:#174e52;color:#b6fff4;border-radius:50%;width:24px;height:24px;display:flex;align-items:center;justify-content:center;font-size:12px;flex-shrink:0}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{padding:8px;border-bottom:1px solid #1e3349;text-align:left}}
th{{color:#9dc4ee;font-weight:600}}
.p0{{color:#f87171}}.p1{{color:#fb923c}}.p2{{color:#fbbf24}}.p3{{color:#9dc4ee}}
</style>
<body>
<div class='hero'>
  <span class='badge'>Phase61 Autonomous Pipeline</span>
  <span class='badge'>{s1.get('primary_industry', 'Unknown')}</span>
  <h1>{report.get('project_name', 'Enterprise Project')}</h1>
  <p>PRD → Industry → Discovery → Impact → Pattern Library · Total: {summary.get('total_duration_seconds', 0)}s</p>
</div>

<div class='grid'>
  <div class='card'><span>🐛 Bugs Found</span><b>{summary.get('total_bugs_found', 0)}</b></div>
  <div class='card'><span>🔴 Critical (P0)</span><b class='p0'>{summary.get('critical_bugs', 0)}</b></div>
  <div class='card'><span>🟠 High (P1)</span><b class='p1'>{summary.get('high_priority_bugs', 0)}</b></div>
  <div class='card'><span>📊 Impact Analyses</span><b>{summary.get('impact_analyses', 0)}</b></div>
  <div class='card'><span>🤖 LLM Analyses</span><b>{summary.get('llm_powered_analyses', 0)}</b></div>
  <div class='card'><span>📚 Pattern Library</span><b>{summary.get('pattern_library_patterns', 0)} patterns</b></div>
</div>

<div class='panel'>
  <h2>Pipeline Stages</h2>
  <div class='stage'><span class='stage-num'>1</span> Industry Inference: <b>{s1.get('primary_industry', '?')}</b> ({s1.get('source', '?')}, {s1.get('duration_seconds', 0)}s) — {s1.get('object_count', 0)} objects, {s1.get('risk_count', 0)} risks</div>
  <div class='stage'><span class='stage-num'>2</span> Bug Discovery: <b>{s2.get('total_findings', 0)} findings</b> ({s2.get('duration_seconds', 0)}s) — {s2.get('by_severity', {})}</div>
  <div class='stage'><span class='stage-num'>3</span> Impact Analysis: <b>{s3.get('total_analyses', 0)} assessments</b> ({s3.get('llm_powered', 0)} LLM, {s3.get('heuristic', 0)} template) — {s3.get('duration_seconds', 0)}s</div>
  <div class='stage'><span class='stage-num'>4</span> Pattern Library: <b>{lib_stats.get('total_patterns', 0)} total</b> ({lib_stats.get('pre_seeded', 0)} pre-seeded, {lib_stats.get('learned', 0)} learned) · {lib_stats.get('projects_contributed', 0)} projects — {s4.get('duration_seconds', 0)}s</div>
  <div class='stage'><span class='stage-num'>5</span> Flywheel Updated: {report.get('stage5_flywheel', {}).get('duration_seconds', 0)}s</div>
</div>

<div class='panel'>
  <h2>Recommended Oracles</h2>
  <p>{', '.join(str(o) for o in summary.get('recommended_oracles', [])[:15]) or 'Run with LLM for Oracle recommendations'}</p>
</div>
"""
    # Cross-project insights
    insights = s4.get("cross_project_insights", [])
    if insights:
        html += "<div class='panel'><h2>Cross-Project Insights (Enterprise Internal)</h2><table><tr><th>Pattern</th><th>Projects Affected</th></tr>"
        for ins in insights:
            html += f"<tr><td>{ins.get('pattern', '')[:80]}</td><td>{ins.get('projects_affected', 0)}</td></tr>"
        html += "</table></div>"

    html += "</body></html>"
    return html


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    from collections import Counter
    return dict(Counter(str(item.get(key, "unknown")) for item in items))


def _openapi_paths(api_text: str) -> set[str]:
    """Extract only declared path keys; never trust model-suggested endpoints."""
    try:
        parsed = json.loads(api_text)
        paths = parsed.get("paths") if isinstance(parsed, dict) else {}
        if isinstance(paths, dict):
            return {str(path) for path in paths if str(path).startswith("/")}
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    # Minimal YAML fallback: path keys in a conventional OpenAPI `paths:` block.
    return {
        match.group(1)
        for match in re.finditer(r"(?m)^\s{2,}(/[^\s:]+)\s*:\s*$", api_text or "")
    }


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
