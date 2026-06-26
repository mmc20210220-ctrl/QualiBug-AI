from __future__ import annotations

"""Input-only project runner for benchmark / enterprise documents.

This is the strict path for the workflow:

    projects/<project>/input/  ->  QualiBug planning / discovery outputs

It deliberately refuses to read oracle, ground-truth, answer, seed or BUG_MATRIX
files.  It does not use demo/local BugLab bootstrap and it does not classify a
finding as a runtime-confirmed bug when no live target is configured.
"""

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

import yaml

from .input_grounded_candidate_compiler import write_grounded_candidate_outputs
from .grounded_probe_executor import run_grounded_probe_executor
from .project_context_compiler import ProjectContextCompiler
from .real_project_defect_discovery import run_real_project_discovery
from .real_project_onboarding import ROOT, _safe_project_id, run_onboarding_check

FORBIDDEN_TOKENS = {
    "oracle",
    "ground_truth",
    "bug_ground_truth",
    "all_bugs",
    "answer",
    "answers",
    "solution",
    "solutions",
    "seed",
    "seeds",
    "enabled_bugs",
    "bug_matrix",
}

DOC_ORDER = [
    "PRD.md",
    "BUSINESS_RULES.md",
    "DATABASE_DESIGN.md",
    "TEST_SCENARIOS.md",
    "RISK_SURFACE_MODEL.md",
]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_forbidden(path: Path) -> bool:
    text = "/".join(part.lower() for part in path.parts)
    return any(token in text for token in FORBIDDEN_TOKENS)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _load_openapi_yaml_or_json(input_dir: Path) -> tuple[dict[str, Any], str]:
    for name in ("openapi.json", "swagger.json"):
        p = input_dir / name
        if p.exists():
            return json.loads(_read(p) or "{}"), name
    for name in ("openapi.yaml", "openapi.yml", "swagger.yaml", "swagger.yml"):
        p = input_dir / name
        if p.exists():
            return yaml.safe_load(_read(p) or "{}") or {}, name
    return {}, ""


def _copy_input_only(source_input_dir: Path, dest_input_dir: Path) -> dict[str, Any]:
    source_input_dir = source_input_dir.resolve()
    if source_input_dir.name.lower() != "input":
        raise ValueError(f"source must be a projects/<project>/input directory, got: {source_input_dir}")
    if _is_forbidden(source_input_dir):
        raise ValueError(f"refusing forbidden source path: {source_input_dir}")

    dest_input_dir.mkdir(parents=True, exist_ok=True)
    allowed: list[dict[str, Any]] = []
    blocked: list[str] = []

    for path in sorted(source_input_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(source_input_dir)
        if _is_forbidden(rel) or _is_forbidden(path.relative_to(source_input_dir.parent)):
            blocked.append(str(rel).replace("\\", "/"))
            continue
        if path.stat().st_size > 8_000_000:
            blocked.append(str(rel).replace("\\", "/") + "#too_large")
            continue
        out = dest_input_dir / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, out)
        allowed.append({
            "file": str(rel).replace("\\", "/"),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
        })
    return {"allowed_input_files": allowed, "blocked_files": blocked}


def _normalize_platform_inputs(project_id: str, source_input_dir: Path, root: Path) -> dict[str, Any]:
    project = _safe_project_id(project_id)
    dest = root / "platform_inputs" / project
    if dest.exists():
        shutil.rmtree(dest)
    manifest = _copy_input_only(source_input_dir, dest)

    merged_docs: list[str] = []
    for name in DOC_ORDER:
        p = dest / name
        if p.exists():
            merged_docs.append(f"\n\n# Source: {name}\n" + _read(p))
    # Include any other markdown input document except API.md, which stays as api docs.
    for p in sorted(dest.glob("*.md")):
        if p.name in set(DOC_ORDER) | {"API.md", "api.md", "prd.md"}:
            continue
        merged_docs.append(f"\n\n# Source: {p.name}\n" + _read(p))
    (dest / "prd.md").write_text("\n".join(merged_docs), encoding="utf-8")

    api_docs = _read(dest / "API.md") or _read(dest / "api.md")
    if api_docs:
        (dest / "api.md").write_text(api_docs, encoding="utf-8")

    openapi, openapi_source = _load_openapi_yaml_or_json(dest)
    if openapi:
        (dest / "openapi.json").write_text(json.dumps(openapi, ensure_ascii=False, indent=2), encoding="utf-8")

    cfg = {
        "project_id": project,
        "project_name": source_input_dir.parent.name,
        "base_url": os.environ.get("QUALIBUG_TARGET_BASE_URL", ""),
        "openapi_source": "json",
        "discovery_mode": "safe",
        "safe_mode": True,
        "allow_destructive_tests": False,
        "request_timeout_seconds": 10,
        "max_probe_count": int(os.environ.get("QUALIBUG_MAX_PROBE_COUNT", "160") or 160),
        "input_only_mode": True,
        "forbidden_sources": sorted(FORBIDDEN_TOKENS),
    }
    (dest / "real_project_config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest.update({
        "project_id": project,
        "source_input_dir": str(source_input_dir),
        "platform_input_dir": str(dest),
        "openapi_source": openapi_source,
        "openapi_path_count": len((openapi or {}).get("paths") or {}) if isinstance(openapi, dict) else 0,
        "leak_guard": "STRICT_INPUT_ONLY_NO_ORACLE_NO_GROUND_TRUTH_NO_BUG_MATRIX",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    (dest / "blind_input_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _compile_project_context(project_id: str, root: Path) -> dict[str, Any]:
    project = _safe_project_id(project_id)
    input_dir = root / "platform_inputs" / project
    prd = _read(input_dir / "prd.md")
    api_docs = _read(input_dir / "api.md")
    openapi = json.loads(_read(input_dir / "openapi.json") or "{}")
    compiler = ProjectContextCompiler()
    ctx = compiler.compile(prd_text=prd, openapi_spec=openapi, api_docs_text=api_docs)
    payload = compiler.to_dict(ctx)
    out = root / "platform_outputs" / project / "input_only_project_context.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "project_context": str(out),
        "entity_count": len(payload.get("entities") or []),
        "api_count": len(payload.get("apis") or []),
        "observer_count": len(payload.get("observers") or []),
        "candidate_invariant_count": len(payload.get("candidate_invariants") or []),
        "candidate_lifecycle_count": len(payload.get("candidate_lifecycle_transitions") or []),
    }


def _summarize_discovery(data: dict[str, Any]) -> dict[str, Any]:
    metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
    issues = data.get("issues") if isinstance(data.get("issues"), list) else []
    return {
        "issue_count": int(metrics.get("issue_count") or len(issues)),
        "high_confidence_issues": int(metrics.get("high_confidence_issues") or 0),
        "suggested_release_blockers": int(metrics.get("suggested_release_blockers") or 0),
        "needs_human_review": int(metrics.get("needs_human_review") or len(issues)),
        "confirmed_runtime_bugs": sum(1 for item in issues if str(item.get("status") or "").lower() in {"confirmed", "validated", "validated_candidate"}),
        "risk_types": sorted({str(item.get("risk_type") or "unknown") for item in issues if isinstance(item, dict)})[:50],
    }


def _summarize_grounded_candidates(data: dict[str, Any]) -> dict[str, Any]:
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    candidates = data.get("candidates") if isinstance(data.get("candidates"), list) else []
    return {
        "issue_count": int(summary.get("candidate_count") or len(candidates)),
        "high_confidence_issues": sum(1 for item in candidates if float(item.get("confidence") or 0) >= 0.7),
        "suggested_release_blockers": sum(1 for item in candidates if str(item.get("severity") or "").upper() in {"P0", "P1"}),
        "needs_human_review": int(summary.get("needs_human_review") or len(candidates)),
        "confirmed_runtime_bugs": 0,
        "risk_types": sorted((summary.get("by_risk_type") or {}).keys()),
        "by_execution_policy": summary.get("by_execution_policy") or {},
        "candidate_mode": "document_grounded_input_only",
    }


def run_input_only_project(
    *,
    project_input_dir: str | Path,
    project_id: str | None = None,
    root: str | Path | None = None,
    base_url: str = "",
    execute_readonly: bool = False,
    probe_config: str | Path | None = None,
    max_probes: int = 0,
) -> dict[str, Any]:
    """Run the enterprise document-driven engine using only input/ files."""
    root_path = Path(root or ROOT).resolve()
    input_dir = Path(project_input_dir).resolve()
    project = _safe_project_id(project_id or input_dir.parent.name)
    manifest = _normalize_platform_inputs(project, input_dir, root_path)
    context_summary = _compile_project_context(project, root_path)
    onboarding = run_onboarding_check(project, root_path)

    output_dir = root_path / "platform_outputs" / project / "input_only_run"
    grounded = write_grounded_candidate_outputs(
        root_path / "platform_inputs" / project,
        output_dir,
        project_id=project,
    )

    probe_execution: dict[str, Any] | None = None
    # Optional runtime bridge: execute only safe read-only probes generated from
    # the input documents.  Without a base URL this remains a dry-run artifact.
    if base_url or execute_readonly or os.environ.get("QUALIBUG_TARGET_BASE_URL"):
        probe_execution = run_grounded_probe_executor(
            probe_plan_path=output_dir / "grounded_probe_plan.json",
            out_dir=output_dir,
            base_url=base_url,
            probe_config=probe_config,
            execute_readonly=execute_readonly,
            max_probes=max_probes,
            input_dir=input_dir,
        )

    # The old broad discovery engine can be run in explicit shadow mode for
    # regression comparison, but input-only must default to the document-grounded
    # compiler to avoid generic industry/static template noise.
    legacy_shadow_enabled = os.environ.get("QUALIBUG_INPUT_ONLY_LEGACY_SHADOW", "0") == "1"
    if legacy_shadow_enabled:
        discovery = run_real_project_discovery(project, root_path)
    else:
        discovery = {"metrics": {"issue_count": 0}, "items": []}
    discovery_summary = _summarize_grounded_candidates(grounded)
    if probe_execution:
        execution_summary = probe_execution.get("summary") or {}
        discovery_summary["confirmed_runtime_bugs"] = int(execution_summary.get("validated_candidate_count") or 0)
        discovery_summary["protected_runtime_candidates"] = int(execution_summary.get("protected_count") or 0)
        discovery_summary["runtime_evidence_ready"] = int(execution_summary.get("validated_candidate_count") or 0) > 0
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "mode": "input_only_enterprise_docs",
        "project_id": project,
        "project_name": input_dir.parent.name,
        "strict_no_peek": True,
        "allowed_source_root": str(input_dir),
        "forbidden_sources": sorted(FORBIDDEN_TOKENS),
        "input_manifest": manifest,
        "project_context_summary": context_summary,
        "onboarding_ok": bool(onboarding.get("ok")),
        "discovery_summary": discovery_summary,
        "grounded_candidate_summary": grounded.get("summary"),
        "grounded_probe_execution_summary": (probe_execution or {}).get("summary"),
        "legacy_static_shadow_enabled": legacy_shadow_enabled,
        "legacy_static_shadow_summary": _summarize_discovery(discovery),
        "outputs": {
            "platform_input_dir": str(root_path / "platform_inputs" / project),
            "project_context": context_summary.get("project_context"),
            "grounded_candidates": str(output_dir / "grounded_candidates.json"),
            "grounded_candidates_md": str(output_dir / "grounded_candidates.md"),
            "grounded_probe_plan": str(output_dir / "grounded_probe_plan.json"),
            "runtime_validation_queue": str(output_dir / "runtime_validation_queue.json"),
            "runtime_validation_queue_md": str(output_dir / "runtime_validation_queue.md"),
            "grounded_probe_execution_report": str(output_dir / "grounded_probe_execution_report.json") if probe_execution else "",
            "grounded_probe_execution_report_md": str(output_dir / "grounded_probe_execution_report.md") if probe_execution else "",
            "grounded_probe_repro_ps1": str(output_dir / "grounded_probe_repro.ps1") if probe_execution else "",
            "grounded_probe_regression_pytest": str(output_dir / "grounded_probe_regression_pytest.py") if probe_execution else "",
            "legacy_shadow_report": str(root_path / "platform_outputs" / project / "real_project" / "real_project_defect_report.html") if legacy_shadow_enabled else "",
            "legacy_shadow_discovered_issues": str(root_path / "platform_outputs" / project / "real_project" / "discovered_issues.json") if legacy_shadow_enabled else "",
        },
        "note": "No oracle/ground_truth/BUG_MATRIX files were read. Without a configured live target, output is document-derived candidates, not runtime-confirmed bugs.",
    }
    (output_dir / "blind_input_run_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "blind_input_run_report.md").write_text(render_input_only_report(report), encoding="utf-8")
    report["outputs"]["blind_input_run_report"] = str(output_dir / "blind_input_run_report.json")
    report["outputs"]["blind_input_run_report_md"] = str(output_dir / "blind_input_run_report.md")
    return report


def render_input_only_report(report: dict[str, Any]) -> str:
    manifest = report.get("input_manifest") or {}
    ctx = report.get("project_context_summary") or {}
    ds = report.get("discovery_summary") or {}
    files = "\n".join(f"- `{item.get('file')}` sha256={item.get('sha256','')[:12]}" for item in manifest.get("allowed_input_files") or [])
    risks = ", ".join(ds.get("risk_types") or [])
    return f"""# Input-only QualiBug Run — {report.get('project_name')}

## Guardrail

- strict_no_peek: `{report.get('strict_no_peek')}`
- allowed source root: `{report.get('allowed_source_root')}`
- leak guard: `{manifest.get('leak_guard')}`
- blocked files: `{len(manifest.get('blocked_files') or [])}`

## Input files used

{files or '- none'}

## Compiled project context

- entities: {ctx.get('entity_count')}
- APIs: {ctx.get('api_count')}
- observers: {ctx.get('observer_count')}
- candidate invariants: {ctx.get('candidate_invariant_count')}
- lifecycle candidates: {ctx.get('candidate_lifecycle_count')}

## Discovery output

- issue candidates: {ds.get('issue_count')}
- high confidence candidates: {ds.get('high_confidence_issues')}
- needs human review: {ds.get('needs_human_review')}
- runtime confirmed bugs: {ds.get('confirmed_runtime_bugs')}
- suggested release blockers: {ds.get('suggested_release_blockers')}
- risk types: {risks or 'none'}

> Without a configured live target, QualiBug does not label these as runtime-confirmed bugs. They are document-derived business-risk candidates and executable probe plans.
"""
