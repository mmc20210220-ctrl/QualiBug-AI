#!/usr/bin/env python
"""
Phase79→Phase78 Bridge: Project Onboarding Pipeline

Takes PRD + OpenAPI → ProjectContext → Observers/Bindings/Flows → Semantic Verifier.

Usage:
    python run_onboarding_pipeline.py [project_id]
"""

import sys, json, time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))


def run(project_id: str = "real_project_demo") -> dict:
    """Full onboarding pipeline: docs → context → verify."""
    from ai_test_asset_center.real_project_onboarding import (
        config_paths, load_real_project_config, _safe_project_id,
    )
    from ai_test_asset_center.project_context_compiler import ProjectContextCompiler
    from ai_test_asset_center.api_capability_mapper import APICapabilityMapper
    from ai_test_asset_center.onboarding_modules import (
        ObserverCandidateBuilder, BindingCandidateBuilder,
        FixtureReadinessAnalyzer, VerificationCoverageAnalyzer,
        OnboardingGapReporter,
    )

    project = _safe_project_id(project_id)
    paths = config_paths(project, PROJECT_ROOT)
    cfg = load_real_project_config(project, PROJECT_ROOT)

    # ── Step 1: Load documents ──
    prd_path = paths["input_dir"] / "prd.md"
    prd_text = prd_path.read_text(encoding="utf-8", errors="replace") if prd_path.exists() else ""
    
    openapi_path = paths["input_dir"] / "openapi.json"
    openapi_spec = {}
    if openapi_path.exists():
        try:
            openapi_spec = json.loads(openapi_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    api_docs = ""
    for doc_path in sorted(paths["input_dir"].glob("*.md")):
        if doc_path.name.lower() not in ("prd.md", "readme.md"):
            api_docs += doc_path.read_text(encoding="utf-8", errors="replace")[:5000]

    # ── Step 2: Compile project context ──
    compiler = ProjectContextCompiler()
    ctx = compiler.compile(prd_text, openapi_spec, api_docs)
    ctx.project_id = project

    # ── Step 3: Build API capability map ──
    mapper = APICapabilityMapper()
    apis = mapper.map_from_openapi(openapi_spec) if openapi_spec else []
    ctx.apis = apis

    # ── Step 4: Build observers ──
    obs_builder = ObserverCandidateBuilder()
    base_url = cfg.get("base_url", "")
    observers = obs_builder.build(apis, ctx.entities, base_url)
    ctx.observers = observers

    # ── Step 5: Build bindings ──
    bind_builder = BindingCandidateBuilder()
    bindings = bind_builder.build(apis, ctx.entities)
    ctx.bindings = bindings

    # ── Step 6: Fixture readiness ──
    fixtures = {}
    fixture_path = paths["input_dir"] / "fixtures.json"
    if fixture_path.exists():
        try:
            fixtures = json.loads(fixture_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    test_accounts_path = paths["input_dir"] / "test_accounts.json"
    test_accounts = {}
    if test_accounts_path.exists():
        try:
            test_accounts = json.loads(test_accounts_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    fixture_analyzer = FixtureReadinessAnalyzer()
    readiness = fixture_analyzer.analyze(bindings, observers, apis, fixtures, test_accounts)
    ctx.fixtures = [{
        "flow_id": r.flow_id, "readiness": r.readiness,
        "missing": r.missing_requirements, "actions": r.recommended_next_actions,
    } for r in readiness]

    # ── Step 7: Coverage analysis ──
    cov_analyzer = VerificationCoverageAnalyzer()
    ctx.coverage = cov_analyzer.analyze(ctx)

    # ── Step 8: Gap report ──
    gap_reporter = OnboardingGapReporter()
    ctx.gaps = [{
        "gap_id": g.gap_id, "category": g.category, "severity": g.severity,
        "description": g.description, "action": g.recommended_action,
    } for g in gap_reporter.report(ctx)]

    # ── Step 9: Generate first executable flows ──
    candidate_flows = []
    ready_bindings = [r for r in readiness if r.readiness == "READY"]
    for r in ready_bindings[:5]:
        obs = next((o for o in observers if o.entity_alias == r.flow_id.replace("flow_", "")), None)
        if obs:
            candidate_flows.append({
                "flow_id": r.flow_id,
                "entity": obs.entity_alias,
                "observer": obs.observer_id,
                "method": obs.method,
                "path": obs.path,
            })

    # ── Step 10: Save context ──
    output_dir = paths["output_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "project_context.json"
    ctx_dict = compiler.to_dict(ctx)
    ctx_dict["candidate_flows"] = candidate_flows
    ctx_dict["compiled_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out_path.write_text(json.dumps(ctx_dict, indent=2, ensure_ascii=False, default=str),
                        encoding="utf-8")

    return ctx_dict


if __name__ == "__main__":
    project = sys.argv[1] if len(sys.argv) > 1 else "real_project_demo"
    result = run(project)
    print(f"\n=== Pipeline Complete ===")
    print(f"Entities:   {len(result.get('entities', []))}")
    print(f"APIs:       {len(result.get('apis', []))}")
    print(f"Observers:  {len(result.get('observers', []))}")
    print(f"Bindings:   {len(result.get('bindings', []))}")
    print(f"Flows:      {len(result.get('candidate_flows', []))}")
    print(f"Gaps:       {len(result.get('gaps', []))}")
    print(f"Coverage:   {json.dumps(result.get('coverage', {}), indent=2, default=str)[:200]}")
    print(f"Output:     project_context.json")
