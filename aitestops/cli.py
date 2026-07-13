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
from aitestops.playwright_offline_bundle import (
    build_playwright_offline_bundle,
    install_playwright_offline_bundle,
    verify_playwright_offline_bundle,
)
from ai_test_asset_center.cognitive_memory_graph import CognitiveMemoryGraph, export_knowledge_vault


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
    """Reject the retired discovery side path with a stable migration receipt."""
    result = {
        "status": "DEPRECATED_COMMAND",
        "reason_code": "DISCOVERY_MAINLINE_REQUIRED",
        "independent_discovery_executed": False,
        "canonical_entrypoint": "ai_test_asset_center scan",
        "detail": (
            "aitestops discover was an independent finding/oracle path and is "
            "retired; use the source-bound scan API or private-pilot campaign "
            "entrypoint so one mainline authority, target policy, attempt ledger, "
            "and delivery gate govern the run"
        ),
    }
    if args.out:
        out = Path(args.out).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 2

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


def cmd_playwright_offline_build(args: argparse.Namespace) -> int:
    bundle = build_playwright_offline_bundle(
        out_dir=Path(args.out).resolve(),
        requirements_file=Path(args.requirements).resolve(),
        browsers=args.browsers.split(","),
    )
    print(json.dumps({"bundle_dir": str(bundle.root), "wheelhouse": str(bundle.wheelhouse), "browsers": str(bundle.browsers), "manifest": str(bundle.manifest)}, ensure_ascii=False, indent=2))
    return 0


def cmd_playwright_offline_install(args: argparse.Namespace) -> int:
    env_vars = install_playwright_offline_bundle(
        bundle_dir=Path(args.bundle).resolve(),
        requirements_file=Path(args.requirements).resolve(),
        browsers_path=Path(args.browsers_path).resolve() if args.browsers_path else None,
        env_out=Path(args.env_out).resolve() if args.env_out else None,
        venv_dir=Path(args.venv).resolve() if args.venv else None,
    )
    print(json.dumps({"installed": True, "env": env_vars, "note": "在 real_project_config.json 增加 playwright_browsers_path 或设置系统环境变量即可启用离线浏览器内核。"}, ensure_ascii=False, indent=2))
    return 0


def cmd_playwright_offline_verify(args: argparse.Namespace) -> int:
    report = verify_playwright_offline_bundle(
        bundle_dir=Path(args.bundle).resolve(),
        browsers_path=Path(args.browsers_path).resolve() if args.browsers_path else None,
        python=Path(args.python).resolve() if args.python else None,
        run_runtime_smoke=bool(args.runtime_smoke),
    )
    issues = report.get("issues") if isinstance(report, dict) else []
    ok = not issues
    print(json.dumps({"ok": ok, "report": report}, ensure_ascii=False, indent=2, default=str))
    return 0 if ok else 1

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

    discover = subparsers.add_parser("discover", help="Deprecated: use the source-bound mainline scan entrypoint")
    discover.add_argument("--prd", required=True, help="Path to PRD/business requirement text")
    discover.add_argument("--api", required=True, help="Path to API/OpenAPI contract text")
    discover.add_argument("--base-url", default="", help="Deprecated compatibility argument")
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

    pw_build = subparsers.add_parser("playwright-offline-build", help="Build a Playwright offline bundle (wheelhouse + browser cache)")
    pw_build.add_argument("--out", required=True, help="Output directory for offline bundle")
    pw_build.add_argument("--requirements", default="requirements-optional.txt", help="Requirements file to download into wheelhouse")
    pw_build.add_argument("--browsers", default="chromium,firefox", help="Comma-separated browsers to bundle")
    pw_build.set_defaults(func=cmd_playwright_offline_build)

    pw_install = subparsers.add_parser("playwright-offline-install", help="Install Playwright from a local offline bundle without networking")
    pw_install.add_argument("--bundle", required=True, help="Offline bundle directory")
    pw_install.add_argument("--requirements", default="requirements-optional.txt", help="Requirements file to install from wheelhouse")
    pw_install.add_argument("--browsers-path", default="", help="Optional extracted ms-playwright directory; defaults to bundle browsers/")
    pw_install.add_argument("--env-out", default="", help="Optional env file path to write PLAYWRIGHT_* variables (e.g., .env.local)")
    pw_install.add_argument("--venv", default="", help="Optional venv directory to install into (recommended for locked-down environments)")
    pw_install.set_defaults(func=cmd_playwright_offline_install)

    pw_verify = subparsers.add_parser("playwright-offline-verify", help="Verify a Playwright offline bundle and optional runtime smoke")
    pw_verify.add_argument("--bundle", required=True, help="Offline bundle directory")
    pw_verify.add_argument("--browsers-path", default="", help="Optional extracted ms-playwright directory; defaults to bundle browsers/")
    pw_verify.add_argument("--python", default="", help="Optional python to run runtime smoke (e.g., venv python)")
    pw_verify.add_argument("--runtime-smoke", action="store_true", help="Try import + headless chromium/firefox launch to verify OS deps")
    pw_verify.set_defaults(func=cmd_playwright_offline_verify)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
