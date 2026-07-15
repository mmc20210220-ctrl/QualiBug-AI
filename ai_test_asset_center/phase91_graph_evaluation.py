"""Phase91 measured Graph Context A/B harness.

This harness is intentionally conservative: it measures the local artifacts it
actually builds and does not invent LLM latency, false-positive rates or quality
claims.  Graph context stays in shadow unless external replay/shadow metrics are
provided and satisfy the promotion gates.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .cognitive_memory_graph import CognitiveMemoryGraph, GraphContextComposer, Phase91ABEvaluator, RiskFrontierPlanner
from .project_context_compiler import ProjectContextCompiler
from .real_project_onboarding import config_paths, _safe_project_id


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _openapi(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def _target_from_frontier(frontier: list[dict[str, Any]]) -> dict[str, Any]:
    for row in frontier:
        if row.get("execution_allowed"):
            return dict(row.get("target") or {})
    return {"api": "", "risk_type": "unknown"}


def run_phase91_context_ab(
    project_id: str = "real_project_demo",
    root: str | Path | None = None,
    *,
    environment_id: str = "test",
    observed_baseline: dict[str, Any] | None = None,
    observed_challenger: dict[str, Any] | None = None,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build a real local baseline/graph comparison report.

    The caller may pass measured replay/shadow metrics.  Without them the report
    is explicitly shadow-only; no quality or latency claim is manufactured.
    """
    root_path = Path(root or ".").resolve()
    project = _safe_project_id(project_id)
    paths = config_paths(project, root_path)
    input_dir = paths["input_dir"]
    prd_path = input_dir / "prd.md"
    openapi_path = input_dir / "openapi.json"
    prd = _read(prd_path)
    api_text = _read(openapi_path)
    docs = "\n\n".join(_read(path) for path in sorted(input_dir.glob("*.md")) if path.name.lower() not in {"prd.md"})
    started = time.monotonic()

    graph = CognitiveMemoryGraph(project, environment_id, root_path)
    compiler = ProjectContextCompiler()
    context = compiler.compile(prd, _openapi(api_text), api_docs_text=docs)
    synced = graph.sync_context(
        compiler.to_dict(context),
        artifact={"phase": "phase91_ab", "source_hash": "local-input-measurement"},
        prd_source_ref=str(prd_path.relative_to(root_path)) if prd_path.exists() else "",
        api_source_ref=str(openapi_path.relative_to(root_path)) if openapi_path.exists() else "",
        run_id="phase91-ab",
    )
    frontier = RiskFrontierPlanner(graph).rank(limit=20)
    target = _target_from_frontier(frontier)
    graph_pack = GraphContextComposer(graph).compose(target, high_risk_write=False)

    # Baseline represents the actual documents that legacy Reader/Reasoner must
    # consider. It is not sent to an LLM by this evaluation harness.
    baseline_prompt = "\n\n".join(part for part in (prd, api_text, docs) if part)
    baseline_metrics = dict(observed_baseline or {})
    challenger_metrics = dict(observed_challenger or {})
    # Safety count is locally observable and must stay zero. Other quality
    # dimensions remain unknown until a fixed replay/shadow dataset records them.
    baseline_metrics.setdefault("production_http_requests", 0)
    challenger_metrics.setdefault("production_http_requests", 0)
    evaluator = Phase91ABEvaluator()
    evaluation = evaluator.evaluate(
        baseline_prompt=baseline_prompt,
        graph_pack=graph_pack,
        baseline_metrics=baseline_metrics,
        challenger_metrics=challenger_metrics,
    )
    report = {
        "phase": "phase91_cognitive_memory_graph",
        "project_id": project,
        "environment_id": environment_id,
        "mode": evaluation["promotion"],
        "measurement": {
            "baseline_source": "current project PRD/OpenAPI/API docs",
            "challenger_source": "typed SQLite graph local neighborhood",
            "live_llm_called": False,
            "real_metrics_only": True,
            "missing_quality_metrics_keep_graph_in_shadow": not evaluation["quality_known"],
            "elapsed_seconds": round(time.monotonic() - started, 6),
        },
        "graph_sync": synced,
        "graph_stats": graph.stats(),
        "selected_frontier": next((row for row in frontier if row.get("target") == target), {}),
        "evidence_pack": {
            "target": graph_pack.get("target"),
            "context_refs": graph_pack.get("context_refs"),
            "rendered_char_count": len(str(graph_pack.get("rendered_context") or "")),
            "traceable": bool(graph_pack.get("context_refs")),
        },
        "evaluation": evaluation,
        "promotion_guard": {
            "active_requires_external_replay_or_shadow_metrics": True,
            "production_http_requests": challenger_metrics.get("production_http_requests", 0),
            "safety_violation": challenger_metrics.get("safety_violations", "not_measured"),
            "cleanup_failures": challenger_metrics.get("cleanup_failures", "not_measured"),
        },
    }
    destination = Path(output_path) if output_path else root_path / "platform_outputs" / project / "phase91" / "PHASE91_CONTEXT_AB_REPORT.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    report["output_path"] = str(destination)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


__all__ = ["run_phase91_context_ab"]
