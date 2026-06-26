from __future__ import annotations

import argparse
import json
from pathlib import Path

from aitestops.asset_generator import AssetGenerator
from aitestops.api_asset_generator import ApiAssetGenerator
from aitestops.failure_analyzer import FailureAnalyzer
from aitestops.hybrid_ai_engine import HybridAIEngine
from aitestops.impact_analyzer import ImpactAnalyzer
from aitestops.failure_triage import FailureTriageEngine
from aitestops.llm_client import LLMConfig
from aitestops.enterprise_landing import EnterpriseLandingPackager
from aitestops.productization import ProductReadyPackager
from aitestops.release_verifier import verify_release
from aitestops.self_dogfood_audit import run_self_dogfood_audit
from ai_test_asset_center.agent_discovery_loop import build_agent_discovery_loop
from ai_test_asset_center.agent_experiment_runner import compile_agent_experiment_pack, run_agent_experiment_pack
from ai_test_asset_center.agent_business_flow_orchestrator import compile_agent_business_flow_pack, run_agent_business_flow_pack
from ai_test_asset_center.cognitive_memory_graph import CognitiveMemoryGraph, export_knowledge_vault
from ai_test_asset_center.phase91_graph_evaluation import run_phase91_context_ab


def cmd_generate(args: argparse.Namespace) -> int:
    engine = HybridAIEngine(mode=args.engine)
    summary = AssetGenerator(ai_engine=engine).generate_from_file(Path(args.requirement), Path(args.out))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_generate_openapi(args: argparse.Namespace) -> int:
    summary = ApiAssetGenerator().generate_from_openapi(Path(args.spec), Path(args.out))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0



def cmd_analyze_impact(args: argparse.Namespace) -> int:
    plan = ImpactAnalyzer().analyze(Path(args.diff), Path(args.assets), Path(args.out))
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


def cmd_triage_failure(args: argparse.Namespace) -> int:
    result = FailureTriageEngine(mode=args.engine).analyze_dir(Path(args.evidence), Path(args.out))
    print(json.dumps({
        "failure_type": result.failure_type,
        "confidence": result.confidence,
        "suspected_owner": result.suspected_owner,
        "severity": result.severity,
        "engine_used": result.engine_used,
        "fallback_reason": result.fallback_reason,
        "output_dir": args.out,
    }, ensure_ascii=False, indent=2))
    return 0

def cmd_analyze_failure(args: argparse.Namespace) -> int:
    analysis = FailureAnalyzer(mode=args.engine).analyze_file(Path(args.log), Path(args.out))
    print(json.dumps({
        "failure_type": analysis.failure_type,
        "confidence": analysis.confidence,
        "suspected_owner": analysis.suspected_owner,
        "engine_used": analysis.engine_used,
        "fallback_reason": analysis.fallback_reason,
        "output": args.out,
    }, ensure_ascii=False, indent=2))
    return 0



def cmd_enterprise_demo(args: argparse.Namespace) -> int:
    summary = EnterpriseLandingPackager().build(Path(args.out))
    print(json.dumps({
        "output_dir": summary.output_dir,
        "readiness_score": summary.readiness_score,
        "capability_count": summary.capability_count,
        "gate_count": summary.gate_count,
        "integration_count": summary.integration_count,
        "files": summary.files,
    }, ensure_ascii=False, indent=2))
    return 0



def cmd_product_demo(args: argparse.Namespace) -> int:
    summary = ProductReadyPackager().build(Path(args.out))
    print(json.dumps({
        "output_dir": summary.output_dir,
        "product_name": summary.product_name,
        "version": summary.version,
        "module_count": summary.module_count,
        "persona_count": summary.persona_count,
        "file_count": summary.file_count,
        "files": summary.files,
    }, ensure_ascii=False, indent=2))
    return 0

def cmd_doctor(args: argparse.Namespace) -> int:
    config = LLMConfig.from_env()
    info = {
        "llm_provider": config.provider,
        "llm_base_url_configured": bool(config.base_url),
        "llm_api_key_configured": bool(config.api_key),
        "llm_model_configured": bool(config.model),
        "llm_enabled": config.enabled,
        "note": "engine=auto 会在 LLM 未配置或输出未通过校验时自动使用 local 兜底。engine=llm 会强制使用真实 LLM。",
    }
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0




def cmd_discover(args: argparse.Namespace) -> int:
    """Run the autonomous bug discovery engine from the public CLI."""
    from ai_test_asset_center.discovery_engine import run_discovery

    result = run_discovery(
        prd_path=args.prd,
        api_path=args.api,
        base_url=args.base_url,
    )
    if args.out:
        out = Path(args.out).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("runtime_status") == "OK" else 1

def cmd_self_evolve(args: argparse.Namespace) -> int:
    """Run one supervised QualiBug self-evolution worker."""
    import os
    from ai_test_asset_center.autonomous_evolution_orchestrator import run_evolution_orchestrated

    os.environ["QUALIBUG_PROJECT"] = args.project
    if args.local_bootstrap_only:
        os.environ["QUALIBUG_LOCAL_BOOTSTRAP_ONLY"] = "1"
    if args.graph_mode:
        os.environ["QUALIBUG_GRAPH_CONTEXT_MODE"] = args.graph_mode

    result = run_evolution_orchestrated(project_id=args.project, max_evolution_cycles=args.max_evolution_cycles)
    if args.out:
        out = Path(args.out).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({
        "project_id": result.get("project_id"),
        "active_policy_version": result.get("active_policy_version"),
        "terminal": (result.get("discovery_result") or {}).get("terminal"),
        "execution_status": (result.get("discovery_result") or {}).get("execution_status"),
        "rounds": (result.get("discovery_result") or {}).get("rounds"),
        "total_bugs": (result.get("discovery_result") or {}).get("total_bugs"),
        "inconclusive_rate": (result.get("discovery_result") or {}).get("inconclusive_rate"),
        "signals": result.get("signals", []),
        "evolution": result.get("evolution"),
        "out": args.out,
    }, ensure_ascii=False, indent=2, default=str))
    discovery = result.get("discovery_result") or {}
    terminal = str(discovery.get("terminal") or "")
    return 1 if terminal.startswith("FAILED") else 0


def cmd_bug_engine_auto(args: argparse.Namespace) -> int:
    """Start/recover the local target and run supervised bug-engine autorun cycles."""
    from ai_test_asset_center.bug_engine_autorun import run_bug_engine_auto, start_bug_engine_daemon

    if getattr(args, "detach", False):
        result = start_bug_engine_daemon(
            project_id=args.project,
            cycles=args.cycles,
            interval_seconds=args.interval_seconds,
            local_bootstrap_only=args.local_bootstrap_only,
            bootstrap_target=not args.no_bootstrap_target,
            graph_mode=args.graph_mode,
            reset_stale_runtime=not args.no_reset_stale_runtime,
            out_dir=args.out_dir or None,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result.get("status") in {"started", "already_running"} else 1

    result = run_bug_engine_auto(
        project_id=args.project,
        cycles=args.cycles,
        interval_seconds=args.interval_seconds,
        local_bootstrap_only=args.local_bootstrap_only,
        bootstrap_target=not args.no_bootstrap_target,
        graph_mode=args.graph_mode,
        reset_stale_runtime=not args.no_reset_stale_runtime,
        out_dir=args.out_dir or None,
    )
    print(json.dumps({
        "project_id": result.get("project_id"),
        "target": result.get("target"),
        "runtime_recovery": result.get("runtime_recovery"),
        "cycles": len(result.get("cycles", [])),
        "last_cycle": result.get("last_cycle"),
        "latest_report": result.get("latest_report"),
    }, ensure_ascii=False, indent=2, default=str))
    last = result.get("last_cycle") or {}
    terminal = str(last.get("terminal") or "")
    return 1 if terminal.startswith("FAILED") else 0


def cmd_bug_engine_status(args: argparse.Namespace) -> int:
    """Print bug-engine daemon and latest report status."""
    from ai_test_asset_center.bug_engine_autorun import bug_engine_status

    result = bug_engine_status(project_id=args.project, out_dir=args.out_dir or None)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_bug_engine_input_only(args: argparse.Namespace) -> int:
    """Run QualiBug from projects/<project>/input only; never read oracle/ground-truth files."""
    from ai_test_asset_center.blind_project_runner import run_input_only_project

    result = run_input_only_project(
        project_input_dir=args.input_dir,
        project_id=args.project or None,
        root=Path(args.root).resolve() if args.root else None,
        base_url=args.base_url or "",
        execute_readonly=bool(args.execute_readonly),
        probe_config=args.probe_config or None,
        max_probes=args.max_probes,
    )
    print(json.dumps({
        "project_id": result.get("project_id"),
        "mode": result.get("mode"),
        "strict_no_peek": result.get("strict_no_peek"),
        "input_files": len((result.get("input_manifest") or {}).get("allowed_input_files") or []),
        "blocked_files": len((result.get("input_manifest") or {}).get("blocked_files") or []),
        "project_context_summary": result.get("project_context_summary"),
        "discovery_summary": result.get("discovery_summary"),
        "grounded_probe_execution_summary": result.get("grounded_probe_execution_summary"),
        "outputs": result.get("outputs"),
    }, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_bug_engine_benchmark_blind(args: argparse.Namespace) -> int:
    """Run input-only bug candidate generation over every projects/<name>/input directory."""
    from ai_test_asset_center.blind_benchmark_runner import run_blind_benchmark_suite

    result = run_blind_benchmark_suite(
        suite_root=args.suite_root,
        root=Path(args.root).resolve() if args.root else None,
        project_prefix=args.project_prefix,
        limit=args.limit,
    )
    print(json.dumps({
        "mode": result.get("mode"),
        "strict_no_peek": result.get("strict_no_peek"),
        "totals": result.get("totals"),
        "outputs": result.get("outputs"),
    }, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_bug_engine_benchmark_v3_score(args: argparse.Namespace) -> int:
    """Score blind Suite v3 outputs after generation without feeding oracle to the engine."""
    from benchmark_evaluator.suite_v3 import evaluate_suite_v3

    result = evaluate_suite_v3(
        suite_root=args.suite_root,
        outputs_root=args.outputs_root,
        glob_pattern=args.glob,
        out_dir=args.out,
    )
    print(json.dumps({
        "mode": result.get("mode"),
        "metrics": result.get("metrics"),
        "outputs": {
            "scorecard_json": str(Path(args.out).resolve() / "suite_v3_scorecard.json"),
            "scorecard_md": str(Path(args.out).resolve() / "suite_v3_scorecard.md"),
        },
    }, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_bug_engine_grounded_execute(args: argparse.Namespace) -> int:
    """Execute document-grounded probe plans with strict safety gates."""
    from ai_test_asset_center.grounded_probe_executor import run_grounded_probe_executor

    result = run_grounded_probe_executor(
        probe_plan_path=args.probe_plan,
        out_dir=args.out_dir,
        base_url=args.base_url or "",
        probe_config=args.probe_config or None,
        execute_readonly=bool(args.execute_readonly),
        allow_write_sandbox=bool(args.allow_write_sandbox),
        approval_id=args.approval_id or "",
        max_probes=args.max_probes,
        timeout_seconds=args.timeout_seconds,
        input_dir=getattr(args, "input_dir", "") or None,
    )
    print(json.dumps({
        "engine": result.get("engine"),
        "strict_no_peek": result.get("strict_no_peek"),
        "project_id": result.get("project_id"),
        "summary": result.get("summary"),
        "outputs": result.get("outputs"),
    }, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_bug_engine_probe_config_template(args: argparse.Namespace) -> int:
    """Generate a non-executable probe_config.template.json for grounded probes."""
    from ai_test_asset_center.probe_config_template_builder import build_probe_config_template

    result = build_probe_config_template(
        probe_plan_path=args.probe_plan,
        out_dir=args.out_dir,
        input_dir=args.input_dir or None,
        base_url_hint=args.base_url_hint or "",
        approval_id_hint=args.approval_id_hint or "",
        max_probes=args.max_probes,
    )
    print(json.dumps({
        "engine": result.get("engine"),
        "strict_no_peek": result.get("strict_no_peek"),
        "project_id": result.get("project_id"),
        "summary": result.get("summary"),
        "outputs": result.get("outputs"),
    }, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_verify_release(args: argparse.Namespace) -> int:
    manifest = verify_release(
        cwd=Path(args.cwd).resolve() if args.cwd else Path.cwd(),
        output=Path(args.out).resolve() if args.out else None,
        include_tests=not args.skip_full_tests,
    )
    print(json.dumps({
        "overall_status": manifest.get("overall_status"),
        "generated_at_utc": manifest.get("generated_at_utc"),
        "output": str(Path(args.out).resolve() if args.out else Path.cwd() / "PHASE91_RELEASE_MANIFEST.json"),
        "checks": [
            {"name": item.get("name"), "passed": item.get("passed"), "duration_seconds": item.get("duration_seconds")}
            for item in manifest.get("checks", [])
        ],
    }, ensure_ascii=False, indent=2))
    return 0 if manifest.get("overall_status") == "passed" else 1


def cmd_agent_loop(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else Path.cwd()
    report = build_agent_discovery_loop(
        args.project,
        root,
        {"max_next_actions": args.max_actions, "actor": "cli_agent_loop", "environment_id": args.environment},
    )
    print(json.dumps({
        "project_id": report.get("project_id"),
        "canonical_store": report.get("canonical_store"),
        "summary": report.get("summary"),
        "next_best_actions": report.get("next_best_actions"),
        "governance": report.get("governance"),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_agent_experiments(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else Path.cwd()
    options = {
        "max_experiments": args.max_experiments,
        "execute": bool(args.execute),
        "approved_sandbox_execution": bool(args.execute),
        "approval_id": str(args.approval_id or ""),
    }
    result = run_agent_experiment_pack(args.project, root, options) if args.execute else compile_agent_experiment_pack(args.project, root, options)
    print(json.dumps({
        "project_id": result.get("project_id"),
        "phase": result.get("phase"),
        "summary": result.get("summary") or (result.get("execution") or {}).get("summary"),
        "receipt_count": result.get("receipt_count"),
        "evidence_capture_count": result.get("evidence_capture_count"),
        "governance": result.get("governance"),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_agent_flows(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else Path.cwd()
    options = {
        "max_flows": args.max_flows,
        "execute": bool(args.execute),
        "approved_sandbox_execution": bool(args.execute),
        "approval_id": str(args.approval_id or ""),
        "environment_id": args.environment,
    }
    result = run_agent_business_flow_pack(args.project, root, options) if args.execute else compile_agent_business_flow_pack(args.project, root, options)
    print(json.dumps({
        "project_id": result.get("project_id"),
        "phase": result.get("phase"),
        "summary": result.get("summary") or (result.get("execution") or {}),
        "configured_flow_count": (result.get("summary") or {}).get("configured_flow_count"),
        "receipt_count": result.get("receipt_count"),
        "governance": result.get("governance"),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_export_knowledge_vault(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else Path.cwd()
    result = export_knowledge_vault(
        args.project,
        Path(args.out).resolve(),
        environment_id=args.environment,
        root=root,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_graph_stats(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else Path.cwd()
    graph = CognitiveMemoryGraph(args.project, args.environment, root)
    print(json.dumps(graph.stats(), ensure_ascii=False, indent=2))
    return 0



def cmd_graph_context_ab(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else Path.cwd()
    result = run_phase91_context_ab(
        args.project,
        root,
        environment_id=args.environment,
        output_path=Path(args.out).resolve() if args.out else None,
    )
    print(json.dumps({
        "project_id": result.get("project_id"),
        "environment_id": result.get("environment_id"),
        "mode": result.get("mode"),
        "output_path": result.get("output_path"),
        "evaluation": result.get("evaluation"),
    }, ensure_ascii=False, indent=2))
    return 0

def cmd_self_dogfood_audit(args: argparse.Namespace) -> int:
    report = run_self_dogfood_audit(
        Path(args.root).resolve() if args.root else None,
        mock_llm=not args.live_llm,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI Test Asset Center V8")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="Generate testing assets from requirement text")
    generate.add_argument("--requirement", required=True, help="Path to requirement markdown/text file")
    generate.add_argument("--out", required=True, help="Output directory")
    generate.add_argument("--engine", choices=["auto", "local", "llm"], default="auto", help="AI engine mode. default=auto")
    generate.set_defaults(func=cmd_generate)

    gen_api = subparsers.add_parser("generate-openapi", help="Generate API testing assets from OpenAPI/Swagger JSON")
    gen_api.add_argument("--spec", required=True, help="Path to OpenAPI/Swagger JSON file")
    gen_api.add_argument("--out", required=True, help="Output directory")
    gen_api.add_argument("--engine", choices=["auto", "local", "llm"], default="auto", help="Reserved for future LLM API enhancement. default=auto")
    gen_api.set_defaults(func=cmd_generate_openapi)


    impact = subparsers.add_parser("analyze-impact", help="Analyze Git diff and generate a minimal regression test set")
    impact.add_argument("--diff", required=True, help="Path to unified git diff file")
    impact.add_argument("--assets", required=True, help="Directory generated by generate-openapi")
    impact.add_argument("--out", required=True, help="Output directory for impact plan and generated regression pytest")
    impact.set_defaults(func=cmd_analyze_impact)


    triage = subparsers.add_parser("triage-failure", help="V5: enhanced failure triage from an evidence directory")
    triage.add_argument("--evidence", required=True, help="Evidence directory containing pytest_output.txt, api_response.json, trace_summary.json, etc.")
    triage.add_argument("--out", required=True, help="Output directory for triage report, bug draft and structured result")
    triage.add_argument("--engine", choices=["auto", "local", "llm"], default="auto", help="Failure triage engine. default=auto")
    triage.set_defaults(func=cmd_triage_failure)

    analyze = subparsers.add_parser("analyze-failure", help="Generate failure analysis report from log")
    analyze.add_argument("--log", required=True, help="Path to failure log")
    analyze.add_argument("--out", required=True, help="Output markdown report path")
    analyze.add_argument("--engine", choices=["auto", "local", "llm"], default="auto", help="Failure analysis engine. default=auto")
    analyze.set_defaults(func=cmd_analyze_failure)



    enterprise = subparsers.add_parser("enterprise-demo", help="V7: build enterprise-ready scorecard, ROI, governance and rollout package")
    enterprise.add_argument("--out", default="outputs/enterprise_ready", help="Output directory for enterprise landing package")
    enterprise.set_defaults(func=cmd_enterprise_demo)


    product = subparsers.add_parser("product-demo", help="V8: build product-ready package, personas, PRD, roadmap, metrics and packaging")
    product.add_argument("--out", default="outputs/product_ready", help="Output directory for product-ready package")
    product.set_defaults(func=cmd_product_demo)

    doctor = subparsers.add_parser("doctor", help="Check local and LLM configuration")
    doctor.set_defaults(func=cmd_doctor)

    discover = subparsers.add_parser("discover", help="Run autonomous bug discovery from PRD/API docs")
    discover.add_argument("--prd", required=True, help="Path to PRD/business requirement text")
    discover.add_argument("--api", required=True, help="Path to API/OpenAPI contract text")
    discover.add_argument("--base-url", default="http://127.0.0.1:8000/api", help="Target API base URL")
    discover.add_argument("--out", default="", help="Optional JSON output path")
    discover.set_defaults(func=cmd_discover)

    bug_auto = subparsers.add_parser("bug-engine-auto", help="Recover/start the local target and run the QualiBug bug engine automatically")
    bug_auto.add_argument("--project", default="real_project_demo", help="Project ID. default=real_project_demo")
    bug_auto.add_argument("--cycles", type=int, default=1, help="Number of autorun cycles. Use 0 for until interrupted. default=1")
    bug_auto.add_argument("--interval-seconds", type=float, default=60.0, help="Delay between cycles when cycles > 1 or cycles=0")
    bug_auto.add_argument("--local-bootstrap-only", action="store_true", default=True, help="Use deterministic read-only local bootstrap hypotheses")
    bug_auto.add_argument("--no-bootstrap-target", action="store_true", help="Do not auto-start bundled MES BugLab")
    bug_auto.add_argument("--no-reset-stale-runtime", action="store_true", help="Do not reconcile stale loop runtime state")
    bug_auto.add_argument("--graph-mode", choices=["off", "shadow", "active"], default="shadow", help="Cognitive graph context mode")
    bug_auto.add_argument("--out-dir", default="", help="Output directory for autorun reports")
    bug_auto.add_argument("--detach", action="store_true", help="Start in the background and return immediately")
    bug_auto.set_defaults(func=cmd_bug_engine_auto)

    bug_status = subparsers.add_parser("bug-engine-status", help="Show background bug-engine process and latest report status")
    bug_status.add_argument("--project", default="real_project_demo", help="Project ID. default=real_project_demo")
    bug_status.add_argument("--out-dir", default="", help="Output directory used by bug-engine-auto")
    bug_status.set_defaults(func=cmd_bug_engine_status)

    input_only = subparsers.add_parser("bug-engine-input-only", help="Run QualiBug from projects/<project>/input only; strict no oracle/ground-truth access")
    input_only.add_argument("--input-dir", required=True, help="Path to projects/<project>/input")
    input_only.add_argument("--project", default="", help="Output project ID. Defaults to parent project directory name")
    input_only.add_argument("--root", default="", help="Repository root. Defaults to current installed project root")
    input_only.add_argument("--base-url", default="", help="Optional live/disposable target base URL for grounded read-only probes")
    input_only.add_argument("--execute-readonly", action="store_true", help="Execute eligible GET/HEAD probes only; write probes remain blocked")
    input_only.add_argument("--probe-config", default="", help="Optional JSON config for test accounts/auth flow, sandbox fixtures, headers and path parameters")
    input_only.add_argument("--max-probes", type=int, default=0, help="Optional maximum grounded probes to plan/execute")
    input_only.set_defaults(func=cmd_bug_engine_input_only)

    bench_blind = subparsers.add_parser("bug-engine-benchmark-blind", help="Run input-only QualiBug over a benchmark suite without reading oracle/ground-truth files")
    bench_blind.add_argument("--suite-root", required=True, help="Path to benchmark suite root or its projects directory")
    bench_blind.add_argument("--root", default="", help="Repository/runtime root. Defaults to current installed project root")
    bench_blind.add_argument("--project-prefix", default="bench", help="Prefix for generated output project IDs")
    bench_blind.add_argument("--limit", type=int, default=0, help="Optional maximum number of projects to run")
    bench_blind.set_defaults(func=cmd_bug_engine_benchmark_blind)

    bench_v3_score = subparsers.add_parser("bug-engine-benchmark-v3-score", help="Score blind Benchmark Suite v3 outputs after generation without oracle leakage")
    bench_v3_score.add_argument("--suite-root", required=True, help="Path to Benchmark Suite v3 root")
    bench_v3_score.add_argument("--outputs-root", default="platform_outputs", help="Root containing qb_v3_* output folders")
    bench_v3_score.add_argument("--glob", default="qb_v3_*/input_only_run/grounded_candidates.json", help="Glob below outputs-root for grounded_candidates.json")
    bench_v3_score.add_argument("--out", default="platform_outputs/benchmark_suite_v3_score", help="Output directory for scorecard files")
    bench_v3_score.set_defaults(func=cmd_bug_engine_benchmark_v3_score)

    grounded_exec = subparsers.add_parser("bug-engine-grounded-execute", help="Execute grounded_probe_plan.json with strict safety gates")
    grounded_exec.add_argument("--probe-plan", required=True, help="Path to grounded_probe_plan.json")
    grounded_exec.add_argument("--out-dir", required=True, help="Directory for execution reports and repro assets")
    grounded_exec.add_argument("--base-url", default="", help="Live/disposable target base URL")
    grounded_exec.add_argument("--execute-readonly", action="store_true", help="Execute eligible GET/HEAD read-only probes")
    grounded_exec.add_argument("--probe-config", default="", help="Optional JSON config for test accounts/auth flow and test-environment settings")
    grounded_exec.add_argument("--input-dir", default="", help="Optional projects/<project>/input directory so QualiBug can auto-create test data from OpenAPI/docs")
    grounded_exec.add_argument("--allow-write-sandbox", action="store_true", help="Request write probe execution in a guarded test environment; QualiBug auto-creates qb_auto_* test data")
    grounded_exec.add_argument("--approval-id", default="", help="Optional test-environment approval/change-ticket id")
    grounded_exec.add_argument("--max-probes", type=int, default=0, help="Optional maximum probes")
    grounded_exec.add_argument("--timeout-seconds", type=float, default=10.0, help="HTTP timeout for read-only probes")
    grounded_exec.set_defaults(func=cmd_bug_engine_grounded_execute)

    probe_tpl = subparsers.add_parser("bug-engine-probe-config-template", help="Generate a non-executable probe_config.template.json from grounded_probe_plan.json")
    probe_tpl.add_argument("--probe-plan", required=True, help="Path to grounded_probe_plan.json")
    probe_tpl.add_argument("--out-dir", required=True, help="Directory for probe_config.template.json and template report")
    probe_tpl.add_argument("--input-dir", default="", help="Optional projects/<project>/input directory for OpenAPI schema hints; oracle files are skipped")
    probe_tpl.add_argument("--base-url-hint", default="", help="Optional sandbox base URL placeholder/hint")
    probe_tpl.add_argument("--approval-id-hint", default="", help="Optional sandbox approval id placeholder/hint")
    probe_tpl.add_argument("--max-probes", type=int, default=0, help="Optional maximum probes to include")
    probe_tpl.set_defaults(func=cmd_bug_engine_probe_config_template)

    self_evolve = subparsers.add_parser("self-evolve", help="Run one supervised QualiBug self-evolution worker")
    self_evolve.add_argument("--project", default="real_project_demo", help="Project ID. default=real_project_demo")
    self_evolve.add_argument("--local-bootstrap-only", action="store_true", help="Use deterministic read-only local bootstrap hypotheses without live LLM calls")
    self_evolve.add_argument("--graph-mode", choices=["off", "shadow", "active"], default="shadow", help="Cognitive graph context mode")
    self_evolve.add_argument("--max-evolution-cycles", type=int, default=1, help="Maximum candidate policy evolution cycles")
    self_evolve.add_argument("--out", default="", help="Optional JSON output path")
    self_evolve.set_defaults(func=cmd_self_evolve)

    verify = subparsers.add_parser("verify-release", help="Run release checks and write the Phase90 release manifest")
    verify.add_argument("--cwd", default="", help="Repository root. Defaults to the current working directory")
    verify.add_argument("--out", default="", help="Manifest output path. Defaults to PHASE90_RELEASE_MANIFEST.json")
    verify.add_argument("--skip-full-tests", action="store_true", help="Skip the full pytest suite for a faster local smoke verification")
    verify.set_defaults(func=cmd_verify_release)

    vault = subparsers.add_parser("export-knowledge-vault", help="Export the Phase91 cognitive graph as a read-only, redacted Markdown vault")
    vault.add_argument("--project", required=True, help="Project ID")
    vault.add_argument("--environment", default="test", help="Environment ID")
    vault.add_argument("--out", required=True, help="Directory for the read-only Markdown projection")
    vault.add_argument("--root", default="", help="Repository root. Defaults to current working directory")
    vault.set_defaults(func=cmd_export_knowledge_vault)

    graph_stats = subparsers.add_parser("graph-stats", help="Show Phase91 cognitive graph statistics")
    graph_stats.add_argument("--project", required=True, help="Project ID")
    graph_stats.add_argument("--environment", default="test", help="Environment ID")
    graph_stats.add_argument("--root", default="", help="Repository root. Defaults to current working directory")
    graph_stats.set_defaults(func=cmd_graph_stats)

    graph_ab = subparsers.add_parser("graph-context-ab", help="Measure Phase91 baseline document context vs typed graph context; stays shadow without replay metrics")
    graph_ab.add_argument("--project", required=True, help="Project ID")
    graph_ab.add_argument("--environment", default="test", help="Environment ID")
    graph_ab.add_argument("--root", default="", help="Repository root. Defaults to current working directory")
    graph_ab.add_argument("--out", default="", help="Optional JSON report output path")
    graph_ab.set_defaults(func=cmd_graph_context_ab)

    dogfood = subparsers.add_parser("self-dogfood-audit", help="Run QualiBug against its own private product flows")
    dogfood.add_argument("--root", default="", help="Optional isolated runtime root. Defaults to a temporary directory")
    dogfood.add_argument("--live-llm", action="store_true", help="Use the configured live LLM provider instead of a mocked health response")
    dogfood.set_defaults(func=cmd_self_dogfood_audit)

    loop = subparsers.add_parser("agent-loop", help="Refresh the persistent business Bug discovery ledger and plan next experiments")
    loop.add_argument("--project", required=True, help="Project ID with PRD/OpenAPI/API inputs")
    loop.add_argument("--root", default="", help="Repository root. Defaults to the current working directory")
    loop.add_argument("--max-actions", type=int, default=12, help="Maximum next-best actions to emit")
    loop.add_argument("--environment", default="test", help="Environment ID for graph planning")
    loop.set_defaults(func=cmd_agent_loop)

    experiments = subparsers.add_parser("agent-experiments", help="Compile or explicitly execute approved Agent Loop experiment packs")
    experiments.add_argument("--project", required=True, help="Project ID with a canonical Agent Loop ledger")
    experiments.add_argument("--root", default="", help="Repository root. Defaults to the current working directory")
    experiments.add_argument("--max-experiments", type=int, default=24, help="Maximum document-backed experiments to compile")
    experiments.add_argument("--execute", action="store_true", help="Delegate compiled experiments to the existing disposable-sandbox executor")
    experiments.add_argument("--approval-id", default="", help="Explicit disposable-sandbox approval ID. Required with --execute")
    experiments.set_defaults(func=cmd_agent_experiments)

    flows = subparsers.add_parser("agent-flows", help="Compile or explicitly execute approved multi-step Agent Loop business flows")
    flows.add_argument("--project", required=True, help="Project ID with a canonical Agent Loop ledger")
    flows.add_argument("--root", default="", help="Repository root. Defaults to the current working directory")
    flows.add_argument("--max-flows", type=int, default=12, help="Maximum explicitly mapped business flows to compile")
    flows.add_argument("--environment", default="test", help="Environment ID for graph context")
    flows.add_argument("--execute", action="store_true", help="Execute only explicitly mapped flows through the disposable-sandbox gate")
    flows.add_argument("--approval-id", default="", help="Explicit disposable-sandbox approval ID. Required with --execute")
    flows.set_defaults(func=cmd_agent_flows)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
